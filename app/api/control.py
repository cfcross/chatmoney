from fastapi import APIRouter
from app import contracts as x, control as ctl, chat
from app.http import Read, Write, VIEW

router = APIRouter(tags=["任务与人工交接"])


@router.post("/tasks", status_code=201)
def create_task(body: x.TaskCreate, call: Write):
    return call.execute(
        ("business", "operator"),
        lambda c, w: ctl.create_task(c, w, body),
        body.model_dump(exclude={"name", "note"})
        | body.model_dump(include={"name", "note"}, exclude_unset=True),
        authorize=lambda c, w: ctl.agent(c, w, body.agent_id),
    )


@router.get("/tasks/{task_id}")
def get_task(task_id: str, call: Read):
    def read(c, w):
        tk, _ = ctl.task_access(c, w, task_id)
        return 200, dict(
            task_id=tk.id,
            name=tk.display_name or tk.external_ref,
            note=tk.note,
            edit_revision=tk.edit_revision,
            status=tk.status,
            reason=tk.reason,
            agent_version=tk.agent_version,
        )

    return call.execute((*VIEW, "operator"), read)


def command(id, cmd, body, call):
    return call.execute(
        ("business", "operator"),
        lambda c, w: ctl.task_command(c, w, id, cmd, getattr(body, "reason", None)),
        body.model_dump(),
        authorize=lambda c, w: ctl.task_access(c, w, id),
    )


@router.post("/tasks/{task_id}/start", status_code=202)
def start_task(task_id: str, body: x.Empty, call: Write):
    return command(task_id, "start", body, call)


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: str, body: x.Reason, call: Write):
    return command(task_id, "pause", body, call)


@router.post("/tasks/{task_id}/resume", status_code=202)
def resume_task(task_id: str, body: x.Reason, call: Write):
    return command(task_id, "resume", body, call)


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, body: x.Reason, call: Write):
    return command(task_id, "stop", body, call)


def transition(fn, sid, body, call, roles):
    return call.execute(
        roles,
        lambda c, w: fn(c, w, sid, body),
        body.model_dump(),
        authorize=lambda c, w: chat.access(c, w, sid),
    )


@router.post("/sessions/{session_id}/handoff", status_code=201)
def request_handoff(session_id: str, body: x.Handoff, call: Write):
    return transition(ctl.handoff, session_id, body, call, ("business", "seat"))


@router.post("/sessions/{session_id}/takeover")
def takeover(session_id: str, body: x.Takeover, call: Write):
    return transition(ctl.claim, session_id, body, call, ("seat",))


@router.post("/sessions/{session_id}/human-messages", status_code=202)
def human_message(session_id: str, body: x.HumanMessage, call: Write):
    return transition(ctl.human_message, session_id, body, call, ("seat",))


@router.post("/sessions/{session_id}/release")
def release_session(session_id: str, body: x.Release, call: Write):
    return transition(ctl.release, session_id, body, call, ("seat",))
