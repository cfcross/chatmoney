"""Exercise workbench mutations through the same authenticated HTTP routes as the UI."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import knowledge, schema as s
from app.channels import Sender, MockChannel
from app.db import transaction
from app.main import app
from app.team import ADMIN, PRODUCT, TEAM
from app.worker import Worker
from tests.conftest import profile
from tests.flows import drain, fetch, ready, submit
from tests.test_team_workspace import onboard, write
from tests.test_workspace_login import HOST, ORIGIN, AUTH, post


def manage(client, method, path, body=None, *, token=None, key=None):
    headers = {"Idempotency-Key": key or uuid4().hex}
    if token:
        headers["Authorization"] = "Bearer " + token
    else:
        headers.update(ORIGIN)
    return client.request(method, "/console/manage/" + path, headers=headers, json=body)


def ok(response):
    response.raise_for_status()
    return response.json()["data"]


def member_agent(client, name):
    spec = profile()
    spec.update(name=name, product_id=PRODUCT)
    spec["config"].update(
        company_ref=TEAM + "@1", product_ref=PRODUCT + "@1", knowledge=[]
    )
    return ok(write(client, "agents", spec))["agent_id"]


def test_upgrade_preserves_existing_personal_accounts_in_other_workspaces(env):
    import hashlib
    import os
    from alembic import command
    from alembic.config import Config
    from app.db import insert

    assert (
        env.admin.url.database.endswith("_test")
        and os.environ["MIGRATION_DATABASE_URL"]
    )
    config = Config("alembic.ini")
    command.downgrade(config, "0007_agent_owner")
    try:
        # This is the identity shape produced by the previous web account issuer.
        actor = "member_" + uuid4().hex
        with env.admin.begin() as c:
            insert(
                c,
                s.identities,
                "tob",
                actor,
                roles=["business"],
                products=["pA1"],
                token_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
            )
            insert(c, s.web_accounts, "tob", actor, username="升级前的成员")
        command.upgrade(config, "head")
        with env.admin.connect() as c:
            assert c.scalar(
                select(s.identities.c.roles).where(s.identities.c.id == actor)
            ) == ["business", "own_agents"]
            assert c.scalar(
                select(s.identities.c.roles).where(s.identities.c.id == "tob-business")
            ) == ["business"]
            assert (
                c.scalar(
                    select(s.web_accounts.c.username).where(
                        s.web_accounts.c.id == actor
                    )
                )
                == "升级前的成员"
            )
            assert (
                c.scalar(
                    select(s.docs.c.original).where(
                        s.docs.c.tenant == "tob", s.docs.c.id == "d0"
                    )
                )
                == "产品支持排班。"
            )
    finally:
        command.upgrade(config, "head")


def test_admin_knowledge_files_versions_approval_visibility_and_delete(env):
    book = ok(
        env.req(
            "POST", "/knowledge-bases", body={"name": "原目录", "product_id": "pA1"}
        )
    )["kb_id"]
    path = "knowledge-bases/" + book
    directory = "/console/data/directory/books"
    admin = env.tokens["tob-admin"]
    business = env.tokens["tob-business"]

    def mutate(method, suffix="", body=None, **opts):
        return manage(
            env.client,
            method,
            path + suffix,
            body,
            token=opts.pop("token", admin),
            **opts,
        )

    def view(doc, role):
        return env.client.get(
            "/console/data/documents/" + doc,
            headers={"Authorization": "Bearer " + role},
        )

    def upload(title):
        return ok(
            env.req(
                "POST",
                "/" + path + "/documents",
                files={"file": ("说明.md", title.encode(), "text/markdown")},
                data={"title": title},
            )
        )["document_id"]

    def publish(doc, rev, ver):
        return env.req(
            "POST",
            "/" + path + "/publish",
            body={
                "expected_snapshot": ver,
                "documents": [{"document_id": doc, "revision": rev}],
            },
        )

    def retrieve(ver):
        cfg = {"knowledge": [{"kb_id": book, "snapshot": ver}]}
        with transaction("tob") as c:
            return [
                r["body"]
                for r in knowledge.retrieve(
                    c, "tob", "pA1", cfg, "", product_overview=True
                )
            ]

    rename = {"name": "产品资料", "expected_revision": 1}
    assert mutate("PATCH", body=rename, token=business).status_code == 403
    assert mutate("DELETE", token=business).status_code == 403
    assert ok(mutate("PATCH", body=rename))["edit_revision"] == 2
    assert mutate("PATCH", body=rename | {"name": "过期修改"}).status_code == 409
    assert ok(env.req("GET", "/" + path))["name"] == "产品资料"
    old = upload("原文支持红色")
    drain()
    assert view(old, business).status_code == 404
    ok(publish(old, 1, None))
    assert ok(view(old, business))["text"] == "原文支持红色"
    edit = {
        "title": "新说明",
        "text": "新版本支持蓝色",
        "source_note": "新版手册",
        "expected_revision": 1,
    }
    suffix = "/documents/" + old
    for method, body in (("PATCH", edit), ("DELETE", None)):
        assert mutate(method, suffix, body, token=business).status_code == 403
        assert (
            mutate(method, suffix, body, token=env.tokens["toc-admin"]).status_code
            == 404
        )
    result = ok(mutate("PATCH", suffix, edit, key="same-edit"))
    new = result["document_id"]
    assert new != old and result["revision"] == 2
    assert ok(mutate("PATCH", suffix, edit, key="same-edit")) == result
    assert mutate("PATCH", suffix, edit).status_code == 409
    assert mutate("DELETE", suffix).status_code == 409
    assert ok(env.req("GET", "/" + path))["documents"][0]["document_id"] == new
    assert ok(view(new, admin))["source"] == "新版手册"
    assert view(new, business).status_code == 404
    assert retrieve(1) == ["原文支持红色"]
    assert publish(new, 2, 1).status_code == 409
    drain()
    assert view(new, business).status_code == 404
    ok(publish(new, 2, 1))
    assert retrieve(1) == ["原文支持红色"] and retrieve(2) == ["新版本支持蓝色"]
    assert ok(view(new, business))["text"] == "新版本支持蓝色"
    ok(mutate("DELETE", "/documents/" + new))
    assert not retrieve(1) and not retrieve(2)
    assert view(old, admin).status_code == view(new, business).status_code == 404
    assert not ok(env.req("GET", "/" + path))["documents"]
    queued = upload("解析期间删除")
    assert mutate("DELETE", token=env.tokens["toc-admin"]).status_code == 404
    assert ok(mutate("DELETE", key="remove-book"))["deleted"]
    assert ok(mutate("DELETE", key="remove-book"))["deleted"]
    drain()
    assert (
        view(queued, admin).status_code == env.req("GET", "/" + path).status_code == 404
    )
    assert not ok(
        env.client.get(
            directory, params={"id": book}, headers={"Authorization": "Bearer " + admin}
        )
    )["items"]
    with transaction("tob") as c:
        assert (
            c.scalar(select(s.docs.c.status).where(s.docs.c.id == queued)) == "deleted"
        )


def test_publishing_other_files_during_edit_keeps_approved_revision(env):
    path = "/knowledge-bases/kb"
    doc = ok(
        manage(
            env.client,
            "PATCH",
            "knowledge-bases/kb/documents/d0",
            {"expected_revision": 1, "title": "新说明", "text": "修改后的原文"},
            token=env.tokens["tob-admin"],
        )
    )["document_id"]
    extra = ok(
        env.req(
            "POST",
            path + "/documents",
            files={"file": ("新附件.txt", b"manual", "text/plain")},
            data={"title": "附件"},
        )
    )["document_id"]
    drain()
    original, changed, extra_ref = (
        {"document_id": i, "revision": n} for i, n in (("d0", 1), (doc, 2), (extra, 1))
    )

    def publish(ver, refs):
        return env.req(
            "POST",
            path + "/publish",
            body={"expected_snapshot": ver, "documents": refs},
        )

    assert publish(1, [original, changed, extra_ref]).status_code == 422
    ok(publish(1, [original, extra_ref]))
    seen = ok(env.req("GET", path, "tob-business"))["documents"]
    assert {r["document_id"] for r in seen} == {"d0", extra}
    ok(publish(2, [changed, extra_ref]))
    assert publish(3, [original, extra_ref]).status_code == 409
    seen = ok(env.req("GET", path, "tob-business"))["documents"]
    assert {r["file_id"]: r["document_id"] for r in seen} == {"d0": doc, extra: extra}


def test_deleted_sources_cancel_pending_send(env):
    _, _, sid = ready(env, "toc")
    rid = ok(submit(env, "toc", sid, "产品支持排班吗？"))["run_id"]
    seen = []

    def remove_source():
        with transaction("toc") as c:
            seen.append(
                c.scalar(select(s.sends.c.sources).where(s.sends.c.run_id == rid))
            )
        ok(
            manage(
                env.client,
                "DELETE",
                "knowledge-bases/kb/documents/d0",
                token=env.tokens["toc-admin"],
            )
        )

    drain(Worker(sender=Sender(after_intent=remove_source)))
    assert seen == [["d0"]]
    assert fetch(env, "toc", rid)["result"]["delivery"] == "cancelled"
    with transaction("toc") as c:
        assert c.scalar(select(s.sends.c.status)) == "cancelled"
        assert c.execute(select(s.receipts.c.id)).first() is None


def test_own_and_team_management_revisions_and_task_scope(manager, env):
    with (
        TestClient(app, base_url=HOST) as first,
        TestClient(app, base_url=HOST) as other,
    ):
        onboard(manager, first, "销售同学")
        onboard(manager, other, "客服同学")
        a = member_agent(first, "销售草稿")
        b = member_agent(other, "客服草稿")
        for client, aid in ((first, a), (other, b)):
            created = ok(
                write(
                    manager,
                    "knowledge-bases",
                    {"product_id": PRODUCT, "name": "批准库"},
                )
            )
            kid = created["kb_id"]
            added = manager.post(
                "/api/v1/knowledge-bases/" + kid + "/documents",
                headers=ORIGIN | {"Idempotency-Key": uuid4().hex},
                data={"title": "产品"},
                files={"file": ("说明.txt", b"Approved source", "text/plain")},
            )
            doc = ok(added)["document_id"]
            drain()
            ok(
                write(
                    manager,
                    "knowledge-bases/" + kid + "/publish",
                    {
                        "expected_snapshot": None,
                        "documents": [{"document_id": doc, "revision": 1}],
                    },
                )
            )
            assert manage(client, "DELETE", "knowledge-bases/" + kid).status_code == 403
            ok(
                client.patch(
                    "/api/v1/agents/" + aid,
                    headers=ORIGIN | {"Idempotency-Key": uuid4().hex},
                    json={
                        "expected_revision": 1,
                        "changes": {
                            "name": "已修改" + aid[-3:],
                            "config": {"knowledge": [{"kb_id": kid, "snapshot": 1}]},
                        },
                    },
                )
            )
            ok(write(client, "agents/" + aid + "/publish", {"expected_revision": 2}))
        params = {
            "agent_id": a,
            "agent_version": 1,
            "purpose": "outreach",
            "name": "拼豆邀约",
            "note": "第一批",
        }
        task = ok(write(first, "tasks", params))["task_id"]
        sibling = ok(write(first, "tasks", params | {"name": "第二批"}))["task_id"]
        base = "tasks/" + task
        edit = {"name": "工艺店邀约", "note": "等对方确认", "expected_revision": 1}
        assert (
            manage(other, "PATCH", base, edit).status_code
            == manage(other, "DELETE", base).status_code
            == 404
        )
        assert manage(other, "DELETE", "agents/" + a).status_code == 404
        assert manage(first, "DELETE", "agents/" + b).status_code == 404
        directory = "/console/data/directory/tasks"
        assert other.get(directory, params={"agent_id": a}).status_code == 404
        saved = ok(manage(first, "PATCH", base, edit, key="edit-task"))
        assert saved["edit_revision"] == 2
        assert ok(manage(first, "PATCH", base, edit, key="edit-task")) == saved
        assert (
            manage(
                first, "PATCH", base, edit | {"name": "不同"}, key="edit-task"
            ).status_code
            == 409
        )
        assert manage(first, "PATCH", base, edit).status_code == 409
        assert ok(manager.get("/api/v1/" + base))["note"] == "等对方确认"
        renamed = ok(
            manage(
                manager, "PATCH", base, {"expected_revision": 2, "name": "负责人调整"}
            )
        )
        assert renamed["note"] == "等对方确认"
        ok(write(first, base + "/start", {}))
        ok(manage(first, "DELETE", base))
        assert first.get("/api/v1/" + base).status_code == 404
        assert (
            ok(first.get(directory, params={"agent_id": a}))["items"][0]["id"]
            == sibling
        )
        ok(manage(first, "DELETE", "agents/" + a))
        assert not ok(manager.get(directory))["items"]
        assert first.get("/api/v1/tasks/" + sibling).status_code == 404
        assert write(first, "tasks", params).status_code == 404
        assert ok(manager.get("/console/data/directory/agents"))["total"] == 1
        ok(manage(manager, "DELETE", "agents/" + b))
        assert not ok(other.get("/console/data/directory/agents"))["items"]


def test_retired_agent_fences_queued_messages_but_preserves_history(env):
    a, tk, sid = ready(env, "toc")
    rid = ok(submit(env, "toc", sid, "我的订单 O7 发货了吗？"))["run_id"]

    def delete_agent():
        ok(manage(env.client, "DELETE", "agents/" + a, token=env.tokens["toc-admin"]))

    drain(Worker(sender=Sender(after_intent=delete_agent)))
    assert fetch(env, "toc", rid)["status"] == "cancelled"
    path = "/sessions/" + sid
    assert ok(env.req("GET", path, "toc-admin"))["status"] == "ended"
    assert (
        ok(env.req("GET", path + "/messages", "toc-admin"))["items"][0]["text"]
        == "我的订单 O7 发货了吗？"
    )
    assert submit(env, "toc", sid, "后续来信").status_code == 404
    assert (
        env.req(
            "POST", "/tasks/" + tk + "/resume", "toc-admin", {"reason": "尝试恢复"}
        ).status_code
        == 404
    )
    assert env.req("GET", path, "tob-admin").status_code == 404
    with transaction("toc") as c:
        assert c.scalar(select(s.sends.c.status)) == "cancelled"
        assert c.execute(select(s.receipts.c.id)).first() is None


def test_removed_task_preserves_unknown_receipt_and_closes_handoff(env):
    class NoReceipt(MockChannel):
        def accept(self, c, t, v):
            raise TimeoutError()

    a, tk, sid = ready(env, "toc")
    rid = ok(submit(env, "toc", sid, "订单 O7 发货了吗？"))["run_id"]
    drain(Worker(sender=Sender(NoReceipt())))
    assert fetch(env, "toc", rid)["result"]["delivery"] == "unknown"
    ok(env.req("POST", f"/sessions/{sid}/handoff", "toc-admin", {"reason": "核对回执"}))
    # Removing one task must not change its sibling or delete the parent assistant.
    other = ok(
        env.req(
            "POST",
            "/tasks",
            "toc-admin",
            {"agent_id": a, "agent_version": 1, "purpose": "support"},
        )
    )["task_id"]
    ok(manage(env.client, "DELETE", "tasks/" + tk, token=env.tokens["toc-admin"]))
    drain()
    assert fetch(env, "toc", rid)["result"]["delivery"] == "unknown"
    assert ok(env.req("GET", "/tasks/" + other, "toc-admin"))["status"] == "pending"
    assert ok(env.req("GET", f"/sessions/{sid}", "toc-admin"))["handoff_id"] is None
    with transaction("toc") as c:
        assert (
            c.scalar(select(s.handoffs.c.status).where(s.handoffs.c.session_id == sid))
            == "closed"
        )
        assert c.execute(select(s.receipts.c.id)).first() is None


def test_removing_old_task_keeps_new_owner_of_the_same_conversation(env):
    a, tk, sid = ready(env, "toc")
    ok(
        env.req(
            "POST", "/tasks/" + tk + "/stop", "toc-admin", {"reason": "第一次服务结束"}
        )
    )
    _, new_task, new_sid = ready(env, "toc", agent_id=a)
    ok(manage(env.client, "DELETE", "tasks/" + tk, token=env.tokens["toc-admin"]))
    assert ok(env.req("GET", "/sessions/" + sid, "toc-admin"))["status"] == "ended"
    with transaction("toc") as c:
        assert (
            c.scalar(
                select(s.conversations.c.active_session).where(
                    s.conversations.c.id == "cv2"
                )
            )
            == new_sid
        )
    rid = ok(submit(env, "toc", new_sid, "我的订单 O7 发货了吗？"))["run_id"]
    drain()
    assert fetch(env, "toc", rid)["result"]["delivery"] == "accepted"
    assert env.req("GET", "/tasks/" + new_task, "toc-admin").status_code == 200


def test_deleted_member_loses_all_sessions_and_reissued_login_cannot_inherit(
    manager, env
):
    with (
        TestClient(app, base_url=HOST) as first,
        TestClient(app, base_url=HOST) as other,
        TestClient(app, base_url=HOST) as additional,
    ):
        pw = onboard(manager, first, "原账号")
        onboard(manager, other, "代开账号")
        post(
            additional, "login", {"username": "原账号", "password": pw}
        ).raise_for_status()
        aid = member_agent(first, "原账号的助手")
        actor = ok(first.get("/console/data/workspace"))["actor"]
        delegate = ok(other.get("/console/data/workspace"))["actor"]
        ok(post(manager, "members/" + delegate + "/issuer", {"enabled": True}))
        path = AUTH + "members/" + actor
        assert other.delete(path, headers=ORIGIN).status_code == 403
        assert first.delete(path, headers=ORIGIN).status_code == 403
        assert (
            manager.delete(AUTH + "members/" + ADMIN, headers=ORIGIN).status_code == 403
        )
        assert (
            manager.delete(AUTH + "members/toc-admin", headers=ORIGIN).status_code
            == 403
        )
        assert manager.delete(path).status_code == 403
        for _ in range(2):
            assert ok(manager.delete(path, headers=ORIGIN))["deleted"]
        for client in (first, additional):
            assert client.get("/console/data/workspace").status_code == 401
        assert (
            post(first, "login", {"username": "原账号", "password": pw}).status_code
            == 401
        )
        assert all(x["id"] != actor for x in ok(manager.get(AUTH + "members"))["items"])
        assert (
            ok(manager.get("/api/v1/agents/" + aid, params={"view": "draft"}))[
                "agent_id"
            ]
            == aid
        )
        onboard(manager, first, "原账号")
        assert ok(first.get("/console/data/workspace"))["actor"] != actor
        assert (
            first.get("/api/v1/agents/" + aid, params={"view": "draft"}).status_code
            == 404
        )
        with env.admin.connect() as c:
            assert (
                c.scalar(
                    select(s.identities.c.active).where(
                        s.identities.c.tenant == TEAM, s.identities.c.id == actor
                    )
                )
                is False
            )
            assert (
                c.scalar(
                    select(s.web_accounts.c.id).where(
                        s.web_accounts.c.tenant == TEAM, s.web_accounts.c.id == actor
                    )
                )
                is None
            )
