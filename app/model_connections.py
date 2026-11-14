"""Store personal credentials and resolve the owner of each paid assistant call."""

from contextlib import contextmanager
from sqlalchemy import select
from pydantic import ValidationError
from app import schema as s
from app.contracts import ModelParameters
from app.db import insert, now, reject, uid, update, transaction
from app.llm import DeepSeek, MockLLM, ModelError
from app.llm import credentials
from app.llm.deepseek import DEFAULT_MODEL, ERROR_MESSAGES, SUPPORTED_MODELS
from app.settings import settings

table = s.model_connections
PUBLIC = (
    "id",
    "name",
    "provider",
    "default_model",
    "supported_models",
    "status",
    "revision",
    "last_tested_at",
    "last_error_code",
    "created_by",
    "created",
    "updated",
)


def public(rec):
    data = {key: rec[key] for key in PUBLIC}
    for key in ("created", "updated", "last_tested_at"):
        data[key] = data[key].isoformat() if data[key] else None
    data["error_message"] = ERROR_MESSAGES.get(data["last_error_code"])
    return data


def list_connections(c, tenant, owner, *, ready_only=False):
    query = select(*(table.c[name] for name in PUBLIC)).where(
        table.c.tenant == tenant, table.c.created_by == owner
    )
    if ready_only:
        query = query.where(table.c.status == "ready")
    return [public(rec) for rec in c.execute(query.order_by(table.c.name)).mappings()]


def read(c, tenant, owner, id, locked=False):
    query = select(table).where(
        table.c.tenant == tenant, table.c.created_by == owner, table.c.id == id
    )
    rec = c.execute(query.with_for_update() if locked else query).mappings().first()
    if rec is None:
        reject(404, "NOT_FOUND", "当前账号没有这条模型连接。")
    return rec


def unique_name(c, tenant, owner, name, except_id=None):
    query = select(table.c.id).where(
        table.c.tenant == tenant, table.c.created_by == owner, table.c.name == name
    )
    if except_id:
        query = query.where(table.c.id != except_id)
    if c.scalar(query):
        reject(409, "CONNECTION_NAME_TAKEN", "你的账号已有同名连接，请换一个名称。")


def create(c, w, body):
    unique_name(c, w.tenant, w.actor, body.name)
    id = uid("mc")
    insert(
        c,
        table,
        w.tenant,
        id,
        name=body.name,
        created_by=w.actor,
        default_model=DEFAULT_MODEL,
        encrypted_api_key=credentials.seal(
            w.tenant, id, w.actor, body.api_key.get_secret_value()
        ),
    )
    return 201, public(read(c, w.tenant, w.actor, id))


def check_revision(rec, revision):
    if rec.revision != revision:
        reject(409, "REVISION_CONFLICT", "连接已被其他人更新，请刷新后再操作。")


def revise(c, w, id, body):
    rec = read(c, w.tenant, w.actor, id, True)
    check_revision(rec, body.expected_revision)
    changes = dict(revision=rec.revision + 1, updated=now())
    if body.name is not None:
        unique_name(c, w.tenant, w.actor, body.name, id)
        changes["name"] = body.name
    if body.api_key is not None:
        if rec.status == "disabled":
            reject(409, "CONNECTION_DISABLED", "此连接已停用，请新建连接。")
        changes.update(
            encrypted_api_key=credentials.seal(
                w.tenant, id, w.actor, body.api_key.get_secret_value()
            ),
            status="pending",
            supported_models=[],
            last_tested_at=None,
            last_error_code=None,
        )
    update(c, table, w.tenant, id, **changes)
    return 200, public(read(c, w.tenant, w.actor, id))


def test_connection(c, w, id, revision):
    rec = read(c, w.tenant, w.actor, id, True)
    check_revision(rec, revision)
    if rec.status == "disabled":
        reject(409, "CONNECTION_DISABLED", "此连接已停用，请新建连接。")
    changes = dict(last_tested_at=now(), updated=now(), revision=rec.revision + 1)
    try:
        client = DeepSeek(
            rec.default_model, credentials.unseal(rec), min(5, settings().model_timeout)
        )
        models = client.available_models()
        changes.update(
            status="ready",
            supported_models=models,
            last_error_code=None,
            default_model=rec.default_model
            if rec.default_model in models
            else models[0],
        )
    except ModelError as error:
        changes.update(status="failed", supported_models=[], last_error_code=error.code)
    update(c, table, w.tenant, id, **changes)
    return 200, public(read(c, w.tenant, w.actor, id))


def usage(c, w, id):
    read(c, w.tenant, w.actor, id)
    a, v = s.agents, s.versions
    drafts = c.execute(
        select(a.c.id, a.c.name).where(
            a.c.tenant == w.tenant,
            a.c.owner_id == w.actor,
            a.c.model_connection_id == id,
        )
    ).all()
    items = {
        i.id: dict(agent_id=i.id, name=i.name, draft=True, versions=[]) for i in drafts
    }
    published = c.execute(
        select(v.c.agent_id, v.c.name, v.c.number)
        .join(a, (a.c.tenant == v.c.tenant) & (a.c.id == v.c.agent_id))
        .where(
            v.c.tenant == w.tenant,
            a.c.owner_id == w.actor,
            v.c.model_connection_id == id,
        )
        .order_by(v.c.number)
    ).all()
    for i in published:
        entry = items.setdefault(
            i.agent_id, dict(agent_id=i.agent_id, name=i.name, draft=False, versions=[])
        )
        entry["versions"].append(i.number)
    return list(items.values())


def disable(c, w, id, body):
    rec = read(c, w.tenant, w.actor, id, True)
    check_revision(rec, body.expected_revision)
    users = usage(c, w, id)
    if users and not body.confirm_in_use:
        reject(
            409,
            "CONNECTION_IN_USE",
            f"有 {len(users)} 位助手引用此连接，停用前请确认影响。",
        )
    update(
        c,
        table,
        w.tenant,
        id,
        status="disabled",
        revision=rec.revision + 1,
        updated=now(),
    )
    return 200, public(read(c, w.tenant, w.actor, id))


def remove(c, w, id, revision):
    rec = read(c, w.tenant, w.actor, id, True)
    check_revision(rec, revision)
    # Historical versions can still refer to this key, even where a former shared binding is blocked.
    if any(
        c.scalar(
            select(ref.c.id)
            .where(ref.c.tenant == w.tenant, ref.c.model_connection_id == id)
            .limit(1)
        )
        for ref in (s.agents, s.versions)
    ):
        reject(
            409,
            "CONNECTION_IN_USE",
            "草稿或已发布版本仍然引用此连接，可以停用，无法删除。",
        )
    c.execute(
        table.delete().where(
            table.c.tenant == w.tenant, table.c.created_by == w.actor, table.c.id == id
        )
    )
    return 200, {"id": id, "deleted": True}


def binding(c, tenant, owner, cfg):
    """Normalize references and resolve the default model before snapshotting the draft."""
    provider = cfg.get("model_provider")
    connection_id = cfg.get("model_connection_id")
    model = cfg.get("model_name")
    if provider not in ("mock", "deepseek") or (
        provider == "mock" and (connection_id or model)
    ):
        reject(
            422, "INVALID_MODEL_BINDING", "请选择模拟模型，或你自己的 DeepSeek 连接。"
        )
    if cfg.get("model_params") is not None:
        try:
            params = ModelParameters.model_validate(cfg["model_params"])
        except ValidationError:
            reject(422, "INVALID_MODEL_BINDING", "模型参数超出允许范围。")
        cfg["model_params"] = params.model_dump(exclude_none=True)
    if provider == "mock":
        cfg.update(model_connection_id=None, model_name=None)
        return
    if not isinstance(connection_id, str) or not connection_id:
        reject(422, "INVALID_MODEL_BINDING", "请选择当前账号已经验证的 DeepSeek 连接。")
    rec = read(c, tenant, owner, connection_id)
    if rec.status != "ready":
        reject(409, "CONNECTION_NOT_READY", "请先测试连接，确认可用后再选择或发布。")
    model = model if model is not None else rec.default_model
    if model not in SUPPORTED_MODELS or model not in rec.supported_models:
        reject(422, "MODEL_UNSUPPORTED", ERROR_MESSAGES["MODEL_UNSUPPORTED"])
    cfg["model_name"] = model


def bound_row(c, tenant, owner, cfg):
    if cfg["model_provider"] != "deepseek":
        raise ModelError("MODEL_UNSUPPORTED")
    rec = (
        c.execute(
            select(table)
            .join(
                s.identities,
                (s.identities.c.tenant == table.c.tenant)
                & (s.identities.c.id == table.c.created_by),
            )
            .where(
                table.c.tenant == tenant,
                table.c.created_by == owner,
                s.identities.c.active,
                table.c.id == cfg.get("model_connection_id"),
            )
            .with_for_update(read=True)
        )
        .mappings()
        .first()
    )
    if not rec:
        raise ModelError("MODEL_CONNECTION_MISSING")
    if rec.status != "ready":
        raise ModelError(
            "MODEL_CONNECTION_DISABLED"
            if rec.status == "disabled"
            else rec.last_error_code or "MODEL_CONNECTION_UNVERIFIED"
        )
    name = cfg.get("model_name")
    if name not in SUPPORTED_MODELS or name not in rec.supported_models:
        raise ModelError("MODEL_UNSUPPORTED")
    return rec


@contextmanager
def model_call(tenant, owner, cfg, override=None):
    """Hold only this credential's shared lock across one request, including streaming."""
    provider = cfg.get("model_provider")
    if provider is None and settings().llm_backend == "deepseek":
        raise ModelError("MODEL_PERSONAL_CONNECTION_REQUIRED")
    if provider in (None, "mock"):
        yield override or MockLLM()
        return
    if not owner:
        raise ModelError("MODEL_PERSONAL_CONNECTION_REQUIRED")
    with transaction(tenant, model_actor=owner) as c:
        rec = bound_row(c, tenant, owner, cfg)
        client = DeepSeek(
            cfg["model_name"], credentials.unseal(rec), settings().model_timeout
        )
        try:
            yield client
            return
        except ModelError as err:
            failure = err
    # Do not invalidate a concurrently rotated or re-tested credential.
    if failure.code in {"MODEL_CREDENTIALS", "MODEL_BALANCE"}:
        with transaction(tenant, model_actor=owner) as c:
            c.execute(
                table.update()
                .where(
                    table.c.tenant == tenant,
                    table.c.created_by == owner,
                    table.c.id == rec.id,
                    table.c.revision == rec.revision,
                )
                .values(
                    status="failed",
                    last_error_code=failure.code,
                    revision=rec.revision + 1,
                    updated=now(),
                )
            )
    raise failure
