from pydantic import Field, ValidationError
from sqlalchemy import select

from app import agent_templates
from app import schema as s
from app.contracts import Config, KBRef
from app.db import insert, reject, row, uid, update
from app.model_connections import binding
from app.policies import REGISTRY

RULES = {ref: (workflow, set(signals)) for ref, (workflow, signals) in REGISTRY.items()}
TOOL_NAMES = {"order.lookup", "channel.reply"}


class OptionalKnowledgeConfig(Config):
    knowledge: list[KBRef] = Field(max_length=16)


def product_tools(c, tenant, product_ids):
    grants = {p: set() for p in product_ids}
    for product, capabilities in c.execute(
        select(s.accounts.c.product_id, s.accounts.c.capabilities).where(
            s.accounts.c.tenant == tenant,
            s.accounts.c.product_id.in_(product_ids),
            s.accounts.c.enabled,
        )
    ):
        grants[product].update(TOOL_NAMES.intersection(capabilities))
    return {product: sorted(names) for product, names in grants.items()}


def draft_model(c, w, cfg, origin):
    if (
        origin
        and cfg.get("model_provider") in (None, "deepseek")
        and not cfg.get("model_connection_id")
    ):
        if cfg.get("model_name") or cfg.get("model_params"):
            reject(422, "INVALID_MODEL_BINDING", "请先选择模型连接，再设置模型参数。")
        return
    binding(c, w.tenant, w.actor, cfg)


def template_checks(c, w, product_id, cfg, origin):
    req = origin["requirements"]
    missing = []
    if cfg.get("model_provider") not in ("mock", "deepseek") or (
        cfg.get("model_provider") == "deepseek" and not cfg.get("model_connection_id")
    ):
        missing.append("选择自己的模型连接，或明确选择模拟演示")
    if req["knowledge"] and not cfg.get("knowledge"):
        missing.append("选择已批准并发布的产品资料")
    if not cfg.get("allowed"):
        missing.append("填写可回答的内容")
    if not set(req["handoff"]).issubset(cfg.get("handoff_on") or []):
        missing.append("勾选模板要求的转人工条件")
    if not set(req["tools"]).issubset(
        TOOL_NAMES.intersection(cfg.get("tool_allowlist") or [])
    ):
        missing.append("确认必需工具已接入并获得授权")
    if set(cfg["tool_allowlist"]) - set(
        product_tools(c, w.tenant, [product_id])[product_id]
    ):
        missing.append("只选择本产品已授权的工具")
    if missing:
        reject(422, "TEMPLATE_INCOMPLETE", "发布前还需：" + "；".join(missing))


def allowed_draft(config):
    if set(config) - Config.model_fields.keys():
        reject(422, "INVALID_PROFILE", "未知配置字段")


def agent(c, w, id, *, history=False):
    a = row(c, s.agents, w.tenant, id)
    w.product(a.product_id)
    if (a.deleted_at and not history) or (w.personal_agents and a.owner_id != w.actor):
        reject(404, "NOT_FOUND", "助手不存在或不可见")
    return a


def own_tasks(w):
    return (
        select(s.tasks.c.id)
        .join(
            s.agents,
            (s.agents.c.tenant == s.tasks.c.tenant)
            & (s.agents.c.id == s.tasks.c.agent_id),
        )
        .where(s.tasks.c.tenant == w.tenant, s.agents.c.owner_id == w.actor)
        .correlate(None)
    )


def own_sessions(w):
    return (
        select(s.sessions.c.id)
        .where(s.sessions.c.tenant == w.tenant, s.sessions.c.task_id.in_(own_tasks(w)))
        .correlate(None)
    )


def own_activity(w, table):
    return table.c.task_id.in_(own_tasks(w)) | table.c.session_id.in_(own_sessions(w))


def version(c, t, id, n):
    v = (
        c.execute(
            select(s.versions).where(
                s.versions.c.tenant == t,
                s.versions.c.agent_id == id,
                s.versions.c.number == n,
            )
        )
        .mappings()
        .first()
    )
    if not v:
        reject(404, "NOT_FOUND", "角色版本不可见")
    return v


def require_model_operator(c, w, a, version_no):
    cfg = version(c, w.tenant, a.id, version_no).config
    if cfg.get("model_provider") == "deepseek" and a.owner_id != w.actor:
        reject(
            403,
            "MODEL_OWNER_ONLY",
            "只有助手创建者可以启动使用自己 DeepSeek 额度的任务。",
        )


def visible_config(c, w, a, cfg):
    visible = dict(cfg)
    own = a.owner_id == w.actor
    connection_id = cfg.get("model_connection_id")
    rebind = False
    if connection_id:
        permitted = own and c.scalar(
            select(s.model_connections.c.id).where(
                s.model_connections.c.tenant == w.tenant,
                s.model_connections.c.created_by == w.actor,
                s.model_connections.c.id == connection_id,
            )
        )
        if not permitted:
            visible["model_connection_id"] = None
            rebind = own
    return dict(config=visible, model_editable=own, model_needs_rebind=rebind)


def create(c, w, b):
    w.product(b.product_id)
    product = row(c, s.products, w.tenant, b.product_id)
    origin = None
    cfg = dict(b.config)
    if b.source_template:
        origin, defaults = agent_templates.apply(c, w, b.source_template, b.workflow)
        cfg = (
            dict(
                company_ref=c.scalar(
                    select(s.tenants.c.company_ref).where(s.tenants.c.id == w.tenant)
                ),
                product_ref=product.version_ref,
                knowledge=[],
                tool_allowlist=[],
                policy_ref=next(
                    ref for ref, (flow, _) in RULES.items() if flow == b.workflow
                ),
            )
            | defaults
            | cfg
        )
    else:
        cfg.setdefault("model_provider", "mock")
    allowed_draft(cfg)
    draft_model(c, w, cfg, origin)
    id = insert(
        c,
        s.agents,
        w.tenant,
        uid("a"),
        name=b.name,
        owner_id=w.actor,
        workflow=b.workflow,
        product_id=b.product_id,
        draft=cfg,
        template_origin=origin,
        model_connection_id=cfg.get("model_connection_id"),
    )
    return 201, {"agent_id": id, "status": "draft", "draft_revision": 1}


def get(c, w, id, view, n):
    a = agent(c, w, id)
    origin = a.template_origin
    if (
        origin
        and origin["id"] not in agent_templates.BUILTINS
        and a.owner_id != w.actor
    ):
        origin = None
    if view and n:
        reject(422, "VALIDATION_ERROR", "view 和 version 不能同时指定")
    if view == "draft":
        w.require("config", "publish", "own_agents")
        return 200, dict(
            agent_id=id,
            status="disabled" if a.disabled else "draft",
            draft_revision=a.revision,
            source_template=origin,
            **visible_config(c, w, a, a.draft),
            name=a.name,
            workflow=a.workflow,
            product_id=a.product_id,
        )
    v = version(c, w.tenant, id, n or a.published)
    return 200, dict(
        agent_id=id,
        status="disabled" if a.disabled else "published",
        version=v.number,
        source_template=origin,
        name=v.name,
        workflow=a.workflow,
        product_id=a.product_id,
        **visible_config(c, w, a, v.config),
    )


def revise(c, w, id, b):
    a = agent(c, w, id)
    if a.disabled:
        reject(409, "STATE_CONFLICT", "角色已停用")
    if a.revision != b.expected_revision:
        reject(409, "REVISION_CONFLICT", "草稿已变更")
    cfg = a.draft | (b.changes.config or {})
    allowed_draft(cfg)
    model_changes = {k for k in (b.changes.config or {}) if k.startswith("model_")}
    if model_changes and a.owner_id != w.actor:
        reject(403, "MODEL_OWNER_ONLY", "模型设置只能由助手创建者修改。")
    if a.owner_id == w.actor and ("model_provider" in cfg or model_changes):
        draft_model(c, w, cfg, a.template_origin)
    update(
        c,
        s.agents,
        w.tenant,
        id,
        name=b.changes.name or a.name,
        draft=cfg,
        model_connection_id=cfg.get("model_connection_id"),
        revision=a.revision + 1,
    )
    return 200, dict(agent_id=id, status="draft", draft_revision=a.revision + 1)


def validate_profile(c, w, a, config):
    profile_type = OptionalKnowledgeConfig if a.template_origin else Config
    try:
        cfg = profile_type.model_validate(config)
    except ValidationError:
        reject(422, "INVALID_PROFILE", "发布配置缺少必要资料")
    if a.template_origin:
        template_checks(c, w, a.product_id, cfg.model_dump(), a.template_origin)
    company = c.execute(
        select(s.tenants.c.company_ref).where(s.tenants.c.id == w.tenant)
    ).scalar_one()
    product = row(c, s.products, w.tenant, a.product_id)
    reg = RULES.get(cfg.policy_ref)
    if (
        (cfg.research_tools and a.workflow != "sales")
        or cfg.company_ref != company
        or cfg.product_ref != product.version_ref
        or not reg
        or reg[0] != a.workflow
        or set(cfg.handoff_on) - reg[1]
        or set(cfg.tool_allowlist) - TOOL_NAMES
    ):
        reject(422, "INVALID_PROFILE", "企业、产品、规则或工具引用无效")
    for k in cfg.knowledge:
        kb = row(c, s.kbs, w.tenant, k.kb_id)
        snap = (
            c.execute(
                select(s.snapshots).where(
                    s.snapshots.c.tenant == w.tenant,
                    s.snapshots.c.kb_id == kb.id,
                    s.snapshots.c.number == k.snapshot,
                )
            )
            .mappings()
            .first()
        )
        if (
            not snap
            or kb.deleted_at
            or kb.product_id != a.product_id
            or any(
                row(c, s.docs, w.tenant, d["document_id"]).status != "ready"
                for d in snap.documents
            )
        ):
            reject(422, "INVALID_PROFILE", "资料未批准或已撤回")
    if cfg.model_provider == "deepseek" and a.owner_id != w.actor:
        reject(
            403, "MODEL_OWNER_ONLY", "请由助手创建者绑定并发布自己的 DeepSeek 连接。"
        )
    if "model_provider" in config:
        binding(c, w.tenant, w.actor, config)


def publish(c, w, id, b):
    a = agent(c, w, id)
    if a.disabled:
        reject(409, "PROFILE_DISABLED", "角色已停用")
    if a.revision != b.expected_revision:
        reject(409, "REVISION_CONFLICT", "草稿已变更")
    validate_profile(c, w, a, a.draft)
    n = a.published + 1
    insert(
        c,
        s.versions,
        w.tenant,
        uid("av"),
        agent_id=id,
        number=n,
        name=a.name,
        config=a.draft,
        model_connection_id=a.draft.get("model_connection_id"),
        actor=w.actor,
    )
    update(c, s.agents, w.tenant, id, published=n)
    return 201, dict(
        agent_id=id, version=n, status="published", draft_revision=a.revision
    )


def disable(c, w, id, reason):
    a = agent(c, w, id)
    if a.disabled:
        reject(409, "STATE_CONFLICT", "角色已停用")
    update(c, s.agents, w.tenant, id, disabled=True)
    from app.handoff import cancel_automatic

    active = c.execute(
        select(s.sessions.c.id)
        .join(
            s.tasks,
            (s.sessions.c.tenant == s.tasks.c.tenant)
            & (s.sessions.c.task_id == s.tasks.c.id),
        )
        .where(s.sessions.c.tenant == w.tenant, s.tasks.c.agent_id == id)
    )
    for sid in active.scalars():
        cancel_automatic(c, w.tenant, sid, "profile_disabled")
    insert(
        c,
        s.audit,
        w.tenant,
        uid("au"),
        actor=w.actor,
        operation="disable",
        resource=id,
        detail={"reason": reason},
    )
    return 200, dict(agent_id=id, status="disabled", auto_allowed=False)
