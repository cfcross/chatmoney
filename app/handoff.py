from sqlalchemy import select
from app import schema as s
from app.db import insert, row, uid, update
from app.work import cancel, change_state, emit


def cancel_automatic(c, t, sid, reason, keep=None):
    se = row(c, s.sessions, t, sid)
    task = row(c, s.tasks, t, se.task_id)
    a = row(c, s.agents, t, task.agent_id)
    q = select(s.sends).where(
        s.sends.c.tenant == t,
        s.sends.c.session_id == sid,
        s.sends.c.actor == "auto",
        s.sends.c.status == "pending",
    )
    for send in c.execute(q).mappings():
        update(c, s.sends, t, send.id, status="cancelled")
        emit(
            c,
            t,
            a.product_id,
            "delivery.updated",
            sid=sid,
            task=task.id,
            run=send.run_id,
            data={"send_id": send.id, "status": "cancelled"},
        )
    q = select(s.runs).where(
        s.runs.c.tenant == t,
        s.runs.c.session_id == sid,
        s.runs.c.kind.in_(["follow_up", "opening", "inbound"]),
        s.runs.c.status.in_(["queued", "retry_wait", "running"]),
    )
    if keep:
        q = q.where(s.runs.c.id != keep)
    for r in c.execute(q).mappings():
        cancel(c, t, r, reason)


def freeze(c, t, se, task, a, cv, reason, evidence, run_id=None):
    if se.handoff_id:
        return row(c, s.handoffs, t, se.handoff_id).id, cv.epoch, False
    epoch = cv.epoch + 1
    h = insert(
        c,
        s.handoffs,
        t,
        uid("h"),
        session_id=se.id,
        status="handoff_pending",
        reason=reason,
        evidence=evidence,
    )
    update(c, s.conversations, t, cv.id, owner="pending", epoch=epoch)
    update(c, s.sessions, t, se.id, handoff_id=h)
    change_state(c, t, se, task, a.product_id, "handoff_pending")
    cancel_automatic(c, t, se.id, reason, keep=run_id)
    emit(
        c,
        t,
        a.product_id,
        "handoff.created",
        sid=se.id,
        task=task.id,
        run=run_id,
        data={"handoff_id": h, "reason": reason},
    )
    return h, epoch, True
