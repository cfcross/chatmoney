"""Real PostgreSQL, ASGI, shared graph and worker. All model transports in these tests are offline."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from sqlalchemy import func, select
from app import schema as s
from app.db import now, update, insert
from app.llm import MockLLM, ModelOutput
from app.llm.base import ToolCall
from app.practice import personas
from app.worker import Worker
from tests.conftest import profile, published
from tests.test_agent_templates import call, create_from
from tests.test_personal_models import (
    data,
    assistant_spec,
    install_product_fixture,
    request,
    save_connection,
)
from tests.model_service import setup_provider

B = "/console/manage/"


def get(e, id):
    return data(call(e, "GET", B + "practices/" + id))


def make(e, spec=None):
    return data(e.req("POST", "/agents", body=spec or profile()))["agent_id"]


def begin(e, aid=None, **kw):
    return data(
        call(
            e,
            "POST",
            B + "agents/" + (aid or make(e)) + "/practices",
            {"version": 1, **kw},
        )
    )["simulation_id"]


def action(e, id, op, **kw):
    return call(
        e,
        "POST",
        B + "practices/" + id + "/" + op,
        dict(expected_revision=get(e, id)["revision"]) | kw,
    )


def step(e, id, model=None):
    assert Worker(["practice"], model=model).tick()
    return get(e, id)


def live_counts(e):
    with e.admin.connect() as c:
        return {
            t.name: c.scalar(select(func.count()).select_from(t))
            for t in [
                s.customers,
                s.orders,
                s.conversations,
                s.sessions,
                s.memory,
                s.tasks,
                s.messages,
                s.runs,
                s.sends,
                s.handoffs,
                s.versions,
                s.tools,
                s.events,
            ]
        }


@pytest.mark.parametrize("kind", list(personas.PERSONAS))
def test_six_personas_really_run_without_business_writes(env, kind):
    aid = make(env)
    before = live_counts(env)
    id = begin(env, aid, persona=kind, mode="auto", max_turns=4)
    for _ in range(5):
        if not Worker(["practice"]).tick():
            break
    d = get(env, id)
    assert d["status"] == "finished" and 1 <= d["turns"] <= 4, d
    assert d["messages"][0]["buyer"] and d["events"][0]["stage"] == "buyer"
    assert d["messages"][0]["evidence"]["graph"] == "shared"
    if kind == "budget" or kind == "keen":
        assert d["stop_reason"] == "handoff" and not d["messages"][-1]["seller"]
    elif kind == "cold":
        assert d["stop_reason"] == "refusal"
    elif kind == "boundary":
        assert d["stop_reason"] == "optout"
    assert d["platform_access"] is False and before == live_counts(env)


def test_persona_selection_matches_templates_and_custom():
    assert [
        personas.scenario({"id": "sys_" + k}, {})[2]
        for k in [
            "prospecting",
            "supplier",
            "brand",
            "software",
            "industry",
            "retention",
        ]
    ] == ["interested", "careful", "careful", "budget", "careful", "cold"]
    assert personas.scenario(None, {"persona": "新能源设备顾问"})[2] == "careful"
    assert personas.scenario(None, {})[2] == "interested"


def test_hidden_facts_progressive_and_responsive():
    facts = personas.hidden_facts("服务")
    setting = dict(role="采购", topic="服务")

    def req(last=None):
        return personas.make_input(
            "interested",
            setting,
            facts,
            2 if last else 1,
            [dict(seller=last, buyer="服务？")] if last else [],
        )

    assert not req().context["allowed_disclosure"]
    assert not req("采购顾问（讲一个场景。）：资料支持排班。").context[
        "allowed_disclosure"
    ]
    assert req("你的预算是多少？").context["allowed_disclosure"] == {
        "budget": facts["budget"]
    }
    assert "预算" in personas.demo_buyer(req("你的预算是多少？")) or facts[
        "budget"
    ] in personas.demo_buyer(req("你的预算是多少？"))
    assert personas.demo_buyer(req("介绍试用")) != personas.demo_buyer(req("目前未知"))
    assert not req("预算？费用？权限？").context["allowed_disclosure"]


def test_draft_template_role_sources_history_and_restart(env):
    a = data(
        create_from(
            env,
            config={
                "model_provider": "mock",
                "knowledge": [{"kb_id": "kb", "snapshot": 1}],
                "persona": "青藤产品研究员",
            },
        )
    )["agent_id"]
    id = begin(env, a)
    d = step(env, id)
    assert (
        d["status"] == "ready"
        and "青藤产品研究员" in d["messages"][0]["seller"]
        and "排班" in d["messages"][0]["seller"]
    )
    assert d["messages"][0]["evidence"]["sources"][0]["document_id"] == "d0"
    key = uuid4().hex
    path = B + "practices/" + id + "/control/next"
    body = {"expected_revision": d["revision"]}
    assert call(env, "POST", path, body, key=key).status_code == 202
    assert call(env, "POST", path, body, key=key).status_code == 202
    assert call(env, "POST", path, body).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: Worker(["practice"]).tick(), range(2))) == 1
    two = get(env, id)
    assert two["turns"] == 2 and two["messages"][0] == d["messages"][0]
    assert "排班" in two["messages"][1]["buyer"]
    assert not Worker(["practice"]).tick()
    assert not get(env, id)["platform_access"]


def test_published_test_keeps_draft_and_candidate_separate(env):
    a = published(env)
    id = begin(env, a, source="published", max_turns=1)
    d = step(env, id)
    assert d["source"] == "published" and d["evaluation"]["eligible"]
    assert action(env, id, "submit").status_code == 409
    assert (
        action(env, id, "review", decision="satisfied", note="核对了资料").status_code
        == 200
    )
    assert action(env, id, "submit").status_code == 200
    pending = data(call(env, "GET", B + "practice-candidates"))["items"][0]
    assert pending["fingerprint"] == d["fingerprint"] and "messages" not in pending
    res = data(
        call(
            env,
            "POST",
            B + "practice-candidates/" + id + "/review",
            dict(
                expected_revision=pending["revision"],
                decision="recommend",
                note="资料已核对",
            ),
        )
    )
    assert res["candidate"] == "pending_platform" and res["platform_access"] is False
    assert get(env, id)["review_history"][-1]["kind"] == "recommend"


def test_draft_edit_requires_retest_and_history_persists(env):
    a = make(env)
    id = begin(env, a, max_turns=1)
    d = step(env, id)
    data(action(env, id, "review", decision="satisfied", note="已看"))
    data(
        env.req(
            "PATCH",
            "/agents/" + a,
            body={"expected_revision": 1, "changes": {"config": {"tone": "更简洁"}}},
        )
    )
    assert not get(env, id)["current"] and action(env, id, "submit").status_code == 409
    new = begin(env, a, version=2, max_turns=1)
    d2 = step(env, new)
    assert (
        d2["fingerprint"] != d["fingerprint"]
        and "更简洁" in d2["messages"][0]["seller"]
    )
    assert len(data(call(env, "GET", B + "agents/" + a + "/practices"))["items"]) == 2


def test_missing_model_or_knowledge_and_unauthorized_reads(env):
    a = data(create_from(env))["agent_id"]
    path = B + "agents/" + a + "/practices"
    assert (
        call(env, "POST", path, {"version": 1}).json()["error"]["code"]
        == "PRACTICE_MODEL"
    )
    data(
        env.req(
            "PATCH",
            "/agents/" + a,
            body={
                "expected_revision": 1,
                "changes": {"config": {"model_provider": "mock"}},
            },
        )
    )
    assert (
        call(env, "POST", path, {"version": 2}).json()["error"]["code"]
        == "PRACTICE_KNOWLEDGE"
    )
    id = begin(env)
    assert call(env, "GET", B + "practices/" + id, actor="toc-admin").status_code == 404
    assert (
        call(
            env,
            "POST",
            B + "practices/" + id + "/control/stop",
            {"expected_revision": 1},
            actor="tob-business",
        ).status_code
        == 403
    )


def test_pause_resume_auto_stop_and_duplicate_requests(env):
    id = begin(env, mode="auto", max_turns=3)
    data(action(env, id, "control/pause"))
    assert not Worker(["practice"]).tick()
    data(action(env, id, "control/next"))
    d = step(env, id)
    assert d["turns"] == 1 and d["status"] == "ready"
    data(action(env, id, "control/auto"))
    d = step(env, id)
    assert d["status"] == "queued"
    data(action(env, id, "control/stop"))
    assert not Worker(["practice"]).tick() and get(env, id)["turns"] == 2
    assert action(env, id, "control/resume").status_code == 409


def test_inflight_pause_and_resume_does_not_bill_buyer_twice(env):
    id = begin(env)
    started, release = Event(), Event()

    class Slow(MockLLM):
        def generate(self, req):
            if req.context.get("practice_role") == "buyer":
                started.set()
                assert release.wait(8)
            return super().generate(req)

    with ThreadPoolExecutor() as pool:
        f = pool.submit(Worker(["practice"], model=Slow()).tick)
        assert started.wait(8)
        data(action(env, id, "control/pause"))
        release.set()
        assert f.result(timeout=8)
    d = get(env, id)
    assert len(d["events"]) == 1 and d["status"] == "paused" and d["turns"] == 0
    data(action(env, id, "control/resume"))
    d = step(env, id)
    assert [v["stage"] for v in d["events"]].count("buyer") == 1 and d["turns"] == 1


def test_unknown_reservation_after_worker_loss_never_resends(env):
    id = begin(env)
    with env.admin.begin() as c:
        update(
            c,
            s.practice,
            "tob",
            id,
            status="running",
            lease="expired",
            until=now() - timedelta(seconds=5),
            accounted=1300,
        )
        insert(c, s.practice_turns, "tob", id + ":1", sim_id=id, num=1)
        insert(
            c,
            s.practice_calls,
            "tob",
            id + ":1:buyer",
            sim_id=id,
            num=1,
            stage="buyer",
            state="reserved",
            reserved=1300,
            charged=1300,
        )
    d = step(env, id)
    assert (
        d["stop_reason"] == "unknown_call"
        and len(d["events"]) == 1
        and not Worker(["practice"]).tick()
    )


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("budget", "token_limit"),
        ("timeout", "MODEL_TIMEOUT"),
        ("withdraw", "source_changed"),
    ],
)
def test_limits_failures_and_revoked_material(env, scenario, reason):
    id = begin(env, token_limit=1000 if scenario == "budget" else 64000)
    if scenario == "withdraw":
        with env.admin.begin() as c:
            update(c, s.docs, "tob", "d0", status="withdrawn")
    d = step(env, id, MockLLM("timeout") if scenario == "timeout" else None)
    assert (
        d["stop_reason"] == reason
        and not d["evaluation"]["eligible"]
        and not Worker(["practice"]).tick()
    )
    if scenario == "timeout":
        assert len(d["events"]) == 1


class EngineReply(MockLLM):
    def __init__(self, buyer, reply=None, tool=None):
        super().__init__()
        self.buyer = buyer
        self.reply = reply
        self.tool = tool
        self.inputs = []

    def generate(self, req):
        self.inputs.append(req.model_dump())
        if req.context.get("practice_role") == "buyer":
            value = self.buyer
        elif req.mode == "json":
            return super().generate(req)
        elif self.tool and not req.tool_history:
            return ModelOutput(
                tool_calls=[
                    ToolCall(
                        id="call",
                        name=self.tool,
                        args={"text": "拟议回复"}
                        if self.tool == "channel.reply"
                        else {"order_id": "PRACTICE-7"},
                    )
                ],
                actual_model="mock-test",
                provider_request_id="test",
                finish_reason="tool_calls",
            )
        elif self.reply:
            value = self.reply
        else:
            return super().generate(req)
        return ModelOutput(
            text=value,
            actual_model="mock-test",
            provider_request_id="test",
            finish_reason="stop",
        )


@pytest.mark.parametrize(
    "output",
    [
        "我们的正式报价为8000元。",
        "我们保证7天交付。",
        "我们保证提高30%。",
        "其他客户的电话是13800000000。",
    ],
)
def test_proven_hard_veto_with_evidence_no_reply_or_candidate(env, output):
    id = begin(env, max_turns=1)
    d = step(env, id, EngineReply("了解产品", output))
    assert d["evaluation"]["veto"] and not d["messages"][0]["seller"]
    assert any(
        v["turn"] == 1 and v["quote"] in output
        for v in d["evaluation"]["findings"]
        if v["kind"] == "veto"
    )
    data(action(env, id, "review", decision="satisfied", note="看过了"))
    assert action(env, id, "submit").status_code == 409


def test_optout_before_seller_call_and_no_external_gateways(env):
    from app.channels import Sender
    from app.tools.gateway import Gateway

    ex = SimpleNamespace(practice=True)
    assert Sender()(ex, "hello", [])["delivery"] == "not_sent"
    assert Sender().dispatch(ex)["delivery"] == "not_sent"
    assert Gateway().execute(ex, {}, 0)["status"] == "denied"
    id = begin(env)
    d = step(env, id, EngineReply("请停止联系"))
    assert [v["stage"] for v in d["events"]] == ["buyer"] and d[
        "stop_reason"
    ] == "optout"


@pytest.mark.parametrize(
    "tool,expected",
    [
        ("external.mail", "denied"),
        ("channel.reply", "succeeded"),
        ("order.lookup", "denied"),
    ],
)
def test_sandbox_tool_receipts_and_no_real_sender(env, tool, expected):
    a = make(env)
    before = live_counts(env)
    id = begin(env, a, max_turns=1)
    d = step(env, id, EngineReply("了解产品", tool=tool))
    receipt = d["messages"][0]["tools"][0]
    assert (
        receipt["status"] == expected
        and not receipt["external"]
        and live_counts(env) == before
    )
    if expected == "denied":
        assert not d["messages"][0]["seller"]
    else:
        assert "拟议回复" in d["messages"][0]["seller"]


def test_deepseek_same_identity_private_prompts_actual_http_and_isolation(
    env, manager, monkeypatch
):
    from tests.test_team_workspace import onboard
    from tests.test_workspace_login import HOST
    from fastapi.testclient import TestClient
    from app.main import app
    from app.team import TEAM

    p = setup_provider(monkeypatch)
    install_product_fixture(env)
    with TestClient(app, base_url=HOST) as member:
        onboard(manager, member, "对练成员")
        conn = save_connection(member, p.keys["b"])
        spec = assistant_spec(conn)
        spec["config"]["persona"] = "仅销售方可见的角色标记"
        aid = data(request(member, "POST", "/api/v1/agents", spec))["agent_id"]
        url = B + "agents/" + aid + "/practices"
        assert request(member, "POST", url, {"version": 1}).status_code == 422
        sid = data(
            request(
                member,
                "POST",
                url,
                {
                    "version": 1,
                    "accept_cost": True,
                    "max_turns": 2,
                    "mode": "auto",
                    "quality_assist": True,
                },
            )
        )["simulation_id"]
        assert request(manager, "GET", B + "practices/" + sid).status_code == 404
        assert (
            request(
                manager, "POST", url, {"version": 1, "accept_cost": True}
            ).status_code
            == 404
        )
        for _ in range(2):
            assert Worker(["practice"]).tick()
        got = data(request(member, "GET", B + "practices/" + sid))
        assert got["turns"] == 2 and got["provider"] == "deepseek", got
        chat = [v for v in p.calls if v[1] == "/chat/completions"]
        assert len(chat) == 8 and {v[0] for v in chat} == {"b"}
        assert len({v[2]["model"] for v in chat}) == 1 and got["usage"] == 88
        with env.admin.connect() as c:
            h = c.scalar(
                select(s.buyer_private.c.facts).where(
                    s.buyer_private.c.tenant == TEAM, s.buyer_private.c.id == sid
                )
            )
        for _, _, body in chat:
            messages = body["messages"]
            prompt = json.dumps(messages, ensure_ascii=False)
            guide = messages[0]["content"]
            assert p.keys["b"] not in prompt
            if "虚构客户" in guide:
                assert (
                    "仅销售方可见的角色标记" not in prompt and h["budget"] not in prompt
                )
            elif "复核对话" in guide:
                assert (
                    h["budget"] not in prompt and "仅销售方可见的角色标记" not in prompt
                )
            else:
                assert (
                    "仅销售方可见的角色标记" in prompt
                    and "支持排班" in prompt
                    and h["budget"] not in prompt
                )
        assert h["budget"] not in json.dumps(got, ensure_ascii=False) and conn[
            "id"
        ] not in json.dumps(got)


def test_replay_same_fictional_customer_fresh_history(env):
    a = make(env)
    sid = begin(env, a, max_turns=1, persona="careful")
    d = step(env, sid)
    paired = begin(env, a, repeat_from=sid, max_turns=1)
    with env.admin.connect() as c:
        facts = lambda pk: c.scalar(
            select(s.buyer_private.c.facts).where(
                s.buyer_private.c.tenant == "tob", s.buyer_private.c.id == pk
            )
        )
        assert facts(paired) == facts(sid)
    assert (
        get(env, paired)["messages"] == []
        and get(env, paired)["persona"] == d["persona"]
    )
    foreign = make(env)
    assert (
        call(
            env,
            "POST",
            B + "agents/" + foreign + "/practices",
            {"version": 1, "repeat_from": sid},
        ).status_code
        == 422
    )


def test_negative_commitment_is_not_an_assertion(env):
    id = begin(env, max_turns=1)
    d = step(
        env, id, EngineReply("排班服务有哪些？", "我们不能保证7天交付，需要人工确认。")
    )
    assert d["messages"][0]["action"] == "reply" and not d["evaluation"]["veto"]


def test_unverified_technical_claim_not_passed_to_buyer(env):
    id = begin(env)
    d = step(env, id, EngineReply("了解产品", "我们取得ISO9001认证，额定功率达80kW。"))
    assert d["messages"][0]["action"] == "blocked" and not d["messages"][0]["seller"]
    assert any(
        v["kind"] == "hold" and v["turn"] == 1 for v in d["evaluation"]["findings"]
    )
    assert not d["evaluation"]["eligible"]


def test_hidden_budget_cannot_be_invented_on_opening(env):
    id = begin(env)
    d = step(env, id, EngineReply("我们的预算是99000元。"))
    assert (
        d["stop_reason"] == "buyer_consistency"
        and d["messages"] == []
        and len(d["events"]) == 1
    )


def test_goal_completion_and_evidence_of_subjective_assessment(env):
    id = begin(env)
    d = step(
        env,
        id,
        EngineReply(
            "同意安排会议，请说明产品排班的下一步。",
            "您想了解什么？适合什么场景？需要什么资料？",
        ),
    )
    assert d["stop_reason"] == "goal" and any(
        f["rule"] == "一次提出多个问题" and f["quote"]
        for f in d["evaluation"]["findings"]
    )


def test_manual_stop_while_model_inflight_is_terminal(env):
    id = begin(env)
    started, release = Event(), Event()

    class Slow(MockLLM):
        def generate(self, req):
            started.set()
            assert release.wait(8)
            return super().generate(req)

    with ThreadPoolExecutor() as pool:
        f = pool.submit(Worker(["practice"], model=Slow()).tick)
        assert started.wait(8)
        data(action(env, id, "control/stop"))
        release.set()
        f.result(timeout=8)
    d = get(env, id)
    assert (
        not Worker(["practice"]).tick()
        and d["turns"] == 0
        and len(d["events"]) == 1
        and not d["evaluation"]["eligible"]
    )


@pytest.mark.parametrize(
    "question,grounded", [("产品支持排班吗？", True), ("ZXQJ 无关词", False)]
)
def test_identical_inbound_retrieval_and_reply_in_live_and_practice(
    env, question, grounded
):
    from tests.flows import ready, submit, drain, fetch

    aid = published(env)
    _, _, session = ready(env, agent_id=aid)
    drain()  # Complete the authorized test task first.
    rid = data(submit(env, "tob", session, question))["run_id"]
    drain()
    live = fetch(env, "tob", rid)["result"]
    simulation = begin(env, aid, source="published", max_turns=1)
    rehearsal = step(env, simulation, EngineReply(question))["messages"][0]
    assert rehearsal["seller"] == live["text"]
    assert bool(rehearsal["evidence"]["sources"]) == grounded
    assert rehearsal["action"] == ("reply" if grounded else "draft")
    if not grounded:
        assert live["action"] == "draft" and live["delivery"] == "not_sent"


def test_history_pages_do_not_drop_old_records_or_repeat_new_ones(env):
    a = make(env)
    ids = []
    for i in range(4):
        id = begin(env, a, max_turns=1)
        ids.append(id)
        data(action(env, id, "control/stop"))
    endpoint = B + "agents/" + a + "/practices"
    first = data(call(env, "GET", endpoint + "?limit=2"))
    assert [x["id"] for x in first["items"]] == list(reversed(ids[2:]))
    newer = begin(env, a)
    second = data(
        call(env, "GET", endpoint + "?limit=2&before=" + first["next_before"])
    )
    assert [x["id"] for x in second["items"]] == list(reversed(ids[:2])) and second[
        "next_before"
    ] is None
    assert newer not in {
        v["id"]
        for v in data(call(env, "GET", endpoint + "?finished_only=true"))["items"]
    }
    other = begin(env, make(env))
    assert call(env, "GET", endpoint + "?before=" + other).status_code == 404
