"""Authenticated workbench: source management, per-version authorizations and private research."""

from fastapi import APIRouter
from app.http import Read, Write
from app.contracts import Empty
from app.research import service as svc, contracts as b

router = APIRouter(prefix="/console/manage", include_in_schema=False)


@router.get("/research-sources")
def sources(call: Read):
    return call.execute(svc.OWNER, lambda c, w: (200, svc.sources(c, w, management=True)))


@router.post("/research-sources")
def create(body: b.Source, call: Write):
    return call.execute(
        ("publish",), lambda c, w: svc.create_source(c, w, body), body.model_dump()
    )


@router.post("/research-sources/{id}/revoke")
def revoke(id: str, body: Empty, call: Write):
    return call.execute(("publish",), lambda c, w: svc.revoke_source(c, w, id), {})


@router.get("/research-grants")
def grants(call: Read):
    return call.execute(("publish",), lambda c, w: (200, {"items": svc.grants(c, w)}))


@router.post("/research-grants/{id}/decision")
def decide(id: str, body: b.Decision, call: Write):
    return call.execute(
        ("publish",), lambda c, w: svc.decide(c, w, id, body), body.model_dump()
    )


@router.get("/agents/{aid}/research")
def dashboard(aid: str, call: Read):
    return call.execute(svc.OWNER, lambda c, w: (200, svc.dashboard(c, w, aid)))


@router.post("/agents/{aid}/research/grants")
def apply(aid: str, body: b.GrantRequest, call: Write):
    return call.execute(
        svc.OWNER, lambda c, w: svc.request_grant(c, w, aid, body), body.model_dump()
    )


@router.post("/agents/{aid}/research/jobs", status_code=202)
def start(aid: str, body: b.Start, call: Write):
    return call.execute(
        svc.OWNER, lambda c, w: svc.start(c, w, aid, body), body.model_dump()
    )


@router.get("/research-jobs/{id}")
def job(id: str, call: Read):
    return call.execute(svc.OWNER, lambda c, w: (200, svc.detail(c, w, id)))


@router.post("/research-jobs/{id}/stop")
def stop(id: str, body: Empty, call: Write):
    return call.execute(svc.OWNER, lambda c, w: svc.stop(c, w, id), {})


@router.post("/research-jobs/{id}/retry", status_code=202)
def retry(id: str, body: Empty, call: Write):
    return call.execute(svc.OWNER, lambda c, w: svc.retry(c, w, id), {})


@router.post("/research-jobs/{id}/review")
def review(id: str, body: b.Review, call: Write):
    return call.execute(
        svc.OWNER, lambda c, w: svc.review(c, w, id, body), body.model_dump()
    )


@router.post("/research-jobs/{id}/lead")
def lead(id: str, body: Empty, call: Write):
    return call.execute(svc.OWNER, lambda c, w: svc.lead(c, w, id), {})
