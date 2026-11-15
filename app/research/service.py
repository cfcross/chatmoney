"""One published version + one source + one independently approved grant. No CRM or sender imports."""

from datetime import timedelta
from urllib.parse import urlsplit
from sqlalchemy import select, or_, and_
from app import catalog, schema as s, knowledge
from app.db import insert, now, reject, row, uid, update
from app.http import Identity
from app.practice.service import pack, fingerprint
from app.research.network import clean_url, within, Denied, ERR, TOOL, VERSION
from app.research.contracts import Start

OWNER = ("config", "own_agents", "publish")
SKILL = "company-research/1"
ACTIVE = ("queued", "fetching", "captured", "model_started")


def url(value):
    try:
        return clean_url(value)
    except Denied as e:
        reject(422, "RESEARCH_" + e.code, ERR[e.code])


def items(c, tab, t, *cond, limit=100):
    return (
        c.execute(
            select(tab)
            .where(tab.c.tenant == t, *cond)
            .order_by(tab.c.created.desc(), tab.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )


def source_info(r):
    return dict(
        id=r.id,
        name=r.name,
        base_url=r.base_url,
        status=r.status if r.expires > now() else "expired",
        expires=r.expires.isoformat(),
        policy=r.policy,
    )


def sources(c, w):
    return {"items": [source_info(r) for r in items(c, s.research_sources, w.tenant)]}


def create_source(c, w, b):
    if not b.allow_automation or not b.allow_evidence_storage:
        reject(422, "SOURCE_RIGHTS", "首版只处理明确许可自动访问、摘录和保存的来源。")
    base = url(b.base_url)
    if urlsplit(base).hostname == "agent.heiling.tech" and not urlsplit(
        base
    ).path.startswith("/research-samples/"):
        reject(422, "SOURCE_RESTRICTED", "工作台仅可登记独立的公开样本路径。")
    if any(
        r.status == "active" and r.expires > now()
        for r in items(
            c, s.research_sources, w.tenant, s.research_sources.c.base_url == base
        )
    ):
        reject(409, "SOURCE_EXISTS", "已登记相同来源，请使用现有许可。")
    id = uid("src")
    insert(
        c,
        s.research_sources,
        w.tenant,
        id,
        name=b.name,
        base_url=base,
        policy=b.model_dump(exclude={"name", "base_url"}),
        actor=w.actor,
        status="active",
        expires=now() + timedelta(days=b.valid_days),
    )
    return 201, dict(id=id)


def valid_source(c, t, id):
    src = row(c, s.research_sources, t, id)
    if (
        src.status != "active"
        or src.expires <= now()
        or not src.policy.get("allow_automation")
        or not src.policy.get("allow_evidence_storage")
    ):
        reject(403, "SOURCE_INACTIVE", "来源许可已撤回、过期或不允许留存。")
    return src


def scrub(c, t, condition):
    expired = (
        c.execute(
            select(s.research_jobs.c.id)
            .where(
                s.research_jobs.c.tenant == t,
                condition,
                s.research_jobs.c.status != "expired",
            )
            .limit(200)
        )
        .scalars()
        .all()
    )
    if expired:
        c.execute(
            s.research_jobs.update()
            .where(s.research_jobs.c.tenant == t, s.research_jobs.c.id.in_(expired))
            .values(
                status="expired",
                capture=None,
                report=None,
                input={},
                target="",
                error="SCOPE_CHANGED",
                lease=None,
                lease_until=None,
                updated=now(),
            )
        )
        # A lead is only a pointer. Remove its source-derived key; leave an audit tombstone.
        tab = s.research_leads
        c.execute(
            tab.update()
            .where(tab.c.tenant == t, tab.c.job_id.in_(expired))
            .values(target=tab.c.id, status="expired")
        )
    return len(expired)


def revoke_source(c, w, id):
    row(c, s.research_sources, w.tenant, id)
    update(c, s.research_sources, w.tenant, id, status="revoked")
    c.execute(
        s.research_grants.update()
        .where(
            s.research_grants.c.tenant == w.tenant,
            s.research_grants.c.source_id == id,
            s.research_grants.c.status.in_(["pending", "approved"]),
        )
        .values(status="revoked", decided_by=w.actor, reason="数据来源已撤回")
    )
    while scrub(c, w.tenant, s.research_jobs.c.source_id == id):
        pass
    return 200, {"id": id, "status": "revoked"}


def own(c, w, aid):
    a = catalog.agent(c, w, aid, history=True)
    if a.owner_id != w.actor:
        reject(404, "NOT_FOUND", "找不到本账号的销售助手。")
    if a.workflow != "sales":
        reject(422, "RESEARCH_SALES", "仅适用于企业销售助手。")
    return a


def version_for(c, w, a):
    from app.tools.gateway import TOOL_DIRECTORY

    desc = TOOL_DIRECTORY.get(TOOL, {})
    if desc.get("lane") != "research" or desc.get("read_or_write") != "read":
        reject(403, "RESEARCH_TOOL", "该只读工具尚未接入，不能授权。")
    if a.disabled or a.deleted_at or not a.published:
        reject(409, "RESEARCH_PUBLISH", "请先发布已对练的助手配置。")
    v = catalog.version(c, w.tenant, a.id, a.published)
    cfg = v.config
    if TOOL not in (cfg.get("research_tools") or []):
        reject(
            403,
            "RESEARCH_INTENT",
            "请在助手的「资料和工具」勾选企业官网资料研究，测试并发布此版本。",
        )
    if cfg.get("model_provider") not in ("mock", "deepseek") or not cfg.get(
        "knowledge"
    ):
        reject(
            422, "RESEARCH_CONFIG", "请绑定个人模型（或明确选择模拟）和获批产品资料。"
        )
    catalog.validate_profile(c, w, a, cfg)
    return v, fingerprint(pack(a, cfg, v.name))


def witness(c, t, a, h):
    return (
        c.execute(
            select(s.practice)
            .where(
                s.practice.c.tenant == t,
                s.practice.c.agent_id == a.id,
                s.practice.c.owner_id == a.owner_id,
                s.practice.c.fingerprint == h,
                or_(
                    and_(
                        s.practice.c.source == "published",
                        s.practice.c.source_version == a.published,
                    ),
                    s.practice.c.source == "draft",
                ),
                s.practice.c.candidate == "pending_platform",
                s.practice.c.review == "satisfied",
            )
            .order_by(s.practice.c.updated.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def request_grant(c, w, aid, b):
    a = own(c, w, aid)
    v, h = version_for(c, w, a)
    src = valid_source(c, w.tenant, b.source_id)
    sim = witness(c, w.tenant, a, h)
    if not sim:
        reject(409, "RESEARCH_PRACTICE", "请先完成相同配置的对练、人工审阅和候选审核。")
    tab = s.research_grants
    old = items(
        c,
        tab,
        w.tenant,
        tab.c.owner_id == w.actor,
        tab.c.agent_id == aid,
        tab.c.version == a.published,
        tab.c.source_id == src.id,
        tab.c.status.in_(["pending", "approved"]),
    )
    if old:
        return 200, {"id": old[0].id, "status": old[0].status}
    id = uid("grant")
    insert(
        c,
        tab,
        w.tenant,
        id,
        owner_id=w.actor,
        agent_id=aid,
        version=a.published,
        fingerprint=h,
        source_id=src.id,
        practice_id=sim.id,
        status="pending",
    )
    return 201, {"id": id, "status": "pending"}


def identity(c, t, id):
    ident = row(c, s.identities, t, id)
    if not ident.active:
        reject(403, "RESEARCH_OWNER", "成员已停用。")
    w = Identity(t, id, set(ident.roles), set(ident.products))
    w.require(*OWNER)
    return w


def verify(c, w, g, *, pending=False):
    a = own(c, w, g.agent_id)
    if (
        g.owner_id != w.actor
        or g.status != ("pending" if pending else "approved")
        or g.version != a.published
    ):
        reject(403, "RESEARCH_GRANT", "尚未获得该版本和来源的企业官网资料研究权限。")
    v, h = version_for(c, w, a)
    src = valid_source(c, w.tenant, g.source_id)
    q = row(c, s.practice, w.tenant, g.practice_id)
    if (
        g.fingerprint != h
        or q.fingerprint != h
        or q.candidate != "pending_platform"
        or q.review != "satisfied"
        or q.owner_id != w.actor
        or q.agent_id != a.id
    ):
        reject(403, "RESEARCH_REVIEW", "审核的配置已变化，请重新对练并申请。")
    return a, v, src


def decide(c, w, id, b):
    g = row(c, s.research_grants, w.tenant, id, locked=True)
    a = row(c, s.agents, w.tenant, g.agent_id)
    w.product(a.product_id)
    if (b.decision == "revoke" and g.status != "approved") or (
        b.decision != "revoke" and g.status != "pending"
    ):
        reject(409, "STATE_CONFLICT", "申请状态已经改变。")
    if b.decision == "approve":
        from app.db import model_scope

        model_scope(c, g.owner_id)
        verify(c, identity(c, w.tenant, g.owner_id), g, pending=True)
    status = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[
        b.decision
    ]
    update(
        c,
        s.research_grants,
        w.tenant,
        id,
        status=status,
        decided_by=w.actor,
        reason=b.reason,
    )
    return 200, {"id": id, "status": status}


def grants(c, w, aid=None):
    t = s.research_grants
    if aid:
        own(c, w, aid)
    cond = [t.c.agent_id == aid, t.c.owner_id == w.actor] if aid else []
    out = []
    for g in items(c, t, w.tenant, *cond):
        a = row(c, s.agents, w.tenant, g.agent_id)
        if a.product_id not in w.products:
            continue
        src = row(c, s.research_sources, w.tenant, g.source_id)
        out.append(
            dict(
                id=g.id,
                agent=a.name,
                agent_id=a.id,
                version=g.version,
                source=src.name,
                source_id=src.id,
                status=g.status,
                scope=src.base_url,
                reason=g.reason,
            )
        )
    return out


def own_job(c, w, id):
    j = row(c, s.research_jobs, w.tenant, id)
    if j.owner_id != w.actor:
        reject(404, "NOT_FOUND", "没有这项研究。")
    w.product(j.product_id)
    return j


def brief(j):
    return dict(
        id=j.id,
        agent_id=j.agent_id,
        version=j.version,
        product_id=j.product_id,
        status=j.status if j.expires > now() else "expired",
        target=j.target if j.expires > now() else "",
        created=j.created.isoformat(),
        expires=j.expires.isoformat(),
        contacted=False,
    )


def dashboard(c, w, aid):
    a = own(c, w, aid)
    t = s.research_jobs
    return dict(
        agent=a.name,
        product=row(c, s.products, w.tenant, a.product_id).name,
        product_id=a.product_id,
        version=a.published,
        grants=grants(c, w, aid),
        sources=sources(c, w)["items"],
        jobs=[
            brief(j)
            for j in items(c, t, w.tenant, t.c.owner_id == w.actor, t.c.agent_id == aid)
        ],
        provider=catalog.version(c, w.tenant, aid, a.published).config.get(
            "model_provider"
        )
        if a.published
        else None,
    )


def start(c, w, aid, b):
    a = own(c, w, aid)
    g = row(c, s.research_grants, w.tenant, b.grant_id)
    if g.owner_id != w.actor or g.agent_id != aid:
        reject(403, "RESEARCH_GRANT", "尚未获得该助手的企业官网资料研究权限。")
    a, v, src = verify(c, w, g)
    target = url(b.company_url)
    if not within(target, src.base_url):
        reject(422, "RESEARCH_SOURCE", ERR["SOURCE"])
    if v.config.get("model_provider") == "deepseek" and (
        not b.accept_model_cost or not src.policy.get("allow_model_processing")
    ):
        reject(
            422, "RESEARCH_COST", "使用个人模型需确认费用，且来源需允许交由模型处理。"
        )
    t = s.research_jobs
    if (
        len(
            items(
                c, t, w.tenant, t.c.owner_id == w.actor, t.c.status.in_(ACTIVE), limit=4
            )
        )
        >= 3
    ):
        reject(429, "RESEARCH_LIMIT", "已有三项研究在排队或运行，请稍后再试。")
    if (
        len(
            items(
                c,
                t,
                w.tenant,
                t.c.source_id == src.id,
                t.c.created > now() - timedelta(hours=1),
                limit=13,
            )
        )
        >= 12
    ):
        reject(429, "RESEARCH_LIMIT", "本来源每小时最多 12 项，请稍后再试。")
    id = uid("research")
    insert(
        c,
        t,
        w.tenant,
        id,
        owner_id=w.actor,
        agent_id=aid,
        version=a.published,
        product_id=a.product_id,
        source_id=src.id,
        grant_id=g.id,
        target=target,
        input=b.model_dump(exclude={"company_url"}),
        tool_version=VERSION,
        skill_version=SKILL,
        status="queued",
        events=[event("queued")],
        expires=min(src.expires, now() + timedelta(days=src.policy["retention_days"])),
    )
    return 202, dict(id=id, run_id=id, status="queued")


def event(kind, **kw):
    return dict(kind=kind, at=now().isoformat(), **kw)


def detail(c, w, id):
    j = own_job(c, w, id)
    d = brief(j)
    if (
        d["status"] == "expired"
        or row(c, s.research_sources, w.tenant, j.source_id).status != "active"
    ):
        return d | {"status": "expired", "target": "", "events": [], "report": None}
    return d | dict(
        events=j.events,
        report=j.report,
        error=ERR.get(j.error, j.error),
        tool=TOOL,
        tool_version=j.tool_version,
        skill_version=j.skill_version,
    )


def stop(c, w, id):
    j = own_job(c, w, id)
    if j.status not in ACTIVE:
        reject(409, "STATE_CONFLICT", "研究已经结束。")
    update(
        c,
        s.research_jobs,
        w.tenant,
        id,
        status="cancelled",
        lease=None,
        lease_until=None,
        capture=None,
        events=j.events + [event("cancelled")],
        updated=now(),
    )
    return 200, {"id": id, "status": "cancelled"}


def retry(c, w, id):
    j = own_job(c, w, id)
    if j.status not in ("failed", "cancelled") or not j.input or j.expires <= now():
        reject(409, "STATE_CONFLICT", "只可手动重试尚在许可期内的失败或已停止任务。")
    return start(c, w, j.agent_id, Start(**(j.input | {"company_url": j.target})))


def review(c, w, id, b):
    j = own_job(c, w, id)
    if (
        j.status not in ("ready", "revise", "approved")
        or j.expires <= now()
        or not j.report
    ):
        reject(409, "STATE_CONFLICT", "研究结果还不能审核。")
    verify(c, w, row(c, s.research_grants, w.tenant, j.grant_id))
    result = j.report | {"draft": b.draft, "review_status": b.decision}
    update(
        c,
        s.research_jobs,
        w.tenant,
        id,
        report=result,
        status=b.decision,
        events=j.events + [event("review", decision=b.decision)],
        updated=now(),
    )
    return 200, {"id": id, "status": b.decision}


def lead(c, w, id):
    j = own_job(c, w, id)
    if j.status not in ("approved", "saved") or j.expires <= now():
        reject(409, "RESEARCH_REVIEW", "请先人工审核资料和草稿。")
    _, _, src = verify(c, w, row(c, s.research_grants, w.tenant, j.grant_id))
    from sqlalchemy.dialects.postgresql import insert as upsert

    tab = s.research_leads
    key = uid("lead")
    state = "sample" if src.policy["purpose"] == "acceptance_only" else "followup"
    target = j.target.removesuffix("/")
    q = upsert(tab).values(
        tenant=w.tenant,
        id=key,
        owner_id=w.actor,
        product_id=j.product_id,
        target=target,
        job_id=j.id,
        source_id=src.id,
        status=state,
    )
    key = c.scalar(
        q.on_conflict_do_update(
            index_elements=["tenant", "owner_id", "product_id", "target"],
            set_=dict(job_id=j.id, source_id=src.id, status=state),
        ).returning(tab.c.id)
    )
    if j.status != "saved":
        update(
            c,
            s.research_jobs,
            w.tenant,
            id,
            status="saved",
            updated=now(),
            events=j.events + [event("saved", sample=state == "sample")],
        )
    return 200, {"id": key, "status": state, "contacted": False}
