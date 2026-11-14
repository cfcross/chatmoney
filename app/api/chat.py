from fastapi import APIRouter, Query
from sqlalchemy import select
from app import chat, contracts as x, schema as s
from app.db import row, reject
from app.http import Read, Write, VIEW
from app.work import enqueue

router = APIRouter(prefix="/sessions", tags=["会话、记忆与人工"])


@router.post("", status_code=201)
def create_session(body: x.SessionCreate, call: Write):
    from app.catalog import agent

    return call.execute(
        ("business",),
        lambda c, w: chat.create(c, w, body),
        body.model_dump(),
        authorize=lambda c, w: agent(c, w, body.agent_id),
    )


@router.post("/{session_id}/messages", status_code=202)
def submit_message(session_id: str, body: x.Inbound, call: Write):
    return call.execute(
        ("business", "adapter"),
        lambda c, w: chat.inbound(c, w, session_id, body),
        body.model_dump(mode="json"),
        authorize=lambda c, w: chat.access(c, w, session_id),
    )


@router.get("/{session_id}")
def session_state(session_id: str, call: Read):
    def read(c, w):
        se, task, a, cv = chat.access(c, w, session_id, history=True)
        h = row(c, s.handoffs, w.tenant, se.handoff_id) if se.handoff_id else None
        unresolved = c.scalars(
            select(s.sends.c.id).where(
                s.sends.c.tenant == w.tenant,
                s.sends.c.conversation_id == cv.id,
                s.sends.c.status.in_(["unknown", "dispatching"]),
            )
        ).all()
        package = None
        if h:
            sources = [
                chat.source_message(c, w.tenant, se.id, mid) for mid in h.evidence
            ]
            mem = row(c, s.memory, w.tenant, se.id)
            latest = c.execute(
                select(s.sends.c.id, s.sends.c.status)
                .where(s.sends.c.tenant == w.tenant, s.sends.c.session_id == se.id)
                .order_by(s.sends.c.created.desc())
                .limit(1)
            ).first()
            package = dict(
                reason=h.reason,
                evidence_refs=h.evidence,
                owner=h.owner,
                status=h.status,
                evidence=[dict(message_id=m.id, text=m.body) for m in sources],
                agent_version=task.agent_version,
                pending=mem.pending,
                last_send={"send_id": latest.id, "status": latest.status}
                if latest
                else None,
            )
        return 200, dict(
            session_id=se.id,
            task_id=task.id,
            conversation_id=cv.id,
            status=se.status,
            owner=cv.owner if cv.active_session == se.id else "none",
            owner_epoch=cv.epoch,
            handoff_id=se.handoff_id,
            unresolved_send_ids=unresolved,
            handoff=package,
        )

    return call.execute(VIEW, read)


@router.get("/{session_id}/messages")
def get_messages(
    session_id: str,
    call: Read,
    after: str | None = None,
    limit: int = Query(30, ge=1, le=100),
):
    def read(c, w):
        chat.access(c, w, session_id, history=True)
        return 200, chat.records(c, w.tenant, session_id, limit, after)

    return call.execute(VIEW, read)


@router.get("/{session_id}/memory")
def get_memory(session_id: str, call: Read):
    def read(c, w):
        chat.access(c, w, session_id, history=True)
        return 200, chat.memory_view(c, w.tenant, session_id)

    return call.execute(VIEW, read)


@router.patch("/{session_id}/memory")
def correct_memory(session_id: str, body: x.Correction, call: Write):
    call.identity.require("seat")
    return call.execute(
        ("correct",),
        lambda c, w: chat.correct(c, w, session_id, body),
        body.model_dump(),
        authorize=lambda c, w: chat.access(c, w, session_id),
    )


@router.post("/{session_id}/actions", status_code=202)
def schedule_action(session_id: str, body: x.Action, call: Write):
    def apply(c, w):
        se, task, a, cv = chat.access(c, w, session_id)
        if (
            a.workflow != "sales"
            or a.disabled
            or cv.owner != "auto"
            or cv.opted_out
            or cv.active_session != se.id
            or task.status not in {"running", "waiting"}
        ):
            reject(409, "STATE_CONFLICT", "当前不允许自动动作")
        chat.require_model_operator(c, w, a, task.agent_version)
        chat.valid_source(c, w.tenant, body.source_ref, se.customer_id, a.product_id)
        rid = enqueue(
            c,
            w.tenant,
            a.product_id,
            body.kind,
            sid=se.id,
            task=task.id,
            queue=a.workflow,
            payload={
                "epoch": cv.epoch,
                "source_ref": body.source_ref,
                "model_owner": a.owner_id,
            },
            due=body.due_at,
        )
        return 202, dict(run_id=rid, status="queued")

    return call.execute(
        ("business",),
        apply,
        body.model_dump(mode="json"),
        authorize=lambda c, w: chat.access(c, w, session_id),
    )
