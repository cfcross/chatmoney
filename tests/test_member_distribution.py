"""Local distribution uses the same login, tenant and product boundary as the workbench."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import schema as s, web_identity as web
from app.db import now
from app.main import app
from tests.test_workspace_login import AUTH, HOST, enter, issue, post


@pytest.fixture
def admin(env):
    with TestClient(app, base_url=HOST) as client:
        enter(client, issue(env))
        yield client


def distribute(admin, name, existing=None):
    body = {"username": name, "delivery": "temporary_password"}
    if existing:
        body["existing_actor"] = existing
    result = post(admin, "members", body)
    assert result.status_code == 200
    return result.json()["data"]["initial_password"]


def member_id(admin, name):
    rows = admin.get(AUTH + "members").json()["data"]["items"]
    return next(row["id"] for row in rows if row["username"] == name)


def credentials(name, initial, new=None):
    fields = {"username": name, "password": initial}
    if new is not None:
        fields["new_password"] = new
    return fields


def test_initial_password_enforces_private_first_sign_in(env, admin, caplog):
    name = "新成员一"
    initial = distribute(admin, name)
    assert initial not in admin.get(AUTH + "members").text
    assert initial not in admin.get("/console/data/workspace").text
    with TestClient(app, base_url=HOST) as member:
        assert member.get("/accounts").status_code == 200
        for password in ["不正确", ""]:
            assert post(member, "login", credentials(name, password)).status_code in (
                401,
                422,
            )
        res = post(member, "login", credentials(name, initial))
        assert res.status_code == 200 and res.json()["data"] == {
            "signed_in": False,
            "password_change_required": True,
        }
        assert not res.headers.get("set-cookie") and not member.cookies.get(web.COOKIE)
        assert member.get("/console/data/workspace").status_code == 401
        assert (
            post(
                member,
                "activate",
                {
                    "username": name,
                    "password": secrets.token_urlsafe(18),
                    "setup_code": initial,
                },
            ).status_code
            == 400
        )
        for password in [initial, "太短"]:
            assert (
                post(member, "login", credentials(name, initial, password)).status_code
                == 422
            )
        new = secrets.token_urlsafe(22)
        with env.admin.begin() as c:
            account = (
                c.execute(
                    select(s.web_accounts).where(s.web_accounts.c.username == name)
                )
                .mappings()
                .one()
            )
            assert (
                account.password_hash is None
                and account.setup_hash == "initial:" + web.digest(initial)
            )
        assert (
            post(member, "login", credentials(name, initial, new)).json()["data"][
                "signed_in"
            ]
            is True
        )
        w = member.get("/console/data/workspace").json()["data"]
        assert (
            w["tenant"] == "甲企业"
            and w["roles"] == ["business", "own_agents"]
            and not w["manage_members"]
        )
        post(member, "logout")
        assert post(member, "login", credentials(name, initial)).status_code == 401
        assert post(member, "login", credentials(name, new)).status_code == 200
        assert (
            post(
                member, "login", credentials(name, new, secrets.token_urlsafe(20))
            ).status_code
            == 401
        )
    with env.admin.begin() as c:
        account = (
            c.execute(select(s.web_accounts).where(s.web_accounts.c.username == name))
            .mappings()
            .one()
        )
        assert account.setup_hash is None and web.verify_password(
            account.password_hash, new
        )
        audit = str(c.execute(select(s.audit)).all())
        assert all(secret not in audit + caplog.text for secret in (initial, new))


def test_reissue_expiry_and_single_use_race(env, admin):
    name = "新成员二"
    old = distribute(admin, name)
    actor = member_id(admin, name)
    with env.admin.begin() as c:
        c.execute(
            s.web_accounts.update()
            .where(s.web_accounts.c.username == name)
            .values(setup_until=now() - timedelta(minutes=1))
        )
    with TestClient(app, base_url=HOST) as member:
        assert post(member, "login", credentials(name, old)).status_code == 401
        current = distribute(admin, name, actor)
        assert (
            current != old
            and post(member, "login", credentials(name, old)).status_code == 401
        )

    def redeem(_):
        with TestClient(app, base_url=HOST) as client:
            return post(
                client, "login", credentials(name, current, secrets.token_urlsafe(21))
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(redeem, range(2))) == [200, 401]
    with env.admin.begin() as c:
        assert (
            len(
                c.execute(
                    select(s.web_sessions).where(s.web_sessions.c.actor == actor)
                ).all()
            )
            == 1
        )
    distribute(admin, name, actor)
    with env.admin.begin() as c:
        assert not c.scalar(
            select(s.web_sessions.c.id).where(s.web_sessions.c.actor == actor)
        )


def test_delegate_can_issue_only_ordinary_accounts_and_revokes_immediately(env, admin):
    name = "发放同学"
    tmp = distribute(admin, name)
    delegate_id = member_id(admin, name)
    with (
        TestClient(app, base_url=HOST) as delegate,
        TestClient(app, base_url=HOST) as other,
    ):
        post(
            delegate, "login", credentials(name, tmp, secrets.token_urlsafe(22))
        ).raise_for_status()
        enter(other, issue(env, "toc"))
        grant = "members/" + delegate_id + "/issuer"
        assert post(other, grant, {"enabled": True}).status_code == 403
        assert post(delegate, grant, {"enabled": True}).status_code == 403
        assert post(admin, grant, {"enabled": True}).status_code == 200
        ws = delegate.get("/console/data/workspace").json()["data"]
        assert ws["roles"] == ["business", "own_agents"] and ws["manage_members"]
        assert delegate.get("/api/model-admin/v1/connections").json()["data"] == []
        second = distribute(delegate, "普通同学")
        assert second
        assert (
            post(
                delegate,
                "members/" + member_id(admin, "普通同学") + "/issuer",
                {"enabled": True},
            ).status_code
            == 403
        )
        assert (
            post(
                delegate, "members", {"username": "冒用", "tenant_id": "toc"}
            ).status_code
            == 422
        )
        for protected in ("tob-admin", "toc-business", delegate_id):
            assert (
                post(
                    delegate,
                    "members",
                    {"username": "替换", "existing_actor": protected},
                ).status_code
                == 403
            )
        assert (
            post(
                admin,
                "members/" + member_id(admin, "普通同学") + "/issuer",
                {"enabled": True},
            ).status_code
            == 200
        )
        assert (
            post(
                delegate,
                "members",
                {
                    "username": "普通同学",
                    "existing_actor": member_id(admin, "普通同学"),
                },
            ).status_code
            == 403
        )
        assert post(admin, grant, {"enabled": False}).status_code == 200
        assert delegate.get(AUTH + "members").status_code == 403
        assert (
            post(
                delegate,
                "members",
                {"username": "不再发放", "delivery": "temporary_password"},
            ).status_code
            == 403
        )
        assert not delegate.get("/console/data/workspace").json()["data"][
            "manage_members"
        ]
        assert not any(
            x["username"] == name
            for x in other.get(AUTH + "members").json()["data"]["items"]
        )
        with env.admin.begin() as c:
            row = (
                c.execute(
                    select(s.identities).where(
                        s.identities.c.id == member_id(admin, "普通同学")
                    )
                )
                .mappings()
                .one()
            )
            assert (
                row.tenant == "tob"
                and row.products == ["pA1"]
                and row.roles == ["business", "own_agents"]
            )
