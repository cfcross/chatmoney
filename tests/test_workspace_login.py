import secrets
from datetime import timedelta
from html import unescape
from urllib.parse import urlsplit, parse_qs
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import schema as s, web_identity as web
from app.api.runtime import event_page
from app.db import now, transaction
from app.http import Identity
from scripts.workspace_accounts import prepare

HOST = "https://testserver"
AUTH = "/console/auth/"
ORIGIN = {"Origin": HOST}


def post(client, path, data=None):
    return client.post(AUTH + path, headers=ORIGIN, json=data or {})


def issue(env, t="tob", role="admin"):
    actor = t + "-" + role
    with env.admin.begin() as c:
        return web.issue_setup(c, t, actor, actor, by="fixture")


def enter(client, setup):
    password = secrets.token_urlsafe(22)
    res = post(
        client,
        "activate",
        {k: setup[k] for k in ["username", "setup_code"]} | {"password": password},
    )
    assert res.status_code == 200, res.text
    return password


@pytest.fixture
def browser(env):
    from app.main import app

    with TestClient(app, base_url=HOST) as client:
        yield client


def test_password_cookie_refresh_logout_and_replay(env, browser):
    setup = issue(env)
    pw = enter(browser, setup)
    cookie = browser.cookies.get(web.COOKIE)
    assert (
        cookie
        and browser.get("/console/data/workspace").json()["data"]["tenant"] == "甲企业"
    )
    assert browser.get("/api/model-admin/v1/connections").status_code == 200
    with env.admin.begin() as c:
        account = c.execute(select(s.web_accounts)).mappings().one()
        assert (
            account.password_hash.startswith("$argon2id$")
            and account.setup_hash is None
        )
        assert pw not in account.password_hash
        assert c.scalar(select(s.web_sessions.c.id)) == web.digest(cookie)
        assert setup["setup_code"] not in str(c.execute(select(s.audit)).all())
    assert (
        post(
            browser,
            "activate",
            {
                "username": setup["username"],
                "setup_code": setup["setup_code"],
                "password": pw,
            },
        ).status_code
        == 400
    )
    assert post(browser, "logout").status_code == 200 and not browser.cookies.get(
        web.COOKIE
    )
    assert (
        browser.get(
            "/console/data/workspace", headers={"Cookie": web.COOKIE + "=" + cookie}
        ).status_code
        == 401
    )
    response = post(browser, "login", {"username": "TOB-ADMIN", "password": pw})
    assert response.status_code == 200
    assert all(
        x in response.headers["set-cookie"]
        for x in ["Secure", "HttpOnly", "SameSite=strict", "Path=/"]
    )
    assert response.headers["Cache-Control"] == "private, no-store"
    assert browser.cookies.get(web.COOKIE) != cookie
    assert (
        pw not in response.text
        and cookie not in response.text
        and "token" not in response.text
    )


def test_unauthorized_roles_tenant_scope_and_personal_member(env, browser):
    enter(browser, issue(env))
    members = browser.get(AUTH + "members").json()["data"]["items"]
    assert all(r["id"].startswith("tob-") for r in members)
    assert not any(r["id"].endswith("inbound") for r in members)
    for target in ("toc-business", "tob-inbound", "tob-admin"):
        assert (
            post(
                browser, "members", {"username": "张同学", "existing_actor": target}
            ).status_code
            == 403
        )
    assert (
        post(
            browser, "members", {"username": "张同学", "roles": ["publish"]}
        ).status_code
        == 422
    )
    assert (
        post(browser, "members", {"username": "张同学", "tenant_id": "toc"}).status_code
        == 422
    )
    result = post(browser, "members", {"username": "张同学"})
    assert result.status_code == 200
    invite = result.json()["data"]
    assert post(browser, "members", {"username": "张同学"}).status_code == 409
    enter(browser, invite)
    data = browser.get("/console/data/workspace").json()["data"]
    assert (
        data["tenant"] == "甲企业"
        and data["roles"] == ["business", "own_agents"]
        and not data["manage_members"]
    )
    assert [p["id"] for p in data["products"]] == ["pA1"] and data[
        "actor"
    ] != "tob-business"
    assert browser.get(AUTH + "members").status_code == 403
    assert browser.get("/api/model-admin/v1/connections").json()["data"] == []
    for product, expected in (("pA1", 201), ("pB1", 404)):
        assert (
            browser.post(
                "/api/v1/agents",
                json={"name": "个人助手", "workflow": "sales", "product_id": product},
                headers=ORIGIN | {"Idempotency-Key": "create-" + product},
            ).status_code
            == expected
        )
    # A service-only business identity keeps its original restricted bearer contract.
    assert (
        env.req(
            "POST",
            "/agents",
            "tob-business",
            {"name": "未授权", "workflow": "sales", "product_id": "pA1"},
        ).status_code
        == 403
    )
    assert env.req("GET", "/agents/missing", "tob-admin").status_code == 404


def test_company_switch_session_expiry_account_disable_and_sse(env, browser):
    from app.main import app

    enter(browser, issue(env))
    raw = browser.cookies.get(web.COOKIE)
    w = Identity("tob", "tob-admin", {"observe"}, {"pA1"}, web.digest(raw))
    assert event_page(w, {}, 0) == []
    enter(browser, issue(env, "toc"))
    session = browser.cookies.get(web.COOKIE)
    assert browser.get("/console/data/workspace").json()["data"]["tenant"] == "乙企业"
    assert event_page(w, {}, 0) is None
    with TestClient(app, base_url=HOST) as other:
        assert (
            other.get(
                "/console/data/workspace", headers={"Cookie": web.COOKIE + "=" + raw}
            ).status_code
            == 401
        )
    with env.admin.begin() as c:
        c.execute(
            s.identities.update()
            .where(s.identities.c.tenant == "toc", s.identities.c.id == "toc-admin")
            .values(roles=["business"])
        )
    assert browser.get("/api/model-admin/v1/connections").json()["data"] == []
    with env.admin.begin() as c:
        c.execute(
            s.identities.update()
            .where(s.identities.c.id == "toc-admin")
            .values(active=False)
        )
    assert browser.get("/console/data/workspace").status_code == 401
    with env.admin.begin() as c:
        c.execute(
            s.identities.update()
            .where(s.identities.c.id == "toc-admin")
            .values(active=True)
        )
        c.execute(
            s.web_sessions.update()
            .where(s.web_sessions.c.id == web.digest(session))
            .values(expires=now() - timedelta(minutes=1))
        )
    assert browser.get("/console/data/workspace").status_code == 401


def test_csrf_and_https_required_for_cookie_access(env, browser):
    setup = issue(env)
    payload = {
        "username": setup["username"],
        "password": secrets.token_urlsafe(22),
        "setup_code": setup["setup_code"],
    }
    assert (
        browser.post(
            "http://testserver" + AUTH + "activate",
            headers={"X-Forwarded-Proto": "https", "Origin": "http://testserver"},
            json=payload,
        ).status_code
        == 403
    )
    assert browser.post(AUTH + "activate", json=payload).status_code == 403
    assert (
        browser.post(
            AUTH + "activate",
            headers={"Origin": "https://elsewhere.invalid"},
            json=payload,
        ).status_code
        == 403
    )
    enter(browser, setup)
    assert browser.post(AUTH + "logout", headers={"Origin": "null"}).status_code == 403
    assert (
        browser.post(
            "/api/v1/agents",
            headers={"Origin": "https://elsewhere.invalid", "Idempotency-Key": "csrf"},
            json={"name": "新角色", "workflow": "sales", "product_id": "pA1"},
        ).status_code
        == 403
    )
    assert browser.get("/console/data/workspace").status_code == 200


def test_expired_link_reset_and_rls(env, browser):
    enter(browser, issue(env))
    invite = post(
        browser, "members", {"username": "销售同学", "existing_actor": "tob-business"}
    ).json()["data"]
    with env.admin.begin() as c:
        c.execute(
            s.web_accounts.update()
            .where(s.web_accounts.c.username == "销售同学")
            .values(setup_until=now() - timedelta(days=1))
        )
    from app.main import app

    with TestClient(app, base_url=HOST) as member:
        assert (
            post(
                member,
                "activate",
                {
                    "username": "销售同学",
                    "password": secrets.token_urlsafe(22),
                    "setup_code": invite["setup_code"],
                },
            ).status_code
            == 400
        )
        fresh = post(
            browser,
            "members",
            {"username": "销售同学", "existing_actor": "tob-business"},
        ).json()["data"]
        old_password = enter(member, fresh)
        new = post(
            browser,
            "members",
            {"username": "销售同学", "existing_actor": "tob-business"},
        ).json()["data"]
        assert member.get("/console/data/workspace").status_code == 401
        assert (
            post(
                member, "login", {"username": "销售同学", "password": old_password}
            ).status_code
            == 401
        )
        assert enter(member, new) != old_password
    with transaction("toc") as c:
        assert not c.execute(select(s.web_accounts)).first()
        assert not c.execute(select(s.web_sessions)).first()


def test_unknown_and_wrong_credentials_throttled_without_password_leak(
    env, browser, caplog
):
    password = enter(browser, issue(env))
    post(browser, "logout")
    bad = secrets.token_urlsafe(21)

    def fail(name):
        return post(browser, "login", {"username": name, "password": bad})

    a, b = fail("missing"), fail("tob-admin")
    assert (
        a.status_code == b.status_code == 401 and a.json()["error"] == b.json()["error"]
    )
    for _ in range(9):
        assert fail("tob-admin").status_code == 401
    assert (
        post(
            browser, "login", {"username": "tob-admin", "password": password}
        ).status_code
        == 429
    )
    assert bad not in caplog.text and password not in caplog.text
    assert (
        post(
            browser,
            "login",
            {"username": "tob-admin", "password": bad, "tenant_id": "toc"},
        ).status_code
        == 422
    )


def test_setup_delivery_private_repeat_start_and_admin_recovery(env, browser, tmp_path):
    prepare(HOST + "/console", tmp_path)
    files = sorted(x.name for x in tmp_path.glob("*.html"))
    assert files == ["tob-admin.html", "toc-admin.html"]
    page = tmp_path / "tob-admin.html"
    markup = page.read_text(encoding="utf8")
    prepare(HOST + "/console", tmp_path)
    assert markup == page.read_text(encoding="utf8")
    uri = urlsplit(unescape(re.search(r'<a href="([^"]+#activate\?[^"]+)"', markup)[1]))
    assert not uri.query and uri.path == "/console"
    data = parse_qs(uri.fragment.removeprefix("activate?"))
    pwd = enter(
        browser, {"username": data["account"][0], "setup_code": data["code"][0]}
    )
    prepare(HOST + "/console", tmp_path)
    assert browser.get("/console/data/workspace").status_code == 200
    assert (
        post(browser, "login", {"username": "tob-admin", "password": pwd}).status_code
        == 200
    )
    prepare(HOST + "/console", tmp_path, ("tob", "tob-admin"))
    assert browser.get("/console/data/workspace").status_code == 401
    assert page.read_text(encoding="utf8") != markup


def test_stale_tab_cannot_write_into_the_next_workspace(env, browser):
    enter(browser, issue(env))
    previous = browser.get("/console/data/workspace").json()["data"]["browser_context"]
    enter(browser, issue(env, "toc"))
    response = browser.post(
        AUTH + "members",
        headers=ORIGIN | {"X-Workspace-Context": previous},
        json={"username": "测试成员"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "WORKSPACE_CHANGED"
    )
    assert not any(
        x["username"] == "测试成员"
        for x in browser.get(AUTH + "members").json()["data"]["items"]
    )
    assert (
        browser.post(
            AUTH + "logout", headers=ORIGIN | {"X-Workspace-Context": previous}
        ).status_code
        == 409
    )
    assert browser.get("/console/data/workspace").json()["data"]["tenant"] == "乙企业"
