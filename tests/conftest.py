import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import schema as s
from app.db import insert
from scripts.seed import seed


@pytest.fixture
def env(monkeypatch):
    # Reset only an explicitly dedicated database. Production connections are never used by tests.
    owner = os.environ["MIGRATION_DATABASE_URL"]
    url = os.environ["DATABASE_URL"]
    if (
        not make_url(owner).database.endswith("_test")
        or make_url(owner).database != make_url(url).database
    ):
        pytest.fail("Set dedicated *_test database URLs before pytest")
    monkeypatch.setenv("LLM_BACKEND", "mock")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from app.settings import settings

    settings.cache_clear()
    from app.main import app

    admin = create_engine(owner, hide_parameters=True)
    tokens = {
        f"{t}-{r}": uuid.uuid4().hex
        for t in ("tob", "toc")
        for r in ("admin", "business", "inbound")
    }
    with admin.begin() as db:
        tables = (
            ", ".join(s.meta.tables)
            + " , checkpoints, checkpoint_blobs, checkpoint_writes"
        )
        db.execute(text("TRUNCATE " + tables + " RESTART IDENTITY CASCADE"))
        seed(db, tokens)
        # Stage 1 fixtures represent approved documents owned by a separate knowledge workflow.
        for tenant, p in [("tob", "pA1"), ("toc", "pB1")]:
            insert(db, s.kbs, tenant, "kb", product_id=p, name="Demo", latest=1)
            insert(
                db,
                s.docs,
                tenant,
                "d0",
                kb_id="kb",
                title="A1 功能",
                original="产品支持排班。",
                status="ready",
                actor="seed",
            )
            insert(
                db,
                s.chunks,
                tenant,
                "d0:1",
                document_id="d0",
                ordinal=1,
                body="产品支持排班。",
            )
            insert(
                db,
                s.snapshots,
                tenant,
                "kb:1",
                kb_id="kb",
                number=1,
                documents=[{"document_id": "d0", "revision": 1}],
                actor="seed",
            )
    with TestClient(app) as client:

        def req(method, path, actor="tob-admin", body=None, key=None, **kwargs):
            headers = {"Authorization": "Bearer " + tokens[actor]}
            if method not in {"GET", "HEAD"}:
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            return client.request(
                method, "/api/v1" + path, headers=headers, json=body, **kwargs
            )

        yield SimpleNamespace(req=req, admin=admin, tokens=tokens, client=client)
    admin.dispose()


@pytest.fixture
def manager(env):
    from app import web_identity as web
    from app.main import app
    from app.team import TEAM, ADMIN
    from scripts.workspace_accounts import ensure_team

    with env.admin.begin() as c:
        ensure_team(c)
        first = web.issue_setup(c, TEAM, ADMIN, ADMIN, by="test")
    origin = "https://testserver"
    with TestClient(app, base_url=origin) as client:
        result = client.post(
            "/console/auth/activate",
            headers={"Origin": origin},
            json={
                "username": ADMIN,
                "password": secrets.token_urlsafe(22),
                "new_username": "工作台管理员",
                "setup_code": first["setup_code"],
            },
        )
        result.raise_for_status()
        yield client


def profile(t="tob"):
    sales = t == "tob"
    product = "pA1" if sales else "pB1"
    return dict(
        name="产品顾问" if sales else "售后客服",
        product_id=product,
        workflow="sales" if sales else "support",
        config=dict(
            persona="产品顾问" if sales else "售后客服",
            tone="专业" if sales else "简短",
            goal="帮助客户",
            allowed=["批准的资料"],
            forbidden=["承诺交期"],
            company_ref=t + "@1",
            product_ref=product + "@1",
            knowledge=[dict(kb_id="kb", snapshot=1)],
            tool_allowlist=["channel.reply"]
            if sales
            else ["channel.reply", "order.lookup"],
            policy_ref="sales.policy@1" if sales else "support.policy@1",
            handoff_on=["ask_human", "formal_quote"]
            if sales
            else ["ask_human", "refund"],
        ),
    )


def published(e, t="tob", spec=None):
    r = e.req("POST", "/agents", t + "-admin", spec or profile(t))
    assert r.status_code == 201, r.text
    id = r.json()["data"]["agent_id"]
    r = e.req("POST", f"/agents/{id}/publish", t + "-admin", {"expected_revision": 1})
    assert r.status_code == 201, r.text
    return id
