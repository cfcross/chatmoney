from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import select, text
from app import schema as s
from app.channels import Sender, reconcile
from app.db import transaction, update
from app.worker import Worker
from app.llm import MockLLM
from tests.flows import ready, submit, drain, fetch
from tests.conftest import profile


def test_takeover_during_platform_receipt_gap(env):
    _, _, sid = ready(env, "toc")
    reached, resume = Event(), Event()

    def receipt_gap():
        reached.set()
        assert resume.wait(timeout=10)

    rq = submit(env, "toc", sid, "请问 O7 发货了吗")
    rid = rq.json()["data"]["run_id"]
    path = "/sessions/" + sid
    with ThreadPoolExecutor(max_workers=1) as pool:
        f = pool.submit(drain, Worker(sender=Sender(after_accept=receipt_gap)))
        assert reached.wait(timeout=10)
        try:
            h = env.req(
                "POST", path + "/handoff", "toc-admin", {"reason": "人工复核"}
            ).json()["data"]
            owner = env.req(
                "POST",
                path + "/takeover",
                "toc-admin",
                {"handoff_id": h["handoff_id"], "expected_epoch": h["owner_epoch"]},
            ).json()["data"]
            assert (
                env.req(
                    "POST",
                    path + "/human-messages",
                    "toc-admin",
                    {"text": "接管", "expected_epoch": owner["owner_epoch"]},
                ).status_code
                == 409
            )
            assert fetch(env, "toc", rid)["result"]["delivery"] == "dispatching"
        finally:
            resume.set()
        f.result()
    with transaction("toc") as c:
        ledger = c.execute(select(s.sends)).mappings().one()
        assert reconcile(c, "toc", ledger) == "accepted"
        assert len(c.execute(s.receipts.select()).all()) == 1
    assert fetch(env, "toc", rid)["result"]["delivery"] == "accepted"


def test_approved_knowledge_required_and_old_task_facts_survive(env):
    spec = profile()
    spec["config"]["knowledge"][0]["snapshot"] = 2
    a = env.req("POST", "/agents", body=spec).json()["data"]["agent_id"]
    assert (
        env.req("POST", f"/agents/{a}/publish", body={"expected_revision": 1}).json()[
            "error"
        ]["code"]
        == "INVALID_PROFILE"
    )


def test_opening_consent_frequency_and_business_idempotency(env, monkeypatch):
    from app.settings import settings

    monkeypatch.setenv("RATE_PER_HOUR", "1")
    settings.cache_clear()
    _, _, sid = ready(env)
    path = f"/sessions/{sid}/actions"
    b = {"kind": "opening", "source_ref": "lead-c1"}
    with transaction("tob") as c:
        update(c, s.conversations, "tob", "cv1", permitted=False)
    r = env.req("POST", path, "tob-business", b).json()["data"]
    drain()
    assert fetch(env, "tob", r["run_id"])["result"]["delivery"] == "not_sent"
    with transaction("tob") as c:
        update(c, s.conversations, "tob", "cv1", permitted=True)
    r = env.req("POST", path, "tob-business", b, key="permitted").json()["data"]
    drain()
    assert fetch(env, "tob", r["run_id"])["result"]["delivery"] == "accepted"
    assert env.req("POST", path, "tob-business", b, key="permitted").json()["data"] == r
    q = submit(env, "tob", sid, "排班功能")
    drain()
    assert (
        fetch(env, "tob", q.json()["data"]["run_id"])["result"]["reason"]
        == "frequency_limit"
    )


def test_transport_failure_returns_no_partial_message(env, monkeypatch):
    from app.settings import settings

    monkeypatch.setenv("LLM_STREAM_TEXT", "true")
    settings.cache_clear()
    spec = profile()
    spec["config"]["tool_allowlist"] = []
    _, _, sid = ready(env, spec=spec)
    q = submit(env, "tob", sid, "排班文档")
    drain(Worker(model=MockLLM("disconnect")))
    assert (
        fetch(env, "tob", q.json()["data"]["run_id"])["error"]["code"]
        == "MODEL_STREAM_INCOMPLETE"
    )
    with transaction("tob") as c:
        assert not c.execute(s.receipts.select()).first()
        assert not c.execute(
            s.messages.select().where(s.messages.c.kind != "inbound")
        ).first()


def test_rls_runtime_role_and_knowledge_content(env):
    with transaction("tob") as c:
        assert c.scalar(
            text(
                "select not (rolsuper or rolbypassrls) from pg_roles where rolname=current_user"
            )
        )
        assert not c.execute(select(s.docs).where(s.docs.c.tenant == "toc")).first()
        actual = c.scalars(select(s.customers.c.id)).all()
        assert set(actual) == {"c1", "c3"}
        assert not c.execute(select(s.orders)).first()
        assert c.scalars(select(s.tenants.c.id)).all() == ["tob"]


def test_paused_human_release_and_later_task_resume(env):
    _, tk, sid = ready(env)
    p = "/sessions/" + sid
    result = submit(env, "tob", sid, "请给正式报价")
    drain()
    assert (
        fetch(env, "tob", result.json()["data"]["run_id"])["result"]["action"]
        == "handoff"
    )
    se = env.req("GET", p).json()["data"]
    h = {"handoff_id": se["handoff_id"], "expected_epoch": se["owner_epoch"]}
    claimed = env.req("POST", p + "/takeover", body=h).json()["data"]
    assert (
        env.req(
            "POST", f"/tasks/{tk}/pause", body={"reason": "暂停销售任务"}
        ).status_code
        == 200
    )
    new = submit(env, "tob", sid, "排班功能有哪些？")
    assert new.status_code == 200
    release = h | {
        "expected_epoch": claimed["owner_epoch"],
        "resolution": "resume",
        "note": "稍后恢复",
    }
    assert (
        env.req("POST", p + "/release", body=release).json()["data"]["status"]
        == "paused"
    )
    drain()
    assert env.req("GET", p).json()["data"]["status"] == "paused"
    assert (
        env.req("POST", f"/tasks/{tk}/resume", body={"reason": "恢复处理"}).status_code
        == 202
    )
    drain()
    with transaction("tob") as c:
        assert len(c.execute(select(s.receipts)).all()) == 1
    assert env.req("GET", p).json()["data"]["status"] == "waiting"


def test_hard_quote_rule_survives_model_outage(env):
    _, _, sid = ready(env)
    req = submit(env, "tob", sid, "负责人能给正式报价吗？")
    drain(Worker(model=MockLLM("timeout")))
    out = fetch(env, "tob", req.json()["data"]["run_id"])
    assert (
        out["result"]["reason"] == "formal_quote"
        and out["result"]["delivery"] == "not_sent"
    )
    assert (
        env.req("GET", "/sessions/" + sid).json()["data"]["status"] == "handoff_pending"
    )
