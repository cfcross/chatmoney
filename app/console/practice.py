"""Personal practice operations, using the same login, CSRF/context and idempotency as the workbench."""

from typing import Literal
from fastapi import APIRouter, Query
from app.http import Read, Write
from app.practice import service as svc

router = APIRouter(prefix="/console/manage", include_in_schema=False)
OWNER = ("config", "own_agents", "publish")


@router.get("/agents/{aid}/practice-personas")
def personas(aid: str, call: Read):
    return call.execute(OWNER, lambda c, w: (200, svc.choices(c, w, aid)))


@router.get("/agents/{aid}/practices")
def history(
    aid: str,
    call: Read,
    before: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=80),
    finished_only: bool = False,
):
    return call.execute(
        OWNER, lambda c, w: (200, svc.history(c, w, aid, before, limit, finished_only))
    )


@router.post("/agents/{aid}/practices", status_code=202)
def start(aid: str, body: svc.Start, call: Write):
    return call.execute(
        OWNER, lambda c, w: svc.create(c, w, aid, body), body.model_dump()
    )


@router.get("/practices/{id}")
def detail(id: str, call: Read):
    return call.execute(OWNER, lambda c, w: (200, svc.detail(c, w, id)))


@router.post("/practices/{id}/control/{action}")
def control(
    id: str,
    action: Literal["next", "auto", "pause", "resume", "stop"],
    body: svc.Control,
    call: Write,
):
    return call.execute(
        OWNER, lambda c, w: svc.control(c, w, id, action, body), body.model_dump()
    )


@router.post("/practices/{id}/review")
def review(id: str, body: svc.Review, call: Write):
    return call.execute(
        OWNER, lambda c, w: svc.review(c, w, id, body), body.model_dump()
    )


@router.post("/practices/{id}/submit")
def submit(id: str, body: svc.Control, call: Write):
    return call.execute(
        OWNER, lambda c, w: svc.submit(c, w, id, body), body.model_dump()
    )


@router.get("/practice-candidates")
def candidates(call: Read):
    return call.execute(("publish",), lambda c, w: (200, svc.inbox(c, w)))


@router.post("/practice-candidates/{id}/review")
def approval(id: str, body: svc.Approval, call: Write):
    return call.execute(
        ("publish",), lambda c, w: svc.approval(c, w, id, body), body.model_dump()
    )
