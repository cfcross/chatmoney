"""One workbench with editable logins and member-owned assistants."""

import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import schema as s, web_identity as web
from app.api.runtime import event_page
from app.db import insert
from app.http import Identity
from app.main import app
from app.team import ADMIN, PRODUCT, TEAM
from app.work import enqueue
from scripts.workspace_accounts import prepare
from tests.conftest import profile
from tests.test_workspace_login import AUTH, HOST, ORIGIN, post


def onboard(manager, client, name):
    fields = dict(username=name, password=secrets.token_urlsafe(22))
    res = post(manager, "members", fields | {"delivery": "password"})
    res.raise_for_status()
    assert res.json()["data"] == {"username": name}
    post(client, "login", fields).raise_for_status()
    return fields["password"]


def edit(client, endpoint, **fields):
    return client.patch(AUTH + endpoint, headers=ORIGIN, json=fields)


def write(client, path, body):
    return client.post(
        "/api/v1/" + path, headers=ORIGIN | {"Idempotency-Key": uuid4().hex}, json=body
    )


def test_first_open_is_one_editable_team_and_existing_password_survives_restart(
    env, tmp_path
):
    page = prepare(HOST + "/console", tmp_path, team_only=True)
    assert list(tmp_path.glob("*.html")) == [page]
    html = page.read_text(encoding="utf8")
    assert 'http-equiv="refresh"' in html and "ChatMoney 团队" in html
    assert prepare(HOST + "/console", tmp_path, team_only=True) == page
    with env.admin.begin() as c:
        challenge = web.issue_setup(c, TEAM, ADMIN, ADMIN, by="test")["setup_code"]
    with TestClient(app, base_url=HOST) as c:
        pwd = secrets.token_urlsafe(22)
        post(
            c,
            "activate",
            dict(
                username=ADMIN, setup_code=challenge, new_username="吴姐", password=pwd
            ),
        ).raise_for_status()
        assert c.get("/console/data/workspace").json()["data"]["username"] == "吴姐"
        assert prepare(HOST + "/console", tmp_path, team_only=True) is None
        assert post(c, "login", dict(username=ADMIN, password=pwd)).status_code == 401
        assert post(c, "login", dict(username="吴姐", password=pwd)).status_code == 200


def test_member_edits_and_manager_reset_keep_identity_and_revoke_old_logins(
    manager, env
):
    with TestClient(app, base_url=HOST) as c, TestClient(app, base_url=HOST) as second:
        pwd = onboard(manager, c, "小明")
        data = c.get("/console/data/workspace").json()["data"]
        actor = data["actor"]
        assert not data["manage_members"]
        assert c.get("/api/model-admin/v1/connections").json()["data"] == []
        assert post(c, "members", {"username": "新增成员"}).status_code == 403
        post(second, "login", dict(username="小明", password=pwd)).raise_for_status()
        assert (
            edit(c, "me", username="明同学", current_password="填错的密码").status_code
            == 400
        )
        assert (
            edit(c, "me", username="工作台管理员", current_password=pwd).status_code
            == 409
        )
        newer = secrets.token_urlsafe(22)
        res = edit(c, "me", username="明同学", password=newer, current_password=pwd)
        res.raise_for_status()
        assert newer not in res.text and pwd not in res.text
        for client in (c, second):
            assert client.get("/console/data/workspace").status_code == 401
        assert post(c, "login", dict(username="小明", password=pwd)).status_code == 401
        post(c, "login", dict(username="明同学", password=newer)).raise_for_status()
        assert edit(c, "members/" + ADMIN, username="改管理员").status_code == 403
        assert (
            edit(manager, "members/toc-admin", username="改乙管理员").status_code == 403
        )
        edit(manager, "members/" + actor, username="明老师").raise_for_status()
        assert c.get("/console/data/workspace").status_code == 401
        post(c, "login", dict(username="明老师", password=newer)).raise_for_status()
        reset = secrets.token_urlsafe(22)
        edit(manager, "members/" + actor, password=reset).raise_for_status()
        assert (
            post(c, "login", dict(username="明老师", password=newer)).status_code == 401
        )
        post(c, "login", dict(username="明老师", password=reset)).raise_for_status()
        assert c.get("/console/data/workspace").json()["data"]["actor"] == actor
        with env.admin.begin() as db:
            stored = db.scalar(
                select(s.web_accounts.c.password_hash).where(
                    s.web_accounts.c.id == actor
                )
            )
            assert web.verify_password(stored, reset)
            audit = str(db.execute(select(s.audit)).all())
            assert all(p not in audit for p in (pwd, newer, reset))


def test_two_members_have_private_assistants_tasks_and_activity(manager, env):
    with env.admin.begin() as db:
        insert(db, s.kbs, TEAM, "kb", product_id=PRODUCT, name="测试资料", latest=1)
        insert(
            db,
            s.docs,
            TEAM,
            "d",
            kb_id="kb",
            title="说明",
            original="测试资料",
            status="ready",
            actor=ADMIN,
        )
        insert(
            db,
            s.snapshots,
            TEAM,
            "kb:1",
            kb_id="kb",
            number=1,
            documents=[{"document_id": "d", "revision": 1}],
            actor=ADMIN,
        )
    spec = profile()
    spec["product_id"] = PRODUCT
    spec["config"].update(company_ref=TEAM + "@1", product_ref=PRODUCT + "@1")
    with (
        TestClient(app, base_url=HOST) as first,
        TestClient(app, base_url=HOST) as other,
    ):
        pwd = onboard(manager, first, "成员甲")
        onboard(manager, other, "成员乙")
        a = write(first, "agents", spec).json()["data"]["agent_id"]
        b = write(other, "agents", spec | {"name": "李的助手"}).json()["data"][
            "agent_id"
        ]
        assert first.get("/api/model-admin/v1/options").status_code == 200
        assert first.get("/api/v1/agents/" + b + "?view=draft").status_code == 404
        assert other.get("/api/v1/agents/" + a + "?view=draft").status_code == 404
        assert (
            write(
                other, "agents/" + a + "/publish", {"expected_revision": 1}
            ).status_code
            == 404
        )
        assert (
            other.patch(
                "/api/v1/agents/" + a,
                headers=ORIGIN | {"Idempotency-Key": "edit"},
                json={"expected_revision": 1, "changes": {"name": "他人的助手"}},
            ).status_code
            == 404
        )
        for client, expected in ((first, {a}), (other, {b}), (manager, {a, b})):
            rows = client.get("/console/data/directory/agents").json()["data"]
            assert {r["id"] for r in rows["items"]} == expected
            assert client.get("/console/data/workspace").json()["data"]["counts"][
                "agents"
            ] == len(expected)
        write(
            first, "agents/" + a + "/publish", {"expected_revision": 1}
        ).raise_for_status()
        task_spec = {"agent_id": a, "agent_version": 1, "purpose": "outreach"}
        tid = write(first, "tasks", task_spec).json()["data"]["task_id"]
        assert write(other, "tasks", task_spec).status_code == 404
        with env.admin.begin() as db:
            run = enqueue(db, TEAM, PRODUCT, "started", task=tid)
        assert first.get("/api/v1/runs/" + run).status_code == 200
        assert other.get("/api/v1/runs/" + run).status_code == 404
        assert other.get("/api/v1/tasks/" + tid).status_code == 404
        for kind in ["tasks", "runs"]:
            assert (
                other.get("/console/data/directory/" + kind).json()["data"]["total"]
                == 0
            )

        def events(client):
            ws = client.get("/console/data/workspace").json()["data"]
            who = Identity(
                TEAM,
                ws["actor"],
                set(ws["roles"]),
                {PRODUCT},
                web.digest(client.cookies[web.COOKIE]),
            )
            return event_page(who, {}, 0)

        assert events(first) and events(other) == []
        edit(first, "me", username="成员改名", current_password=pwd).raise_for_status()
        post(
            first, "login", {"username": "成员改名", "password": pwd}
        ).raise_for_status()
        assert first.get("/api/v1/agents/" + a + "?view=draft").status_code == 200
        assert (
            write(other, "agents/" + a + "/disable", {"reason": "未授权"}).status_code
            == 404
        )


def test_delegated_team_member_can_issue_but_cannot_reset_admin(manager, env):
    with (
        TestClient(app, base_url=HOST) as delegate,
        TestClient(app, base_url=HOST) as newcomer,
    ):
        onboard(manager, delegate, "受托人")
        who = delegate.get("/console/data/workspace").json()["data"]["actor"]
        post(
            manager, "members/" + who + "/issuer", {"enabled": True}
        ).raise_for_status()
        onboard(delegate, newcomer, "普通新成员")
        assert newcomer.get("/console/data/workspace").json()["data"]["roles"] == [
            "business",
            "own_agents",
        ]
        target = newcomer.get("/console/data/workspace").json()["data"]["actor"]
        edit(delegate, "members/" + target, username="改名新成员").raise_for_status()
        assert (
            edit(
                delegate, "members/" + ADMIN, password=secrets.token_urlsafe(22)
            ).status_code
            == 403
        )
        assert (
            post(
                delegate, "members/" + target + "/issuer", {"enabled": True}
            ).status_code
            == 403
        )
        post(
            manager, "members/" + who + "/issuer", {"enabled": False}
        ).raise_for_status()
        assert (
            edit(delegate, "members/" + target, username="再次改名").status_code == 403
        )
