import json
from uuid import uuid4
import pytest
from sqlalchemy import select
from app import schema as s
from app.db import row, transaction
from app.llm import credentials, ModelError
from app.settings import settings
from tests.conftest import profile, published
from tests.flows import ready as prepare_session, submit, drain, fetch
from tests.model_service import BASE, setup_provider


@pytest.fixture
def provider(env, monkeypatch):
    # These test owners both publish and operate their own assistants.
    with env.admin.begin() as c:
        for tenant in ("tob", "toc"):
            rec = row(c, s.identities, tenant, tenant + "-admin")
            c.execute(
                s.identities.update()
                .where(s.identities.c.tenant == tenant, s.identities.c.id == rec.id)
                .values(roles=rec.roles + ["business"])
            )
    return setup_provider(monkeypatch)


def ready(e, t="tob", **kwargs):
    return prepare_session(e, t, actor=t + "-admin", **kwargs)


def api(
    e, method, suffix="", body=None, *, actor="tob-admin", key=None, scheme="https"
):
    return e.client.request(
        method,
        scheme + "://testserver" + BASE + suffix,
        headers={
            "Authorization": "Bearer " + e.tokens[actor],
            "Idempotency-Key": key or uuid4().hex,
        },
        json=body,
    )


def connection(e, p, actor="tob-admin", key="a", label="企业主连接", verify=True):
    result = api(
        e, "POST", "/connections", {"name": label, "api_key": p.keys[key]}, actor=actor
    )
    assert result.status_code == 201, result.text
    rec = result.json()["data"]
    assert rec["status"] == "pending"
    if verify:
        result = api(
            e,
            "POST",
            "/connections/" + rec["id"] + "/test",
            {"expected_revision": 1},
            actor=actor,
        )
        assert result.status_code == 200, result.text
        rec = result.json()["data"]
        assert rec["status"] == "ready"
    return rec


def spec(t, rec, model=None):
    p = profile(t)
    p["config"].update(
        model_provider="deepseek",
        model_connection_id=rec["id"],
        model_params={"temperature": 0.25, "max_tokens": 160},
    )
    if model:
        p["config"]["model_name"] = model
    return p


def execute(e, t, sid):
    res = submit(e, t, sid, "产品支持排班吗？", actor=t + "-admin")
    run = res.json()["data"]["run_id"]
    drain()
    return fetch(e, t, run)


def test_transport_encryption_idempotency_and_no_disclosure(env, provider, caplog):
    p = provider
    body = dict(name="销售连接", api_key=p.keys["a"])
    assert api(env, "POST", "/connections", body, scheme="http").status_code == 403
    assert (
        api(env, "POST", "/connections", body | {"tenant_id": "toc"}).status_code == 422
    )
    assert api(env, "GET", "/connections?api_key=forbidden").status_code == 400
    spoof = env.client.post(
        "http://testserver" + BASE + "/connections",
        headers={
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer " + env.tokens["tob-admin"],
            "Idempotency-Key": uuid4().hex,
        },
        json=body,
    )
    assert (
        spoof.status_code == 403 and spoof.json()["error"]["code"] == "HTTPS_REQUIRED"
    )
    assert api(env, "GET", "/connections", actor="tob-business").status_code == 403
    first = api(env, "POST", "/connections", body, key="same")
    assert first.status_code == 201
    rec = first.json()["data"]
    cid = rec["id"]
    assert api(env, "POST", "/connections", body, key="same").json() == first.json()
    assert (
        api(
            env, "POST", "/connections", dict(body, api_key=p.keys["b"]), key="same"
        ).status_code
        == 409
    )
    assert not p.calls
    with transaction("tob", model_actor="tob-admin") as c:
        record = row(c, s.model_connections, "tob", cid)
        ciphertext = record.encrypted_api_key
        assert credentials.unseal(record) == p.keys["a"] and ciphertext != p.keys["a"]
        from types import SimpleNamespace

        with pytest.raises(ModelError, match="MODEL_ENCRYPTION"):
            credentials.unseal(SimpleNamespace(**(dict(record) | {"tenant": "toc"})))
        audit = str(c.execute(select(s.audit)).all()) + str(
            c.execute(select(s.idempotency)).all()
        )
    ws = env.client.get(
        "/console/data/workspace",
        headers={"Authorization": "Bearer " + env.tokens["tob-admin"]},
    )
    ls = api(env, "GET", "/connections")
    assert (ws.status_code, ls.status_code) == (200, 200)
    assert ls.json()["data"] == [rec]
    errored = api(
        env,
        "PATCH",
        "/connections/" + cid,
        {"expected_revision": 1, "api_key": p.keys["bad"]},
    )
    assert errored.status_code == 200
    failure = api(
        env, "POST", "/connections/" + cid + "/test", {"expected_revision": 2}
    )
    assert failure.json()["data"]["last_error_code"] == "MODEL_CREDENTIALS"
    outputs = (
        first.text
        + ls.text
        + ws.text
        + failure.text
        + caplog.text
        + audit
        + str(env.client.get(BASE + "/openapi.json").json())
    )
    for secret in [*p.keys.values(), ciphertext]:
        assert secret not in outputs
    assert "encrypted_api_key" not in outputs


@pytest.mark.parametrize("mode", ["missing", "invalid"])
def test_mock_works_without_encryption(env, provider, monkeypatch, mode):
    if mode == "missing":
        monkeypatch.delenv("CHATMONEY_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("CHATMONEY_CREDENTIAL_ENCRYPTION_KEY", "wrong")
    settings.cache_clear()
    r = api(
        env, "POST", "/connections", {"name": "连接", "api_key": provider.keys["a"]}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (503, "MODEL_ENCRYPTION")
    _, _, sid = ready(env)
    assert execute(env, "tob", sid)["status"] == "succeeded" and not provider.calls
    assert api(env, "GET", "/connections").json()["data"] == []


def test_model_admin_can_manage_without_gaining_sales_access(env, provider):
    with env.admin.begin() as c:
        c.execute(
            s.identities.update()
            .where(s.identities.c.tenant == "tob", s.identities.c.id == "tob-admin")
            .values(roles=["model_admin"])
        )
    headers = {"Authorization": "Bearer " + env.tokens["tob-admin"]}
    workspace = env.client.get("/console/data/workspace", headers=headers)
    assert (
        workspace.status_code == 200
        and workspace.json()["data"]["counts"]["agents"] is None
    )
    assert api(env, "GET", "/connections").status_code == 200
    assert (
        env.client.get("/console/data/directory/runs", headers=headers).status_code
        == 403
    )
    assert env.req("POST", "/agents", body=profile()).status_code == 403


def test_isolation_and_business_permissions(env, provider):
    rec = connection(env, provider)
    path = "/connections/" + rec["id"]
    for actor, code in [
        ("toc-admin", 404),
        ("tob-business", 403),
        ("toc-business", 403),
    ]:
        for m, sub, body in [
            ("GET", "/usage", None),
            ("POST", "/test", {"expected_revision": 2}),
            ("PATCH", "", {"expected_revision": 2, "name": "错误修改"}),
            ("POST", "/disable", {"expected_revision": 2}),
            ("DELETE", "", {"expected_revision": 2}),
        ]:
            assert api(env, m, path + sub, body, actor=actor).status_code == code
    for endpoint in ["/connections", "/options"]:
        assert api(env, "GET", endpoint, actor="toc-admin").json()["data"] == []
    assert env.req("POST", "/agents", "toc-admin", spec("toc", rec)).status_code == 404
    assert (
        api(
            env,
            "POST",
            "/connections",
            {"name": "业务", "api_key": provider.keys["a"]},
            actor="tob-business",
        ).status_code
        == 403
    )
    with transaction("toc") as c:
        assert c.execute(select(s.model_connections)).first() is None


def test_version_binding_shared_connection_rotation_and_disable(
    env, provider, monkeypatch
):
    p = provider
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", p.keys["legacy"])
    settings.cache_clear()
    ra = connection(env, p)
    rb = connection(env, p, actor="toc-admin", key="b")
    a = published(env, "tob", spec("tob", ra))
    a2 = published(env, "tob", spec("tob", ra, "deepseek-v4-pro"))
    b = published(env, "toc", spec("toc", rb))
    sid = ready(env, agent_id=a)[2]
    sid2 = ready(env, agent_id=a2, customer="c3", cv="cv3")[2]
    sidb = ready(env, "toc", agent_id=b)[2]
    changed = {
        "expected_revision": 1,
        "changes": {
            "config": {
                "model_provider": "mock",
                "model_connection_id": None,
                "model_name": None,
            }
        },
    }
    assert env.req("PATCH", "/agents/" + a, body=changed).status_code == 200
    assert (
        env.req("GET", "/agents/" + a + "?view=draft").json()["data"]["config"][
            "model_provider"
        ]
        == "mock"
    )
    assert (
        env.req("GET", "/agents/" + a).json()["data"]["config"]["model_connection_id"]
        == ra["id"]
    )
    assert (
        env.req(
            "POST", "/agents/" + a + "/publish", body={"expected_revision": 2}
        ).status_code
        == 201
    )
    for t, session, tag, name in [
        ("tob", sid, "a", "deepseek-flash"),
        ("tob", sid2, "a", "deepseek-v4-pro"),
        ("toc", sidb, "b", "deepseek-flash"),
    ]:
        p.calls.clear()
        run = execute(env, t, session)
        assert run["status"] == "succeeded" and run["model_calls"]
        assert all(
            k == tag
            and url == "/chat/completions"
            and body["model"] == name
            and body["temperature"] == 0.25
            and body["max_tokens"] == 160
            for k, url, body in p.calls
        )
        assert all(x["usage"]["total_tokens"] == 11 for x in run["model_calls"])
    path = "/connections/" + ra["id"]
    refs = api(env, "GET", path + "/usage").json()["data"]
    assert {x["agent_id"] for x in refs} == {a, a2}
    assert next(x for x in refs if x["agent_id"] == a)["versions"] == [1]
    assert api(env, "DELETE", path, {"expected_revision": 2}).status_code == 409
    rot = api(
        env, "PATCH", path, {"expected_revision": 2, "api_key": p.keys["rotated"]}
    ).json()["data"]
    assert (
        rot["id"] == ra["id"]
        and rot["status"] == "pending"
        and rot["supported_models"] == []
    )
    assert (
        api(env, "POST", path + "/test", {"expected_revision": 3}).json()["data"][
            "status"
        ]
        == "ready"
    )
    p.calls.clear()
    assert execute(env, "tob", sid)["status"] == "succeeded"
    assert p.calls and all(k == "rotated" for k, _, _ in p.calls)
    assert (
        api(env, "POST", path + "/disable", {"expected_revision": 4}).status_code == 409
    )
    assert (
        api(
            env,
            "POST",
            path + "/disable",
            {"expected_revision": 4, "confirm_in_use": True},
        ).json()["data"]["status"]
        == "disabled"
    )
    p.calls.clear()
    failed = execute(env, "tob", sid)
    assert (
        failed["status"] == "failed"
        and failed["error"]["code"] == "MODEL_CONNECTION_DISABLED"
    )
    assert not p.calls
    with transaction("tob") as c:
        audit = (
            str(c.execute(select(s.model_calls)).all())
            + str(c.execute(select(s.events)).all())
            + str(c.execute(select(s.memory)).all())
        )
        for secret in p.keys.values():
            assert secret not in audit


@pytest.mark.parametrize(
    "fault,code,attempts",
    [
        (401, "MODEL_CREDENTIALS", 1),
        (429, "MODEL_RATE_LIMIT", 2),
        (503, "MODEL_UPSTREAM", 2),
        ("timeout", "MODEL_TIMEOUT", 2),
        ("network", "MODEL_NETWORK", 2),
        ("invalid", "MODEL_BAD_RESPONSE", 2),
    ],
)
def test_worker_fails_closed_and_bounds_attempts(
    env, provider, monkeypatch, fault, code, attempts
):
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", provider.keys["legacy"])
    settings.cache_clear()
    rec = connection(env, provider)
    sid = ready(env, spec=spec("tob", rec))[2]
    provider.errors["/chat/completions"] = fault
    provider.calls.clear()
    run = execute(env, "tob", sid)
    assert run["status"] == "failed" and run["error"]["code"] == code
    assert len(provider.calls) == attempts and all(
        tag == "a" for tag, _, _ in provider.calls
    )
    assert len(run["model_calls"]) == attempts
    assert all(
        secret not in json.dumps(run, ensure_ascii=False)
        for secret in provider.keys.values()
    )
    if fault == 401:
        assert api(env, "GET", "/connections").json()["data"][0]["status"] == "failed"


def test_catalog_errors_rename_delete_and_model_filter(env, provider):
    p = provider
    r = connection(env, p, verify=False)
    assert env.req("POST", "/agents", body=spec("tob", r)).status_code == 409
    path = "/connections/" + r["id"]
    p.available = ["retired-name"]
    verify = api(env, "POST", path + "/test", {"expected_revision": 1}, key="check")
    assert verify.json()["data"]["last_error_code"] == "MODEL_UNSUPPORTED"
    assert (
        api(env, "POST", path + "/test", {"expected_revision": 1}, key="check").json()
        == verify.json()
    )
    assert len(p.calls) == 2
    p.available = ["deepseek-v4-pro"]
    checked = api(env, "POST", path + "/test", {"expected_revision": 2}).json()["data"]
    assert checked["default_model"] == "deepseek-v4-pro"
    assert (
        env.req("POST", "/agents", body=spec("tob", r, "retired-name")).status_code
        == 422
    )
    assert (
        api(env, "PATCH", path, {"name": "新名称", "expected_revision": 2}).status_code
        == 409
    )
    assert (
        api(env, "PATCH", path, {"name": "新名称", "expected_revision": 3}).json()[
            "data"
        ]["name"]
        == "新名称"
    )
    assert api(env, "DELETE", path, {"expected_revision": 4}).json()["data"]["deleted"]
    assert api(env, "GET", "/connections").json()["data"] == []


def test_history_global_and_new_mock_with_global_enabled(env, provider, monkeypatch):
    monkeypatch.setenv("CHATMONEY_LEGACY_MODEL_TENANT", "tob")
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", provider.keys["legacy"])
    settings.cache_clear()
    # Represent a V1.0 immutable snapshot created before this migration.
    p = profile()
    aid = published(env)
    with env.admin.begin() as c:
        c.execute(
            s.versions.update()
            .where(s.versions.c.tenant == "tob", s.versions.c.agent_id == aid)
            .values(config=p["config"])
        )
        c.execute(
            s.agents.update()
            .where(s.agents.c.tenant == "tob", s.agents.c.id == aid)
            .values(draft=p["config"])
        )
    sid = ready(env, agent_id=aid)[2]
    result = execute(env, "tob", sid)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "MODEL_PERSONAL_CONNECTION_REQUIRED"
    assert not provider.calls
    p["config"]["model_provider"] = "mock"
    newsid = ready(env, spec=p, customer="c3", cv="cv3")[2]
    assert execute(env, "tob", newsid)["status"] == "succeeded" and not provider.calls


@pytest.mark.parametrize(
    "fault,code",
    [
        ("timeout", "MODEL_TIMEOUT"),
        ("empty", "MODEL_BALANCE"),
        ("invalid", "MODEL_BAD_RESPONSE"),
        (429, "MODEL_RATE_LIMIT"),
    ],
)
def test_verification_reports_bound_errors(env, provider, fault, code):
    rec = connection(env, provider, verify=False)
    provider.errors["/user/balance"] = fault
    reply = api(
        env, "POST", "/connections/" + rec["id"] + "/test", {"expected_revision": 1}
    ).json()["data"]
    assert (reply["status"], reply["last_error_code"]) == ("failed", code)
    assert (
        reply["error_message"]
        and reply["last_tested_at"]
        and reply["supported_models"] == []
    )
    assert len(provider.calls) == 1


def test_runtime_rejects_missing_foreign_binding_even_with_global_key(
    env, provider, monkeypatch
):
    from app.model_connections import model_call

    rec = connection(env, provider, actor="toc-admin", key="b")
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", provider.keys["legacy"])
    settings.cache_clear()
    provider.calls.clear()
    for ident in [rec["id"], "missing"]:
        with pytest.raises(ModelError, match="MODEL_CONNECTION_MISSING"):
            with model_call(
                "tob",
                "tob-admin",
                {
                    "model_provider": "deepseek",
                    "model_connection_id": ident,
                    "model_name": "deepseek-flash",
                },
            ):
                pytest.fail("unexpected resolver fallback")
    assert not provider.calls


def test_rotation_serializes_with_inflight_attempt(env, provider):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.llm import ModelInput
    from app.model_connections import model_call

    rec = connection(env, provider)
    cfg = spec("tob", rec, "deepseek-flash")["config"]
    entered, release, rotating = Event(), Event(), Event()

    def gate():
        entered.set()
        assert release.wait(6)

    provider.block = gate

    def call():
        with model_call("tob", "tob-admin", cfg) as client:
            return client.generate(ModelInput(instructions="", current_message="您好"))

    def rotate():
        rotating.set()
        return api(
            env,
            "PATCH",
            "/connections/" + rec["id"],
            {"expected_revision": 2, "api_key": provider.keys["rotated"]},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call)
        assert entered.wait(3)
        second = pool.submit(rotate)
        try:
            assert rotating.wait(3) and not second.done()
        finally:
            release.set()
        assert first.result().actual_model == "deepseek-flash"
        assert second.result().json()["data"]["status"] == "pending"
    with pytest.raises(ModelError, match="MODEL_CONNECTION_UNVERIFIED"):
        with model_call("tob", "tob-admin", cfg):
            pytest.fail("old credential reused")


def test_lost_master_key_fails_without_fallback(env, provider, monkeypatch):
    rec = connection(env, provider)
    sid = ready(env, spec=spec("tob", rec))[2]
    provider.calls.clear()
    from cryptography.fernet import Fernet

    monkeypatch.setenv(
        "CHATMONEY_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    settings.cache_clear()
    run = execute(env, "tob", sid)
    assert run["status"] == "failed" and run["error"]["code"] == "MODEL_ENCRYPTION"
    assert not provider.calls


def test_legacy_global_credential_never_funds_an_account(env, provider, monkeypatch):
    from app.model_connections import model_call

    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", provider.keys["legacy"])
    monkeypatch.delenv("CHATMONEY_LEGACY_MODEL_TENANT", raising=False)
    settings.cache_clear()
    for tenant in ("tob", "toc"):
        with pytest.raises(ModelError, match="MODEL_PERSONAL_CONNECTION_REQUIRED"):
            with model_call(tenant, tenant + "-admin", {}):
                pytest.fail("unassigned global key used")
    monkeypatch.setenv("CHATMONEY_LEGACY_MODEL_TENANT", "tob")
    settings.cache_clear()
    with pytest.raises(ModelError, match="MODEL_PERSONAL_CONNECTION_REQUIRED"):
        with model_call("toc", "toc-admin", {}):
            pytest.fail("foreign global key used")
    assert not provider.calls


def test_stream_records_usage_from_provider_final_chunk():
    import httpx
    from app.llm import DeepSeek, ModelInput

    event = {
        "id": "synthetic",
        "model": "deepseek-flash",
        "choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}],
    }
    final = {
        "choices": [],
        "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
    }
    wire = (
        "".join("data: " + json.dumps(x) + "\n\n" for x in [event, final])
        + "data: [DONE]\n\n"
    )
    client = DeepSeek(
        "deepseek-flash",
        uuid4().hex,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=wire)),
    )
    out = list(client.stream(ModelInput(instructions="", current_message="排班")))[-1]
    assert out.usage == dict(input_tokens=7, output_tokens=1, total_tokens=8)
