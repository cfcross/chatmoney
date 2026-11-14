"""Exercise real workbench logins in one workspace, with a recording offline model provider."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import schema as s
from app.db import insert, transaction
from app.main import app
from app.team import ADMIN, PRODUCT, TEAM
from tests.conftest import profile
from tests.flows import drain
from tests.model_service import BASE, setup_provider
from tests.test_team_workspace import onboard
from tests.test_workspace_login import AUTH, HOST, ORIGIN


def request(client, method, path, body=None, *, key=None):
    return client.request(
        method,
        path,
        headers=ORIGIN | {"Idempotency-Key": key or uuid4().hex},
        json=body,
    )


def data(response):
    response.raise_for_status()
    return response.json()["data"]


def save_connection(client, key):
    rec = data(
        request(
            client, "POST", BASE + "/connections", {"name": "我的连接", "api_key": key}
        )
    )
    return data(
        request(
            client,
            "POST",
            BASE + "/connections/" + rec["id"] + "/test",
            {"expected_revision": rec["revision"]},
        )
    )


@pytest.fixture
def members(env, manager, monkeypatch):
    upstream = setup_provider(monkeypatch)
    with TestClient(app, base_url=HOST) as member:
        onboard(manager, member, "成员一")
        yield manager, member, upstream


def assistant_spec(rec):
    spec = profile()
    spec["product_id"] = PRODUCT
    spec["config"].update(
        company_ref=TEAM + "@1",
        product_ref=PRODUCT + "@1",
        knowledge=[{"kb_id": "kb", "snapshot": 1}],
        model_provider="deepseek",
        model_connection_id=rec["id"],
    )
    return spec


def install_product_fixture(env):
    with env.admin.begin() as c:
        insert(c, s.kbs, TEAM, "kb", product_id=PRODUCT, name="排班产品", latest=1)
        insert(
            c,
            s.docs,
            TEAM,
            "d",
            kb_id="kb",
            title="授权说明",
            original="支持排班。",
            status="ready",
            actor=ADMIN,
        )
        insert(
            c, s.chunks, TEAM, "chunk", document_id="d", ordinal=1, body="支持排班。"
        )
        insert(
            c,
            s.snapshots,
            TEAM,
            "kb:1",
            kb_id="kb",
            number=1,
            documents=[{"document_id": "d", "revision": 1}],
            actor=ADMIN,
        )
        insert(
            c,
            s.accounts,
            TEAM,
            "mock",
            product_id=PRODUCT,
            capabilities=["channel.reply"],
        )
        for ident in ("1", "2"):
            insert(c, s.customers, TEAM, ident, name="模拟客户" + ident)
            insert(
                c,
                s.conversations,
                TEAM,
                ident,
                customer_id=ident,
                account_id="mock",
                external_ref=ident,
                permitted=True,
            )


def published(client, rec):
    agent = data(request(client, "POST", "/api/v1/agents", assistant_spec(rec)))[
        "agent_id"
    ]
    data(
        request(
            client, "POST", f"/api/v1/agents/{agent}/publish", {"expected_revision": 1}
        )
    )
    return agent


def running_session(client, aid, contact):
    task = data(
        request(
            client,
            "POST",
            "/api/v1/tasks",
            dict(agent_id=aid, agent_version=1, purpose="outreach"),
        )
    )["task_id"]
    data(request(client, "POST", f"/api/v1/tasks/{task}/start", {}))
    session = data(
        request(
            client,
            "POST",
            "/api/v1/sessions",
            dict(
                agent_id=aid, task_id=task, conversation_id=contact, customer_id=contact
            ),
        )
    )
    return task, session["session_id"]


def message(client, sid):
    return request(
        client,
        "POST",
        f"/api/v1/sessions/{sid}/messages",
        dict(external_message_id=uuid4().hex, text="产品支持排班吗？"),
    )


def test_same_team_private_list_options_crud_binding_and_database_policy(env, members):
    manager, member, upstream = members
    first, second = [
        save_connection(c, upstream.keys[k]) for c, k in ((manager, "a"), (member, "b"))
    ]
    assert first["name"] == second["name"] and first["id"] != second["id"]
    upstream.calls.clear()
    for client, mine, other in [(manager, first, second), (member, second, first)]:
        actor = data(client.get("/console/data/workspace"))
        assert actor["manage_models"] and actor["manage_knowledge"] == (
            client is manager
        )
        for endpoint in ["/connections", "/options"]:
            assert data(client.get(BASE + endpoint)) == [mine]
        path = BASE + "/connections/" + other["id"]
        for method, url, payload in [
            ("GET", path + "/usage", None),
            ("POST", path + "/test", {"expected_revision": 2}),
            (
                "PATCH",
                path,
                {"expected_revision": 2, "api_key": upstream.keys["rotated"]},
            ),
            (
                "POST",
                path + "/disable",
                {"expected_revision": 2, "confirm_in_use": True},
            ),
            ("DELETE", path, {"expected_revision": 2}),
        ]:
            assert request(client, method, url, payload).status_code == 404
        assert (
            request(client, "POST", "/api/v1/agents", assistant_spec(other)).status_code
            == 404
        )
        with transaction(TEAM, model_actor=actor["actor"]) as c:
            assert c.scalars(select(s.model_connections.c.id)).all() == [mine["id"]]
            assert (
                c.execute(
                    s.model_connections.update()
                    .where(s.model_connections.c.id == other["id"])
                    .values(name="非法修改")
                ).rowcount
                == 0
            )
    with transaction(TEAM) as c:
        assert c.scalar(select(s.model_connections.c.id)) is None
    assert not upstream.calls


def test_each_paid_run_uses_its_creators_key_and_foreign_work_cannot_spend(
    env, members
):
    manager, member, upstream = members
    install_product_fixture(env)
    first, second = [
        save_connection(c, upstream.keys[k]) for c, k in ((manager, "a"), (member, "b"))
    ]
    a, b = published(manager, first), published(member, second)
    ua, ub = running_session(manager, a, "1"), running_session(member, b, "2")
    draft = data(manager.get(f"/api/v1/agents/{b}?view=draft"))
    assert not draft["model_editable"] and not draft["config"]["model_connection_id"]
    assert second["id"] not in manager.get(f"/api/v1/agents/{b}").text
    assert (
        request(
            manager,
            "PATCH",
            f"/api/v1/agents/{b}",
            {
                "expected_revision": 1,
                "changes": {"config": {"model_connection_id": first["id"]}},
            },
        ).status_code
        == 403
    )
    assert (
        request(
            manager, "POST", f"/api/v1/agents/{b}/publish", {"expected_revision": 1}
        ).status_code
        == 403
    )
    assert (
        request(
            manager,
            "POST",
            "/api/v1/tasks",
            dict(agent_id=b, agent_version=1, purpose="outreach"),
        ).status_code
        == 403
    )
    assert message(manager, ub[1]).status_code == 403
    assert message(member, ua[1]).status_code == 404
    for client, sid, tag in [(manager, ua[1], "a"), (member, ub[1], "b")]:
        upstream.calls.clear()
        rid = data(message(client, sid))["run_id"]
        drain()
        assert data(client.get("/api/v1/runs/" + rid))["status"] == "succeeded"
        assert upstream.calls and all(
            (source, path) == (tag, "/chat/completions")
            for source, path, _ in upstream.calls
        )
    data(
        request(manager, "POST", f"/api/v1/tasks/{ub[0]}/pause", {"reason": "人工核对"})
    )
    assert (
        request(
            manager, "POST", f"/api/v1/tasks/{ub[0]}/resume", {"reason": "错误账号"}
        ).status_code
        == 403
    )
    assert (
        request(
            member, "POST", f"/api/v1/tasks/{ub[0]}/resume", {"reason": "继续处理"}
        ).status_code
        == 202
    )


def test_historical_shared_binding_and_deleted_owner_fail_before_provider(env, members):
    manager, member, upstream = members
    install_product_fixture(env)
    admin_conn, own = [
        save_connection(c, upstream.keys[k]) for c, k in ((manager, "a"), (member, "b"))
    ]
    aid = published(member, own)
    task, sid = running_session(member, aid, "1")
    next_sid = data(
        request(
            member,
            "POST",
            "/api/v1/sessions",
            dict(agent_id=aid, task_id=task, customer_id="2", conversation_id="2"),
        )
    )["session_id"]
    actor = data(member.get("/console/data/workspace"))["actor"]

    def old_reference(rec):
        with env.admin.begin() as c:
            for tab, field, match in [
                (s.agents, "draft", s.agents.c.id == aid),
                (s.versions, "config", s.versions.c.agent_id == aid),
            ]:
                c.execute(
                    tab.update()
                    .where(tab.c.tenant == TEAM, match)
                    .values(
                        model_connection_id=rec["id"],
                        **{
                            field: tab.c[field].concat(
                                {"model_connection_id": rec["id"]}
                            )
                        },
                    )
                )

    old_reference(admin_conn)
    draft = data(member.get(f"/api/v1/agents/{aid}?view=draft"))
    assert (
        draft["model_needs_rebind"] and draft["config"]["model_connection_id"] is None
    )
    upstream.calls.clear()
    for code in ("old_binding", "deleted_owner"):
        if code == "deleted_owner":
            old_reference(own)
            sid = next_sid
        rid = data(message(member, sid))["run_id"]
        if code == "deleted_owner":
            data(request(manager, "DELETE", AUTH + "members/" + actor))
        drain()
        run = data(manager.get("/api/v1/runs/" + rid))
        assert (
            run["status"] == "failed"
            and run["error"]["code"] == "MODEL_CONNECTION_MISSING"
        )
        with env.admin.begin() as c:
            assert c.scalar(select(s.sends.c.id).where(s.sends.c.run_id == rid)) is None
    assert not upstream.calls


def test_paid_queue_without_new_owner_authorization_never_reaches_provider(
    env, members
):
    manager, member, provider = members
    install_product_fixture(env)
    connection = save_connection(member, provider.keys["b"])
    aid = published(member, connection)
    _, sid = running_session(member, aid, "1")
    rid = data(message(member, sid))["run_id"]
    with env.admin.begin() as c:
        c.execute(
            s.runs.update()
            .where(s.runs.c.tenant == TEAM, s.runs.c.id == rid)
            .values(payload=s.runs.c.payload.op("-")("model_owner"))
        )
    provider.calls.clear()
    drain()
    result = data(manager.get("/api/v1/runs/" + rid))
    assert (
        result["status"] == "failed"
        and result["error"]["code"] == "MODEL_RUN_OWNER_REQUIRED"
    )
    assert not provider.calls


def test_existing_encrypted_key_and_audited_owner_survive_upgrade(env, monkeypatch):
    import base64
    import os
    from alembic import command
    from alembic.config import Config
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.llm import credentials
    from app.model_connections import model_call
    from tests.conftest import published as make_old_agent

    provider = setup_provider(monkeypatch)
    cfg = Config("alembic.ini")
    assert env.admin.url.database.endswith("_test")
    aid = make_old_agent(env)
    command.downgrade(cfg, "0008_workspace_management")
    try:
        nonce = os.urandom(12)
        old_key = nonce + AESGCM(credentials.master()).encrypt(
            nonce,
            provider.keys["a"].encode(),
            credentials.context("tob", "old-connection"),
        )
        with env.admin.begin() as c:
            c.execute(
                s.agents.update()
                .where(s.agents.c.tenant == "tob", s.agents.c.id == aid)
                .values(owner_id=None)
            )
            insert(
                c,
                s.model_connections,
                "tob",
                "old-connection",
                name="历史连接",
                created_by="tob-admin",
                default_model="deepseek-flash",
                supported_models=["deepseek-flash"],
                status="ready",
                encrypted_api_key=base64.urlsafe_b64encode(old_key).decode(),
            )
    finally:
        command.upgrade(cfg, "head")
    with env.admin.begin() as c:
        assert (
            c.scalar(select(s.agents.c.owner_id).where(s.agents.c.id == aid))
            == "tob-admin"
        )
    binding = dict(
        model_provider="deepseek",
        model_connection_id="old-connection",
        model_name="deepseek-flash",
    )
    with model_call("tob", "tob-admin", binding) as model:
        assert model.key == provider.keys["a"]
