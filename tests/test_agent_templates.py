"""Template selections are private data and cannot become execution permissions."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import schema as s
from app.agent_templates import TemplateContent
from app.db import transaction
from app.main import app
from tests.conftest import profile
from tests.flows import drain, fetch, ready, submit
from tests.model_service import setup_provider
from tests.test_personal_models import (
    assistant_spec,
    data,
    install_product_fixture,
    request,
    save_connection,
)
from tests.test_team_workspace import onboard
from tests.test_workspace_login import HOST

BASE = "/console/manage/templates"
SYS = "sys_prospecting"


def call(env, method, path, body=None, actor="tob-admin", key=None):
    headers = {"Authorization": "Bearer " + env.tokens[actor]}
    if method != "GET":
        headers["Idempotency-Key"] = key or uuid4().hex
    return env.client.request(method, path, headers=headers, json=body)


def content(record):
    return {k: deepcopy(record[k]) for k in TemplateContent.model_fields}


def create_from(env, id=SYS, version=1, config=None):
    return env.req(
        "POST",
        "/agents",
        body={
            "name": "研究测试",
            "product_id": "pA1",
            "workflow": "sales",
            "source_template": {"id": id, "version": version},
            "config": config or {},
        },
    )


def publish(env, id, rev=1):
    return env.req("POST", f"/agents/{id}/publish", body={"expected_revision": rev})


def test_catalog_stable_repeat_read_non_executable(env):
    expected = [
        "sys_" + k
        for k in (
            "prospecting",
            "supplier",
            "brand",
            "software",
            "industry",
            "retention",
        )
    ]
    records = data(call(env, "GET", BASE + "?kind=system"))
    assert records == data(
        call(env, "GET", BASE + "?kind=system", actor="toc-business")
    )
    assert [r["id"] for r in records] == expected
    assert len({r["config"]["goal"] for r in records}) == 6
    for r in records:
        assert (
            r["config"]["persona"]
            and r["audience"]
            and r["materials"]
            and r["config"]["allowed"]
        )
        assert r["version"] == 1 and r["config"]["forbidden"] and r["human_review"]
        assert set(r["config"]) == {
            "persona",
            "tone",
            "goal",
            "allowed",
            "forbidden",
            "handoff_on",
        }
    with env.admin.begin() as c:
        assert all(
            c.scalar(select(func.count()).select_from(t)) == 0
            for t in (
                s.agent_templates,
                s.agents,
                s.versions,
                s.tasks,
                s.runs,
                s.sends,
                s.sessions,
            )
        )
    assert publish(env, SYS).status_code == 404
    assert (
        env.req(
            "POST",
            "/tasks",
            "tob-business",
            dict(agent_id=SYS, agent_version=1, purpose="outreach"),
        ).status_code
        == 404
    )
    assert env.client.get(BASE + "?kind=system").status_code == 401


def test_system_copy_mutation_and_idempotency(env):
    seed = data(call(env, "GET", BASE + "/" + SYS))
    edit = {"expected_version": 1, "content": content(seed)}
    assert call(env, "PATCH", BASE + "/" + SYS, edit).status_code == 403
    assert (
        call(env, "DELETE", BASE + "/" + SYS + "?expected_version=1").status_code == 403
    )
    path = BASE + "/" + SYS + "/copy"
    body = {"name": "自己的配置", "expected_version": 1}
    rec = data(call(env, "POST", path, body, key="copy"))
    assert rec == data(call(env, "POST", path, body, key="copy"))
    assert (
        call(env, "POST", path, body | {"name": "其他"}, key="copy").status_code == 409
    )
    assert data(call(env, "GET", BASE + "?kind=personal"))[0]["name"] == body["name"]
    assert data(call(env, "GET", BASE + "/" + SYS)) == seed


@pytest.fixture
def pair(env, manager):
    with TestClient(app, base_url=HOST) as one, TestClient(app, base_url=HOST) as two:
        onboard(manager, one, "模板甲")
        onboard(manager, two, "模板乙")
        yield one, two


def test_personal_crud_restart_and_private_even_from_admin(env, manager, pair):
    one, two = pair
    template = content(data(one.get(BASE + "/" + SYS)))
    rec = data(request(one, "POST", BASE, template))
    path = BASE + "/" + rec["id"]
    for other in [two, manager]:
        assert data(other.get(BASE + "?kind=personal")) == []
        assert other.get(path).status_code == 404
        assert (
            request(
                other, "PATCH", path, {"expected_version": 1, "content": template}
            ).status_code
            == 404
        )
        assert (
            request(
                other, "POST", path + "/copy", {"expected_version": 1, "name": "复制"}
            ).status_code
            == 404
        )
        assert request(other, "DELETE", path + "?expected_version=1").status_code == 404
        assert (
            request(
                other,
                "POST",
                "/api/v1/agents",
                dict(
                    name="不可用",
                    workflow="sales",
                    product_id="team-default",
                    source_template=rec,
                ),
            ).status_code
            == 404
        )
    assert (
        request(
            one, "POST", BASE, template | {"owner_user_id": "someone-else"}
        ).status_code
        == 422
    )
    edit = {"expected_version": 1, "content": template | {"name": "工业销售"}}
    assert data(request(one, "PATCH", path, edit))["version"] == 2
    assert request(one, "PATCH", path, edit).status_code == 409
    with TestClient(app, base_url=HOST, cookies=one.cookies) as fresh:
        assert data(fresh.get(path))["name"] == "工业销售"
    # Verify storage through an independent DB transaction and its runtime row policy.
    with env.admin.begin() as c:
        row = (
            c.execute(
                select(s.agent_templates).where(s.agent_templates.c.id == rec["id"])
            )
            .mappings()
            .one()
        )
        assert row.content["name"] == "工业销售"
    with transaction(row.tenant, model_actor="another-actor") as c:
        assert not c.scalar(select(s.agent_templates.c.id))
    dup = data(
        request(
            one, "POST", path + "/copy", {"expected_version": 2, "name": "我的副本"}
        )
    )
    assert dup["id"] != rec["id"]
    assert request(one, "DELETE", path + "?expected_version=1").status_code == 409
    assert data(request(one, "DELETE", path + "?expected_version=2"))["deleted"]
    with env.admin.begin() as c:
        assert not c.scalar(
            select(s.agent_templates.c.id).where(s.agent_templates.c.id == rec["id"])
        )
    assert one.get(path).status_code == 404


def test_template_draft_missing_keeps_no_model_and_no_binding(env):
    id = data(create_from(env))["agent_id"]
    draft = data(env.req("GET", "/agents/" + id + "?view=draft"))
    cfg = draft["config"]
    assert (
        "model_provider" not in cfg
        and not cfg["knowledge"]
        and not cfg["tool_allowlist"]
    )
    assert (
        cfg["persona"] == data(call(env, "GET", BASE + "/" + SYS))["config"]["persona"]
    )
    error = publish(env, id).json()["error"]
    assert (
        error["code"] == "TEMPLATE_INCOMPLETE"
        and "模型" in error["message"]
        and "资料" in error["message"]
    )
    r = env.req(
        "PATCH",
        "/agents/" + id,
        body=dict(
            expected_revision=1,
            changes={"config": {"model_provider": "deepseek", "tone": "亲切"}},
        ),
    )
    assert r.status_code == 200
    assert publish(env, id, 2).status_code == 422
    with env.admin.begin() as c:
        assert not c.scalar(select(s.versions.c.id)) and not c.scalar(
            select(s.runs.c.id)
        )


def test_edit_delete_independent_published_snapshot_and_worker(env):
    original = content(data(call(env, "GET", BASE + "/" + SYS)))
    personal = data(call(env, "POST", BASE, original))
    id = data(
        create_from(
            env,
            personal["id"],
            config={
                "model_provider": "mock",
                "knowledge": [{"kb_id": "kb", "snapshot": 1}],
                "tone": "亲切简明",
            },
        )
    )["agent_id"]
    path = BASE + "/" + personal["id"]
    changed = content(data(call(env, "GET", path)))
    changed["config"]["tone"] = "另外一种风格"
    data(call(env, "PATCH", path, {"expected_version": 1, "content": changed}))
    assert create_from(env, personal["id"], 1).status_code == 409
    assert data(publish(env, id))["version"] == 1
    data(call(env, "DELETE", path + "?expected_version=2"))
    assert data(env.req("GET", "/agents/" + id))["config"]["tone"] == "亲切简明"
    _, _, sid = ready(env, agent_id=id)
    rid = data(submit(env, "tob", sid, "我们有20个账号，请给正式报价。"))["run_id"]
    drain()
    result = fetch(env, "tob", rid)
    assert (
        result["result"]["action"] == "handoff"
        and result["result"]["delivery"] == "not_sent"
    )


@pytest.mark.parametrize(
    "condition",
    ["not_registered", "missing_capability", "required_handoff", "revoked_knowledge"],
)
def test_publish_requires_real_grants_approved_sources_and_handoff(env, condition):
    item = content(data(call(env, "GET", BASE + "/" + SYS)))
    cfg = {"model_provider": "mock", "knowledge": [{"kb_id": "kb", "snapshot": 1}]}
    if condition == "not_registered":
        item["requirements"]["tools"] = ["company.profile_read"]
        cfg["tool_allowlist"] = ["company.profile_read"]
    elif condition == "missing_capability":
        cfg["tool_allowlist"] = ["order.lookup"]
        with env.admin.begin() as c:
            c.execute(
                s.accounts.update()
                .where(s.accounts.c.tenant == "tob")
                .values(capabilities=["channel.reply"])
            )
    elif condition == "required_handoff":
        cfg["handoff_on"] = ["ask_human"]
    else:
        with env.admin.begin() as c:
            c.execute(
                s.docs.update()
                .where(s.docs.c.tenant == "tob")
                .values(status="withdrawn")
            )
    source = data(call(env, "POST", BASE, item))
    id = data(create_from(env, source["id"], config=cfg))["agent_id"]
    assert publish(env, id).status_code == 422


def test_optional_knowledge_only_for_declared_templates(env):
    item = content(data(call(env, "GET", BASE + "/" + SYS)))
    item["requirements"]["knowledge"] = False
    source = data(call(env, "POST", BASE, item))
    id = data(create_from(env, source["id"], config={"model_provider": "mock"}))[
        "agent_id"
    ]
    assert publish(env, id).status_code == 201
    old = profile()
    old["config"]["knowledge"] = []
    assert (
        publish(env, data(env.req("POST", "/agents", body=old))["agent_id"]).status_code
        == 422
    )


def test_export_selected_version_is_private_and_has_no_instance_fields(
    env, manager, pair, monkeypatch
):
    one, two = pair
    provider = setup_provider(monkeypatch)
    install_product_fixture(env)
    connection = save_connection(one, provider.keys["a"])
    spec = assistant_spec(connection)
    id = data(request(one, "POST", "/api/v1/agents", spec))["agent_id"]
    data(
        request(
            one, "POST", "/api/v1/agents/" + id + "/publish", {"expected_revision": 1}
        )
    )
    data(
        request(
            one,
            "PATCH",
            "/api/v1/agents/" + id,
            {"expected_revision": 1, "changes": {"config": {"tone": "新风格"}}},
        )
    )
    base = "/console/manage/agents/" + id
    for user in [manager, two]:
        assert (
            user.get(base + "/template-preview?view=published&version=1").status_code
            == 404
        )
    preview = data(one.get(base + "/template-preview?view=published&version=1"))
    assert (
        data(one.get(base + "/template-preview?view=draft&version=2"))["config"]["tone"]
        == "新风格"
    )
    assert one.get(base + "/template-preview?view=draft&version=1").status_code == 409
    assert set(preview["config"]) == {
        "persona",
        "tone",
        "goal",
        "allowed",
        "forbidden",
        "handoff_on",
    }
    tpl = content(data(one.get(BASE + "/" + SYS))) | preview
    tpl["config"]["persona"] = "【产品】的顾问"
    exported = {"view": "published", "version": 1, "content": tpl}
    for user in [manager, two]:
        assert request(user, "POST", base + "/templates", exported).status_code == 404
    rec = data(request(one, "POST", base + "/templates", exported))
    saved = data(one.get(BASE + "/" + rec["id"]))
    assert (
        saved["config"]["tone"] == spec["config"]["tone"]
        and saved["config"]["persona"] == "【产品】的顾问"
    )
    assert connection["id"] not in str(saved) and provider.keys["a"] not in str(saved)
    assert not (
        {
            "knowledge",
            "tool_allowlist",
            "customer_id",
            "messages",
            "memory",
            "runs",
            "logs",
            "model_connection_id",
        }
        & saved["config"].keys()
    )
    dest = {k: v for k, v in spec.items() if k != "config"} | {"source_template": rec}
    new = data(request(one, "POST", "/api/v1/agents", dest))["agent_id"]
    cfg = data(one.get("/api/v1/agents/" + new + "?view=draft"))["config"]
    assert (
        not cfg["knowledge"]
        and not cfg.get("model_connection_id")
        and not cfg["tool_allowlist"]
    )
    assert (
        request(
            two,
            "POST",
            "/api/v1/agents",
            dest
            | {
                "source_template": {"id": SYS, "version": 1},
                "config": {
                    "model_provider": "deepseek",
                    "model_connection_id": connection["id"],
                },
            },
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "extra",
    [
        "api_key",
        "model_connection_id",
        "knowledge",
        "tool_allowlist",
        "customer_id",
        "script",
    ],
)
def test_only_portable_typed_fields_accepted(env, extra):
    item = content(data(call(env, "GET", BASE + "/" + SYS)))
    item["config"][extra] = "must-not-transfer"
    assert call(env, "POST", BASE, item).status_code == 422


def test_blank_agent_stores_sql_null_and_keeps_compatibility(env):
    id = data(env.req("POST", "/agents", body=profile()))["agent_id"]
    with env.admin.begin() as db:
        assert (
            db.scalar(
                select(s.agents.c.id).where(
                    s.agents.c.id == id, s.agents.c.template_origin.is_(None)
                )
            )
            == id
        )
    assert data(publish(env, id))["status"] == "published"


@pytest.mark.parametrize(
    "id",
    [
        "sys_prospecting",
        "sys_supplier",
        "sys_brand",
        "sys_software",
        "sys_industry",
        "sys_retention",
    ],
)
def test_each_builtin_can_work_with_approved_inputs_and_stop_for_a_quote(env, id):
    agent = data(
        create_from(
            env,
            id,
            config={
                "model_provider": "mock",
                "knowledge": [{"kb_id": "kb", "snapshot": 1}],
            },
        )
    )["agent_id"]
    data(publish(env, agent))
    _, _, sid = ready(env, agent_id=agent)
    first = data(submit(env, "tob", sid, "请介绍排班功能"))["run_id"]
    drain()
    result = fetch(env, "tob", first)
    assert (
        result["status"] == "succeeded" and result["result"]["delivery"] == "not_sent"
    )
    unknown = data(submit(env, "tob", sid, "贵公司的未知认证是否有效"))["run_id"]
    drain()
    assert fetch(env, "tob", unknown)["result"]["delivery"] == "not_sent"
    quote = data(submit(env, "tob", sid, "请给正式报价"))["run_id"]
    drain()
    assert fetch(env, "tob", quote)["result"]["action"] == "handoff"
