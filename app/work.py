"""Transactional outbox and work table. Workers take leases only for due work."""

from sqlalchemy import select, func
from app import schema as s
from app.db import insert, lock, now, uid, update


def emit(c, t, product, event, *, sid=None, task=None, run=None, data=None):
    # Assign the sequence under a tenant transaction lock so commits cannot overtake an SSE cursor.
    lock(c, "events", t)
    seq = c.scalar(
        select(func.coalesce(func.max(s.events.c.ordinal), 0) + 1).where(
            s.events.c.tenant == t
        )
    )
    return insert(
        c,
        s.events,
        t,
        uid("ev"),
        ordinal=seq,
        type=event,
        product_id=product,
        session_id=sid,
        task_id=task,
        run_id=run,
        data=data or {},
    )


def enqueue(
    c, t, product, kind, *, task=None, sid=None, queue="system", payload=None, due=None
):
    id = insert(
        c,
        s.runs,
        t,
        uid("r"),
        kind=kind,
        product_id=product,
        task_id=task,
        session_id=sid,
        queue=queue,
        payload=payload or {},
        status="queued",
        due=due or now(),
    )
    emit(c, t, product, "run.queued", sid=sid, task=task, run=id, data={"kind": kind})
    return id


def finish(c, t, r, result, *, error=None):
    status = "failed" if error else "succeeded"
    update(
        c,
        s.runs,
        t,
        r.id,
        status=status,
        result=result,
        error=error,
        lease_token=None,
        lease_until=None,
    )
    emit(
        c,
        t,
        r.product_id,
        "run.failed" if error else "run.finished",
        sid=r.session_id,
        task=r.task_id,
        run=r.id,
        data={"status": status, "action": (result or {}).get("action")},
    )


def cancel(c, t, r, reason):
    outcome = {"action": "suppressed", "reason": reason, "delivery": "not_sent"}
    ledger = (
        c.execute(
            select(s.sends).where(s.sends.c.tenant == t, s.sends.c.run_id == r.id)
        )
        .mappings()
        .first()
    )
    if ledger:
        # Cancellation cannot undo a platform request. Preserve its identity and actual disposition.
        outcome.update(
            send_id=ledger.id, message_id=ledger.message_id, delivery=ledger.status
        )
        if ledger.status in {"dispatching", "unknown", "accepted"}:
            outcome["action"] = "reply"
    update(
        c,
        s.runs,
        t,
        r.id,
        status="cancelled",
        result=outcome,
        lease_until=None,
        lease_token=None,
    )
    emit(
        c,
        t,
        r.product_id,
        "run.finished",
        sid=r.session_id,
        task=r.task_id,
        run=r.id,
        data={"status": "cancelled", "reason": reason},
    )


def change_state(c, t, se, task, product, state):
    if se.status != state:
        update(c, s.sessions, t, se.id, status=state)
        emit(
            c,
            t,
            product,
            "session.state_changed",
            sid=se.id,
            task=task.id,
            data={"status": state},
        )
