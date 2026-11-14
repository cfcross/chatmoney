"""Single egress for both the graph and the human desk. Only the local mock adapter exists."""

from datetime import timedelta
from sqlalchemy import select, func
from app import schema as s
from app.catalog import version
from app.chat import append
from app.db import insert, now, row, uid, update
from app.settings import settings
from app.work import emit
from app.workflows import entities


def unresolved(c, t, cv, exclude=None):
    q = select(s.sends.c.id).where(
        s.sends.c.tenant == t,
        s.sends.c.conversation_id == cv.id,
        s.sends.c.status.in_(["dispatching", "unknown"]),
    )
    if exclude:
        q = q.where(s.sends.c.id != exclude)
    return c.scalar(q.limit(1))


def barrier(c, t, r, actor, epoch, sources, exclude=None):
    se, task, a, cv = entities(c, t, r)
    acc = row(c, s.accounts, t, cv.account_id)
    if cv.active_session != se.id or cv.epoch != epoch or cv.owner != actor:
        return "owner_changed"
    if not acc.enabled or "channel.reply" not in acc.capabilities:
        return "account_denied"
    if (
        not cv.permitted
        or cv.opted_out
        or (cv.window_until and cv.window_until <= now())
    ):
        return "contact_denied"
    if unresolved(c, t, cv, exclude):
        return "send_unresolved"
    if task.status in {"cancelled", "completed", "failed"} or se.status == "ended":
        return "closed"
    if actor == "auto":
        cfg = version(c, t, a.id, task.agent_version).config
        if (
            a.disabled
            or task.status not in {"running", "waiting"}
            or se.status not in {"running", "waiting"}
        ):
            return "paused"
        if "channel.reply" not in cfg["tool_allowlist"]:
            return "tool_forbidden"
        for source in sources:
            if source.startswith("tool:"):
                continue
            if source.startswith("m_"):
                if row(c, s.messages, t, source).session_id != se.id:
                    return "memory_source_denied"
                continue
            if row(c, s.docs, t, source).status != "ready":
                return "knowledge_withdrawn"
    count = c.scalar(
        select(func.count())
        .select_from(s.sends)
        .where(
            s.sends.c.tenant == t,
            s.sends.c.conversation_id == cv.id,
            s.sends.c.created > now() - timedelta(hours=1),
            s.sends.c.status == "accepted",
        )
    )
    return "frequency_limit" if count >= settings().rate_per_hour else None


def current(c, t, rid):
    return (
        c.execute(select(s.sends).where(s.sends.c.tenant == t, s.sends.c.run_id == rid))
        .mappings()
        .first()
    )


def output(c, t, v):
    m = row(c, s.messages, t, v.message_id)
    return dict(
        action="reply"
        if v.status in {"accepted", "unknown", "dispatching"}
        else "draft",
        text=m.body,
        message_id=m.id,
        send_id=v.id,
        delivery=v.status,
        source_refs=v.sources,
    )


def set_status(c, t, r, v, status, receipt=None):
    update(c, s.sends, t, v.id, status=status, receipt=receipt)
    emit(
        c,
        t,
        r.product_id,
        "delivery.updated",
        sid=r.session_id,
        task=r.task_id,
        run=r.id,
        data={"send_id": v.id, "status": status},
    )


class MockChannel:
    """The simulated platform has its own receipt record. A worker can die before copying it back."""

    def accept(self, c, t, v):
        receipt = "mock:" + v.id
        insert(c, s.receipts, t, uid("receipt"), send_id=v.id, reference=receipt)
        return receipt


def reconcile(c, t, v):
    if v.status not in {"unknown", "dispatching"}:
        return v.status
    receipt = c.scalar(
        select(s.receipts.c.reference).where(
            s.receipts.c.tenant == t, s.receipts.c.send_id == v.id
        )
    )
    state = "accepted" if receipt else "unknown"
    if state != v.status:
        set_status(c, t, row(c, s.runs, t, v.run_id), v, state, receipt)
    return state


def create_intent(c, t, r, se, cv, actor, epoch, mid, sources):
    id = insert(
        c,
        s.sends,
        t,
        uid("send"),
        conversation_id=cv.id,
        session_id=se.id,
        message_id=mid,
        run_id=r.id,
        actor=actor,
        epoch=epoch,
        status="pending",
        sources=sources,
    )
    emit(
        c,
        t,
        r.product_id,
        "delivery.updated",
        sid=se.id,
        task=se.task_id,
        run=r.id,
        data={"send_id": id, "status": "pending"},
    )
    return id


class Sender:
    def __init__(self, adapter=None, after_intent=None, after_accept=None):
        self.adapter = adapter or MockChannel()
        self.after_intent, self.after_accept = after_intent, after_accept

    def __call__(self, ex, text, sources):
        t = ex.tenant
        with ex.tx() as (c, r):
            se, task, a, cv = entities(c, t, r)
            ledger = current(c, t, r.id)
            if not ledger:
                denied = barrier(c, t, r, "auto", r.payload["epoch"], sources)
                mid = append(
                    c, t, se, cv, "draft" if denied else "outbound", text, run_id=r.id
                )
                if denied:
                    return dict(
                        action="draft",
                        reason=denied,
                        text=text,
                        message_id=mid,
                        delivery="not_sent",
                        source_refs=sources,
                    )
                create_intent(c, t, r, se, cv, "auto", r.payload["epoch"], mid, sources)
        if self.after_intent:
            self.after_intent()
        return self.dispatch(ex)

    def dispatch(self, ex):
        t = ex.tenant
        with ex.tx() as (c, r):
            v = current(c, t, r.id)
            if v.status != "pending":
                reconcile(c, t, v)
                return output(c, t, current(c, t, r.id))
            reason = barrier(c, t, r, v.actor, v.epoch, v.sources)
            if reason:
                set_status(c, t, r, v, "cancelled")
                return output(c, t, current(c, t, r.id)) | {"reason": reason}
            set_status(c, t, r, v, "dispatching")
        # The authority lock spans the final check AND acceptance. No model or network call happens inside it.
        with ex.tx() as (c, r):
            v = current(c, t, r.id)
            reason = barrier(c, t, r, v.actor, v.epoch, v.sources, exclude=v.id)
            if reason:
                set_status(c, t, r, v, "cancelled")
                return output(c, t, current(c, t, r.id)) | {"reason": reason}
            try:
                self.adapter.accept(c, t, v)
            except TimeoutError:
                set_status(c, t, r, v, "unknown")
                return output(c, t, current(c, t, r.id))
        if self.after_accept:
            self.after_accept()
        with ex.tx() as (c, r):
            v = current(c, t, r.id)
            reconcile(c, t, v)
            return output(c, t, current(c, t, r.id))
