"""Read-only presentation queries. Writes and business-state decisions stay in the v1 API."""

from typing import Literal
from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, case, func, literal, or_, select
from app import schema as s
from app.catalog import RULES, TOOL_NAMES, agent, own_activity, own_sessions
from app.db import now, reject, row
from app.http import Read, VIEW
from app.knowledge import book
from app.settings import settings
from app.team import TEAM
from app.web_identity import can_issue_members, workspace_context

router = APIRouter(prefix="/console/data", include_in_schema=False)
AGENTS = ("config", "publish", "business")
TASKS = (*VIEW, "operator")
RUNS = (*TASKS, "config", "publish")
PEOPLE = ("business",)
LIST_ROLES = {
    "agents": AGENTS,
    "books": AGENTS,
    "sessions": VIEW,
    "handoffs": VIEW,
    "tasks": TASKS,
    "runs": RUNS,
    "contacts": PEOPLE,
}


def join(table, parent, foreign):
    return and_(table.c.tenant == parent.c.tenant, table.c.id == foreign)


def match(w, table):
    condition = and_(table.c.tenant == w.tenant, table.c.product_id.in_(w.products))
    if table is s.agents and w.personal_agents:
        condition &= table.c.owner_id == w.actor
    return condition


def visible_runs(w):
    r = s.runs
    if set(VIEW).intersection(w.roles):
        return (match(w, r) & own_activity(w, r)) if w.personal_agents else match(w, r)
    kinds = []
    if w.roles.intersection({"config", "publish"}):
        kinds.append("index")
    if "operator" in w.roles:
        kinds.extend(["started", "resumed"])
    return and_(match(w, r), r.c.kind.in_(kinds))


def agent_view(w):
    a, v = s.agents, s.versions
    editor = w.personal_agents or bool(w.roles.intersection({"config", "publish"}))
    registry = a.outerjoin(
        v,
        and_(
            v.c.tenant == a.c.tenant,
            v.c.agent_id == a.c.id,
            v.c.number == a.c.published,
        ),
    )
    name = a.c.name if editor else v.c.name
    stamp = (
        select(func.max(s.audit.c.created))
        .where(
            s.audit.c.tenant == w.tenant,
            s.audit.c.resource.in_(
                [
                    "/api/v1/agents/" + a.c.id,
                    "/api/v1/agents/" + a.c.id + "/publish",
                    "/api/v1/agents/" + a.c.id + "/disable",
                ]
            ),
        )
        .correlate(a)
        .scalar_subquery()
    )
    creation = (
        select(func.min(s.audit.c.created))
        .select_from(
            s.audit.join(
                s.idempotency,
                and_(
                    s.audit.c.tenant == s.idempotency.c.tenant,
                    s.audit.c.detail["request_id"].astext
                    == s.idempotency.c.response["request_id"].astext,
                ),
            )
        )
        .where(
            s.audit.c.tenant == w.tenant,
            s.audit.c.resource == "/api/v1/agents",
            s.idempotency.c.response["data"]["agent_id"].astext == a.c.id,
        )
        .correlate(a)
        .scalar_subquery()
    )
    return (
        select(
            a.c.id,
            name.label("name"),
            a.c.product_id,
            a.c.workflow,
            a.c.published.label("version"),
            case(
                (a.c.disabled, "disabled"),
                (a.c.published > 0, "published"),
                else_="draft",
            ).label("status"),
            (
                and_(
                    a.c.published > 0,
                    or_(v.c.config != a.c.draft, v.c.name != a.c.name),
                )
                if editor
                else literal(False)
            ).label("changed"),
            func.coalesce(stamp, v.c.created, creation).label("at"),
        )
        .select_from(registry)
        .where(
            match(w, a), a.c.deleted_at.is_(None), literal(editor) | (a.c.published > 0)
        )
    )


def bound_version(task):
    return and_(
        s.versions.c.tenant == task.c.tenant,
        s.versions.c.agent_id == task.c.agent_id,
        s.versions.c.number == task.c.agent_version,
    )


def task_view(w):
    t, a = s.tasks, s.agents
    return (
        select(
            t.c.id,
            t.c.status,
            t.c.external_ref,
            t.c.display_name.label("task_name"),
            a.c.id.label("agent_id"),
            s.versions.c.name.label("name"),
            a.c.workflow,
            a.c.product_id,
            t.c.agent_version,
            t.c.reason,
        )
        .select_from(
            t.join(a, join(a, t, t.c.agent_id)).join(s.versions, bound_version(t))
        )
        .where(match(w, a), a.c.deleted_at.is_(None), t.c.deleted_at.is_(None))
    )


def session_view(w, handoffs=False):
    se, t, a, cu, cv, h = (
        s.sessions,
        s.tasks,
        s.agents,
        s.customers,
        s.conversations,
        s.handoffs,
    )
    m, r = s.messages, s.runs
    latest_run = (
        select(r.c.id, r.c.created)
        .where(r.c.tenant == w.tenant, r.c.session_id == se.c.id)
        .order_by(r.c.ordinal.desc())
        .limit(1)
        .lateral()
    )
    latest_msg = (
        select(m.c.body, m.c.kind)
        .where(m.c.tenant == w.tenant, m.c.session_id == se.c.id)
        .order_by(m.c.seq.desc())
        .limit(1)
        .lateral()
    )
    src = (
        se.join(t, join(t, se, se.c.task_id))
        .join(a, join(a, t, t.c.agent_id))
        .join(s.versions, bound_version(t))
        .join(cu, join(cu, se, se.c.customer_id))
        .join(cv, join(cv, se, se.c.conversation_id))
        .outerjoin(h, join(h, se, se.c.handoff_id))
        .outerjoin(latest_run, literal(True))
        .outerjoin(latest_msg, literal(True))
    )
    q = (
        select(
            se.c.id,
            cu.c.name.label("name"),
            cu.c.profile,
            cu.c.id.label("customer_id"),
            s.versions.c.name.label("agent"),
            a.c.workflow,
            a.c.product_id,
            a.c.id.label("agent_id"),
            se.c.task_id,
            t.c.deleted_at.is_not(None).label("task_deleted"),
            t.c.status.label("task_status"),
            t.c.agent_version,
            se.c.status,
            case((cv.c.active_session == se.c.id, cv.c.owner), else_="none").label(
                "owner"
            ),
            h.c.reason,
            se.c.handoff_id,
            latest_run.c.id.label("run_id"),
            latest_run.c.created.label("at"),
            func.left(latest_msg.c.body, 180).label("last_message"),
            latest_msg.c.kind.label("message_kind"),
        )
        .select_from(src)
        .where(match(w, a))
    )
    if handoffs:
        q = q.where(se.c.status.in_(["human_owned", "handoff_pending"]))
    return q


def run_view(w):
    r, cu, se, t, a = s.runs, s.customers, s.sessions, s.tasks, s.agents
    src = (
        r.outerjoin(se, join(se, r, r.c.session_id))
        .outerjoin(cu, join(cu, se, se.c.customer_id))
        .outerjoin(t, join(t, r, r.c.task_id))
        .outerjoin(a, join(a, t, t.c.agent_id))
        .outerjoin(s.versions, bound_version(t))
    )
    return (
        select(
            r.c.id,
            r.c.product_id,
            r.c.kind,
            r.c.status,
            r.c.created.label("at"),
            r.c.session_id,
            r.c.task_id,
            cu.c.name.label("name"),
            s.versions.c.name.label("agent"),
            r.c.result["action"].astext.label("action"),
        )
        .select_from(src)
        .where(visible_runs(w))
    )


def books_view(w):
    b = s.kbs
    query = select(b.c.id, b.c.name, b.c.product_id, b.c.latest.label("version")).where(
        match(w, b), b.c.deleted_at.is_(None)
    )
    if not w.manage_knowledge:
        query = query.where(b.c.latest > 0)
    return query


def contacts_view(w):
    cv, ac, cu, l = s.conversations, s.accounts, s.customers, s.leads
    source = (
        select(l.c.id)
        .where(
            l.c.tenant == w.tenant,
            l.c.customer_id == cu.c.id,
            l.c.product_id == ac.c.product_id,
        )
        .order_by(l.c.id)
        .limit(1)
        .correlate(cu, ac)
        .scalar_subquery()
    )
    available = match(w, ac)
    if w.personal_agents:
        available &= cv.c.active_session.is_(None) | cv.c.active_session.in_(
            own_sessions(w)
        )
    return (
        select(
            cv.c.id,
            cu.c.name,
            cu.c.id.label("customer_id"),
            ac.c.product_id,
            cv.c.active_session,
            source.label("source_ref"),
        )
        .select_from(
            cv.join(ac, join(ac, cv, cv.c.account_id)).join(
                cu, join(cu, cv, cv.c.customer_id)
            )
        )
        .where(available)
    )


QUERIES = {
    "agents": agent_view,
    "books": books_view,
    "tasks": task_view,
    "sessions": session_view,
    "handoffs": lambda w: session_view(w, True),
    "runs": run_view,
    "contacts": contacts_view,
}


def list_page(
    c,
    w,
    kind,
    search="",
    page=1,
    limit=25,
    id=None,
    product=None,
    state=None,
    agent_id=None,
):
    x = QUERIES[kind](w).subquery()
    query = select(x)
    if agent_id:
        if kind != "tasks":
            reject(422, "VALIDATION_ERROR", "助手筛选只适用于任务。")
        agent(c, w, agent_id)
        query = query.where(x.c.agent_id == agent_id)
    if product:
        query = query.where(x.c.product_id == w.product(product))
    if id:
        query = query.where(x.c.id == id)
    if state and "status" in x.c:
        query = query.where(x.c.status == state)
    if search:
        fields = [
            x.c[k] for k in ("name", "agent", "external_ref", "task_name") if k in x.c
        ]
        query = query.where(
            or_(*(f.icontains(search, autoescape=True) for f in fields))
        )
    total = c.scalar(select(func.count()).select_from(query.subquery()))
    order = ([x.c.at.desc().nulls_last()] if "at" in x.c else []) + [x.c.id]
    items = c.execute(
        query.order_by(*order).offset((page - 1) * limit).limit(limit)
    ).mappings()
    items = [dict(i) for i in items]
    if kind == "books" and items:
        by_id = {i["id"]: i for i in items}
        for item in items:
            item["available_versions"] = []
        docs = c.execute(
            select(s.docs.c.id, s.docs.c.revision, s.docs.c.status).where(
                s.docs.c.tenant == w.tenant, s.docs.c.kb_id.in_(by_id)
            )
        ).all()
        valid = {(d.id, d.revision) for d in docs if d.status == "ready"}
        snapshots = c.execute(
            select(s.snapshots.c.kb_id, s.snapshots.c.number, s.snapshots.c.documents)
            .where(s.snapshots.c.tenant == w.tenant, s.snapshots.c.kb_id.in_(by_id))
            .order_by(s.snapshots.c.number.desc())
        )
        for snap in snapshots:
            if snap.documents and all(
                (ref["document_id"], ref["revision"]) in valid for ref in snap.documents
            ):
                by_id[snap.kb_id]["available_versions"].append(snap.number)
    return dict(items=items, total=total, page=page, limit=limit)


@router.get("/directory/{kind}")
def directory(
    kind: Literal[
        "agents", "books", "tasks", "sessions", "handoffs", "runs", "contacts"
    ],
    call: Read,
    search: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    id: str | None = None,
    product: str | None = None,
    state: str | None = None,
    agent_id: str | None = None,
):
    return call.execute(
        LIST_ROLES[kind],
        lambda c, w: (
            200,
            jsonable_encoder(
                list_page(c, w, kind, search, page, limit, id, product, state, agent_id)
            ),
        ),
    )


@router.get("/workspace")
def workspace(call: Read):
    def view(c, w):
        tenant = (
            c.execute(select(s.tenants).where(s.tenants.c.id == w.tenant))
            .mappings()
            .one()
        )
        products = (
            c.execute(
                select(s.products.c.id, s.products.c.name, s.products.c.version_ref)
                .where(s.products.c.tenant == w.tenant, s.products.c.id.in_(w.products))
                .order_by(s.products.c.name, s.products.c.id)
            )
            .mappings()
            .all()
        )

        def permitted(roles):
            return bool(w.roles.intersection(roles))

        counts = dict(agents=None, running=None, waiting=None, handoffs=None)
        if permitted(AGENTS):
            f = match(w, s.agents) & s.agents.c.deleted_at.is_(None)
            if not w.personal_agents and not permitted(("config", "publish")):
                f = and_(f, s.agents.c.published > 0)
            counts["agents"] = c.scalar(
                select(func.count()).select_from(s.agents).where(f)
            )
        if permitted(TASKS):
            tasks = task_view(w).subquery()
            states = dict(
                c.execute(
                    select(tasks.c.status, func.count()).group_by(tasks.c.status)
                ).all()
            )
            counts.update(
                running=states.get("running", 0), waiting=states.get("waiting", 0)
            )
        if permitted(VIEW):
            counts["handoffs"] = c.scalar(
                select(func.count())
                .select_from(
                    s.sessions.join(
                        s.tasks, join(s.tasks, s.sessions, s.sessions.c.task_id)
                    ).join(s.agents, join(s.agents, s.tasks, s.tasks.c.agent_id))
                )
                .where(
                    match(w, s.agents),
                    s.sessions.c.status.in_(["handoff_pending", "human_owned"]),
                )
            )
        cfg = settings()
        return 200, dict(
            tenant=tenant.name,
            team_workspace=w.tenant == TEAM,
            company_ref=tenant.company_ref,
            actor=w.actor,
            username=c.scalar(
                select(s.web_accounts.c.username).where(
                    s.web_accounts.c.tenant == w.tenant, s.web_accounts.c.id == w.actor
                )
            )
            if w.session_id
            else None,
            roles=sorted(w.roles),
            manage_knowledge=w.manage_knowledge,
            manage_models=w.personal_models,
            manage_members=can_issue_members(c, w),
            browser_context=workspace_context(w.session_id),
            products=[dict(p) for p in products],
            rules={
                wf: {"ref": k, "triggers": list(signals)}
                for k, (wf, signals) in RULES.items()
            },
            tools=sorted(TOOL_NAMES),
            model=cfg.llm_backend,
            model_configured=cfg.llm_backend == "mock"
            or bool(
                c.scalar(
                    select(s.model_connections.c.id)
                    .where(
                        s.model_connections.c.tenant == w.tenant,
                        s.model_connections.c.created_by == w.actor,
                        s.model_connections.c.status == "ready",
                    )
                    .limit(1)
                )
            ),
            channel="mock",
            worker=None,
            counts=counts,
            at=now().isoformat(),
        )

    return call.execute((*RUNS, "model_admin"), view)


@router.get("/documents/{document_id}")
def preview(document_id: str, call: Read):
    def view(c, w):
        d = row(c, s.docs, w.tenant, document_id)
        k = book(c, w, d.kb_id)
        if d.status == "deleted":
            reject(404, "NOT_FOUND", "文件已删除或不可见")
        if not w.manage_knowledge:
            published = (
                k.latest
                and row(c, s.snapshots, w.tenant, f"{k.id}:{k.latest}").documents
            )
            if (
                d.status != "ready"
                or not published
                or not any(x["document_id"] == d.id for x in published)
            ):
                reject(404, "NOT_FOUND", "当前身份不可查看该资料")
        return 200, dict(
            title=d.title, text=d.original, source=d.source_note, revision=d.revision
        )

    return call.execute(AGENTS, view)
