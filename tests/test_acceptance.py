from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from app import schema as s
from app.db import now, transaction, update
from app.channels import Sender
from app.llm import MockLLM
from app.worker import Worker
from tests.flows import ready, submit, drain, fetch


def test_personas_tenants_sessions_parallel_and_day_three(env):
    a, tk, one = ready(env)
    _, _, two = ready(env, agent_id=a, task_id=tk, customer="c3", cv="cv3")
    _, _, other = ready(env, "toc")
    entries = [
        ("tob", one, "排班，预算8万，需要20个账号"),
        ("tob", two, "排班，预算2万"),
        ("toc", other, "排班功能？"),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(
            pool.map(lambda x: submit(env, *x).json()["data"]["run_id"], entries)
        )
        list(
            pool.map(
                lambda qs: drain(Worker(queues=qs)), [["support"], ["sales"], ["sales"]]
            )
        )
    sale = fetch(env, "tob", ids[0])["result"]
    service = fetch(env, "toc", ids[2])["result"]
    assert "专业" in sale["text"] and "简短" in service["text"]
    assert (
        "跨" not in service["text"]
        and "排班" in sale["text"]
        and "排班" in service["text"]
    )
    assert env.req("GET", "/runs/" + ids[0], "toc-business").status_code == 404
    for sid, budget in [(one, "8万"), (two, "2万")]:
        m = env.req("GET", f"/sessions/{sid}/memory", "tob-business").json()["data"]
        assert next(x for x in m["facts"] if x["key"] == "budget")["value"] == budget
    with transaction("tob") as c:
        c.execute(
            s.messages.update()
            .where(s.messages.c.session_id == one)
            .values(created=now() - timedelta(days=3))
        )
    rid = submit(env, "tob", one, "上次预算多少，要几个席位？").json()["data"]["run_id"]
    drain(Worker())
    out = fetch(env, "tob", rid)["result"]["text"]
    assert "20" in out and "8万" in out and "2万" not in out


def test_concurrent_ingress_dedup_across_identities(env):
    _, _, sid = ready(env, "toc")
    p = f"/sessions/{sid}/messages"
    b = {"external_message_id": "original", "text": "订单 O7"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rs = list(
            pool.map(
                lambda who: env.req("POST", p, who, b, key="same"),
                ["toc-business", "toc-inbound", "toc-business"],
            )
        )
    assert {r.status_code for r in rs} == {202}
    ids = {r.json()["data"]["run_id"] for r in rs}
    assert len(ids) == 1
    assert (
        env.req("POST", p, "toc-inbound", b | {"text": "different"}).status_code == 409
    )
    drain()
    with transaction("toc") as c:
        assert len(c.execute(s.receipts.select()).all()) == 1


@pytest.mark.parametrize(
    "fault,expected", [("timeout", "failed"), ("invalid_json", "succeeded")]
)
def test_model_failures_do_not_send(env, fault, expected):
    _, _, sid = ready(env)
    rid = submit(env, "tob", sid, "排班功能").json()["data"]["run_id"]
    drain(Worker(model=MockLLM(fault)))
    res = fetch(env, "tob", rid)
    assert res["status"] == expected and len(res["model_calls"]) == 2
    if fault == "timeout":
        assert res["error"]["code"] == "MODEL_TIMEOUT"
    else:
        assert res["result"]["action"] == "handoff"
    assert not any(x["usage"] for x in res["model_calls"])
    with transaction("tob") as c:
        assert not c.execute(s.receipts.select()).first()


@pytest.mark.parametrize("action", ["disable", "withdraw", "pause", "optout"])
def test_final_authority_checked_after_intent(env, action):
    a, tk, sid = ready(env)

    def intervene():
        if action == "disable":
            r = env.req("POST", f"/agents/{a}/disable", body={"reason": "复核"})
        elif action == "withdraw":
            r = env.req(
                "POST",
                "/knowledge-bases/kb/withdraw",
                body={"document_id": "d0", "revision": 1, "reason": "过期"},
            )
        elif action == "pause":
            r = env.req("POST", f"/tasks/{tk}/pause", body={"reason": "暂停"})
        else:
            r = submit(env, "tob", sid, "请退订")
        assert r.status_code == 200, r.text

    rid = submit(env, "tob", sid, "排班功能是什么").json()["data"]["run_id"]
    drain(Worker(sender=Sender(after_intent=intervene)))
    r = fetch(env, "tob", rid)
    assert (
        r["status"] in {"succeeded", "cancelled"}
        and r["result"]["delivery"] != "accepted"
    )
    with transaction("tob") as c:
        assert not c.execute(s.receipts.select()).first()


def test_timer_wakes_stale_version_is_pinned_and_ordered(env):
    a, tk, sid = ready(env)
    path = f"/sessions/{sid}/actions"
    b = {
        "kind": "follow_up",
        "source_ref": "lead-c1",
        "due_at": (now() + timedelta(days=4)).isoformat(),
    }
    timer = env.req("POST", path, "tob-business", b).json()["data"]["run_id"]
    r = env.req(
        "PATCH",
        f"/agents/{a}",
        body={"expected_revision": 1, "changes": {"config": {"tone": "新版语气"}}},
    )
    assert r.status_code == 200
    assert (
        env.req(
            "POST", f"/agents/{a}/publish", body={"expected_revision": 2}
        ).status_code
        == 201
    )
    rid = submit(env, "tob", sid, "排班").json()["data"]["run_id"]
    drain()
    assert (
        "专业" in fetch(env, "tob", rid)["result"]["text"]
        and fetch(env, "tob", timer)["status"] == "queued"
    )
    with transaction("tob") as c:
        update(c, s.runs, "tob", timer, due=now() - timedelta(seconds=1))
    drain()
    assert fetch(env, "tob", timer)["status"] == "succeeded"


def test_openapi_contract_and_negative_paths(env):
    from pathlib import Path
    import re

    doc = next(Path("docs/architecture").glob("*Agent引擎开发方案*.md")).read_text(
        encoding="utf8"
    )
    expected = {
        (m.lower(), "/api/v1" + p)
        for m, p in re.findall(r"\*\* · `(POST|GET|PATCH) ([^\x60]+)`", doc)
    }
    api = env.client.get("/openapi.json").json()
    actual = {(method, path) for path, ops in api["paths"].items() for method in ops}
    assert len(expected) == 29 and expected == actual
    for method, path in expected:
        op = api["paths"][path][method]
        assert op["security"]
        if method != "get":
            assert any(
                p.get("name") == "Idempotency-Key" and p["required"]
                for p in op["parameters"]
            )
        if path.endswith("stream"):
            continue
        success = next(k for k in op["responses"] if k.startswith("2"))
        assert (
            "data"
            in op["responses"][success]["content"]["application/json"]["schema"][
                "properties"
            ]
        )
