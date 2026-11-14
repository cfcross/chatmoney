import asyncio
from datetime import timedelta
import json
from typing import Annotated
from fastapi import APIRouter, Header, Request
from starlette.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, func
from app import schema as s
from app.catalog import own_activity
from app.chat import access
from app.control import task_access
from app.db import now, reject, row, transaction
from app.http import Read, VIEW
from app.notifications import envelope
from app.settings import settings

router = APIRouter(tags=["执行与事件"])
READ_RUN = (*VIEW, "config", "publish", "operator")


def visible_run(c, w, id):
    r = row(c, s.runs, w.tenant, id)
    w.product(r.product_id)
    if w.personal_agents:
        if r.task_id:
            task_access(c, w, r.task_id, history=True)
        elif r.session_id:
            access(c, w, r.session_id, history=True)
        else:
            reject(404, "NOT_FOUND", "执行不可见")
    if not w.roles.intersection(VIEW) and r.kind not in {"index", "started", "resumed"}:
        reject(404, "NOT_FOUND", "执行不可见")
    return r


@router.get("/runs/{run_id}")
def read_run(run_id: str, call: Read):
    def read(c, w):
        r = visible_run(c, w, run_id)
        res = r.result.copy() if r.result else None
        if res and res.get("send_id"):
            res["delivery"] = row(c, s.sends, w.tenant, res["send_id"]).status
        used = c.execute(
            select(s.tools.c.id, s.tools.c.tool, s.tools.c.status).where(
                s.tools.c.tenant == w.tenant, s.tools.c.run_id == r.id
            )
        )
        calls = c.execute(
            select(
                s.model_calls.c.stage, s.model_calls.c.status, s.model_calls.c.metrics
            ).where(s.model_calls.c.tenant == w.tenant, s.model_calls.c.run_id == r.id)
        )
        return 200, dict(
            run_id=r.id,
            status=r.status,
            result=res,
            error=r.error,
            session_id=r.session_id,
            task_id=r.task_id,
            tool_executions=[
                dict(execution_id=x.id, tool=x.tool, status=x.status) for x in used
            ],
            model_calls=[
                dict(stage=x.stage, status=x.status, **x.metrics) for x in calls
            ],
        )

    return call.execute(READ_RUN, read)


def event_query(w, filters):
    q = select(s.events).where(
        s.events.c.tenant == w.tenant, s.events.c.product_id.in_(w.products)
    )
    if w.personal_agents:
        q = q.where(own_activity(w, s.events))
    for k, v in filters.items():
        if v:
            q = q.where(s.events.c[k] == v)
    return q


def cursor_start(w, filters, last):
    with transaction(w.tenant) as c:
        if filters["session_id"]:
            access(c, w, filters["session_id"], history=True)
        if filters["task_id"]:
            task_access(c, w, filters["task_id"], history=True)
        if filters["run_id"]:
            visible_run(c, w, filters["run_id"])
        q = event_query(w, filters)
        if last:
            event = c.execute(q.where(s.events.c.id == last)).mappings().first()
            if not event or event.created < now() - timedelta(
                hours=settings().event_retention_hours
            ):
                reject(410, "CURSOR_EXPIRED", "事件游标过期或不可用")
            return event.ordinal
        return c.scalar(q.with_only_columns(func.max(s.events.c.ordinal))) or 0


def event_page(w, filters, cursor):
    with transaction(w.tenant) as c:
        # A long-lived subscription must stop if the identity is disabled or loses access.
        actor = row(c, s.identities, w.tenant, w.actor)
        if w.session_id:
            from app.web_identity import live_session

            if not live_session(c, w.tenant, w.actor, w.session_id):
                return None
        if (
            not actor.active
            or not w.roles.issubset(actor.roles)
            or (
                "own_agents" in actor.roles
                and not w.personal_agents
                and not {"config", "publish"}.intersection(actor.roles)
            )
            or not set(actor.roles).intersection(VIEW)
            or not w.products.issubset(actor.products)
        ):
            return None
        rows = c.execute(
            event_query(w, filters)
            .where(s.events.c.ordinal > cursor)
            .order_by(s.events.c.ordinal)
            .limit(50)
        ).mappings()
        return [(e.ordinal, envelope(e)) for e in rows]


@router.get(
    "/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def event_stream(
    request: Request,
    call: Read,
    session_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    last: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    w = call.identity
    w.require(*VIEW)
    filters = dict(session_id=session_id, task_id=task_id, run_id=run_id)
    start = await run_in_threadpool(cursor_start, w, filters, last)

    async def generate():
        cursor = start
        yield ": connected\n\n"
        idle = 0
        while not await request.is_disconnected():
            page = await run_in_threadpool(event_page, w, filters, cursor)
            if page is None:
                return
            for cursor, e in page:
                yield f"id: {e['event_id']}\nevent: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            if not page:
                idle += 1
                if idle % 40 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
