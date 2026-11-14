"""PostgreSQL-backed worker. One claimant per session; sleeping timers consume no worker."""

import argparse
from datetime import timedelta
import logging
import time
from sqlalchemy import select, or_, and_, exists, text
from app import schema as s, knowledge, workflows
from app.channels import Sender, reconcile
from app.db import engine, now, lock, row, transaction, uid, update
from app.execution import Execution, LostLease
from app.llm import ModelError
from app.llm.deepseek import ERROR_MESSAGES
from app.tools.gateway import Gateway
from app.work import change_state, finish

log = logging.getLogger("chatmoney.worker")
LIVE = {"queued", "running", "retry_wait"}


def claim(tenant, queues):
    t = tenant
    with transaction(t) as c:
        lock(c, "mutation", t)
        j = s.runs
        prior = j.alias("prior")
        # A timer scheduled for tomorrow cannot block today's inbound message.
        before = exists(
            select(1).where(
                prior.c.tenant == t,
                prior.c.session_id == j.c.session_id,
                prior.c.id != j.c.id,
                prior.c.status.in_(LIVE),
                or_(
                    prior.c.status == "running",
                    and_(
                        prior.c.ordinal < j.c.ordinal,
                        or_(prior.c.due <= now(), prior.c.kind == "inbound"),
                    ),
                ),
            )
        )
        eligible = or_(
            and_(
                j.c.status.in_(["queued", "retry_wait"]),
                j.c.due <= now(),
                or_(j.c.session_id.is_(None), ~before),
            ),
            and_(j.c.status == "running", j.c.lease_until <= now()),
        )
        query = (
            select(j)
            .where(j.c.tenant == t, j.c.queue.in_(queues), eligible)
            .order_by(j.c.due, j.c.ordinal)
            .limit(8)
            .with_for_update(skip_locked=True)
        )
        for r in c.execute(query).mappings():
            lease = uid("lease")
            update(
                c,
                j,
                t,
                r.id,
                status="running",
                attempt=r.attempt + 1,
                lease_token=lease,
                lease_until=now() + timedelta(seconds=120),
            )
            return r.id, lease, r.kind, r.session_id
    return None


def settle(c, t, r, result, error=None):
    finish(c, t, r, result, error=error)
    if r.kind == "index" and error:
        c.execute(
            s.docs.update()
            .where(
                s.docs.c.tenant == t,
                s.docs.c.id == r.payload["document_id"],
                s.docs.c.status == "processing",
            )
            .values(status="failed")
        )
    if not r.task_id:
        return
    task = row(c, s.tasks, t, r.task_id)
    if r.session_id:
        se = row(c, s.sessions, t, r.session_id)
        if r.kind == "inbound" and result and result.get("action") != "suppressed":
            update(c, s.messages, t, r.payload["message_id"], handled=True)
        if r.kind == "human_send" and result and result.get("delivery") == "accepted":
            m = row(c, s.messages, t, result["message_id"])
            c.execute(
                s.messages.update()
                .where(
                    s.messages.c.tenant == t,
                    s.messages.c.session_id == se.id,
                    s.messages.c.kind == "inbound",
                    s.messages.c.seq < m.seq,
                )
                .values(handled=True)
            )
        if se.status == "running":
            change_state(c, t, se, task, r.product_id, "error" if error else "waiting")
    outstanding = c.scalar(
        select(s.runs.c.id)
        .where(
            s.runs.c.tenant == t,
            s.runs.c.task_id == task.id,
            s.runs.c.status.in_(LIVE),
            s.runs.c.due <= now(),
        )
        .limit(1)
    )
    if not outstanding and task.status == "running":
        update(c, s.tasks, t, task.id, status="waiting")


def process(t, claimed, model=None, sender=None):
    id, lease, kind, sid = claimed
    ex = Execution(t, id, lease, model, Gateway(), sender or Sender())
    # Session-level advisory lock spans the graph, including checkpoints on a separate connection.
    # Concurrent claims never enter the same graph; crashes release the DB connection lock.
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as guard:
        key = f"graph:{t}:{sid or id}"
        guard.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key,0))"), {"key": key}
        )
        try:
            with ex.tx() as (c, r):
                if r.attempt > 4:
                    settle(
                        c,
                        t,
                        r,
                        None,
                        dict(
                            code="RETRY_EXHAUSTED",
                            message="执行超过重试上限",
                            retryable=False,
                        ),
                    )
                    return
                if kind in {"started", "resumed"}:
                    settle(c, t, r, {"action": kind, "task_id": r.task_id})
                    return
                if kind == "index":
                    settle(c, t, r, knowledge.index(c, t, r.payload["document_id"]))
                    return
                if sid:
                    task = row(c, s.tasks, t, r.task_id)
                    if task.status == "waiting":
                        update(c, s.tasks, t, task.id, status="running")
            if kind == "release_graph":
                result = workflows.drive(ex, sid, release=True)
            elif kind == "human_send":
                result = ex.sender.dispatch(ex)
            else:
                result = workflows.drive(ex, sid)
            with ex.tx() as (c, r):
                settle(c, t, r, result)
        except LostLease:
            return
        except Exception as err:
            code = err.code if isinstance(err, ModelError) else "EXECUTION_ERROR"
            log.warning("run=%s failure=%s type=%s", id, code, type(err).__name__)
            try:
                with ex.tx() as (c, r):
                    unresolved_send = (
                        c.execute(
                            select(s.sends).where(
                                s.sends.c.tenant == t, s.sends.c.run_id == r.id
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if unresolved_send and unresolved_send.status in {
                        "dispatching",
                        "unknown",
                    }:
                        reconcile(c, t, unresolved_send)
                    if code == "EXECUTION_ERROR" and r.attempt < 3:
                        update(
                            c,
                            s.runs,
                            t,
                            id,
                            status="retry_wait",
                            lease_token=None,
                            lease_until=None,
                            due=now() + timedelta(seconds=r.attempt),
                        )
                    else:
                        settle(
                            c,
                            t,
                            r,
                            None,
                            dict(
                                code=code,
                                message=ERROR_MESSAGES.get(
                                    code, "本轮未能完成，请查看执行记录"
                                ),
                                retryable=False,
                            ),
                        )
            except LostLease:
                pass
        finally:
            guard.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                {"key": key},
            )


class Worker:
    def __init__(self, queues=("support", "sales", "system"), model=None, sender=None):
        self.queues, self.model, self.sender = list(queues), model, sender
        self.offset = 0

    def tick(self):
        with engine().connect() as c:
            tenants = c.scalars(text("SELECT * FROM public.worker_tenants()")).all()
        # Rotate tenant and queue priority for independently scheduled business lanes.
        lanes = [(t, q) for q in self.queues for t in tenants]
        for i in range(len(lanes)):
            t, q = lanes[(self.offset + i) % len(lanes)]
            job = claim(t, [q])
            if job:
                self.offset = (self.offset + i + 1) % len(lanes)
                process(t, job, self.model, self.sender)
                return True
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queues", default="support,sales,system")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = Worker(args.queues.split(","))
    logging.basicConfig(level=logging.WARNING)
    print("ChatMoney worker ready", flush=True)
    from app.notifications import deliver_events

    while True:
        try:
            active = worker.tick()
            deliver_events()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            log.error("worker poll failed: %s", type(exc).__name__)
            active = False
        if args.once:
            return
        if not active:
            time.sleep(0.25)


if __name__ == "__main__":
    main()
