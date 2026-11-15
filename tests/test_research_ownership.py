"""Two real workbench identities, same company; personal model transported to an offline provider only."""

import json
import httpx
from fastapi.testclient import TestClient
from app.main import app
from app.worker import Worker
from app.research import network as net
from tests.test_company_research import BASE, SITE, SOURCE, RULES, capture
from tests.test_workspace_login import HOST
from tests.test_team_workspace import onboard
from tests.test_personal_models import (
    request,
    data,
    save_connection,
    assistant_spec,
    install_product_fixture,
)
from tests.model_service import setup_provider
from app.llm import DeepSeek


def test_reviewed_member_read_uses_own_key_and_same_team_is_private(
    env, manager, monkeypatch
):
    provider = setup_provider(monkeypatch)
    install_product_fixture(env)
    calls = capture(monkeypatch)
    prev = provider.handle

    def handle(req):
        body = json.loads(req.content) if req.content else {}
        if (
            req.url.path == "/chat/completions"
            and "evidence_id" in body["messages"][0]["content"]
        ):
            assert (
                req.headers["Authorization"] == "Bearer " + provider.keys["b"]
                and "tools" not in body
            )
            assert all(v not in req.content.decode() for v in provider.keys.values())
            ctx = json.loads(body["messages"][1]["content"].split("：", 1)[1])
            selection = dict(
                evidence_id=ctx["company"][0]["id"],
                product_id=ctx["product"][0]["id"],
                approach="了解需求",
            )
            return httpx.Response(
                200,
                json={
                    "id": "offline-research",
                    "model": body["model"],
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(selection)},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 3,
                        "total_tokens": 15,
                    },
                },
            )
        return prev(req)

    provider.handle = handle
    with TestClient(app, base_url=HOST) as m, TestClient(app, base_url=HOST) as other:
        onboard(manager, m, "企业研读甲")
        onboard(manager, other, "企业研读乙")
        con = save_connection(m, provider.keys["b"])
        spec = assistant_spec(con)
        spec["config"]["research_tools"] = [net.TOOL]
        a = data(request(m, "POST", "/api/v1/agents", spec))["agent_id"]
        aid = "/api/v1/agents/" + a
        data(request(m, "POST", aid + "/publish", {"expected_revision": 1}))
        src = data(
            request(
                manager,
                "POST",
                BASE + "research-sources",
                SOURCE | {"allow_model_processing": True},
            )
        )["id"]
        agent = BASE + "agents/" + a
        sim = data(
            request(
                m,
                "POST",
                agent + "/practices",
                {
                    "version": 1,
                    "source": "published",
                    "accept_cost": True,
                    "max_turns": 1,
                },
            )
        )["simulation_id"]
        assert Worker(["practice"]).tick()
        p = BASE + "practices/" + sim
        d = data(request(m, "GET", p))
        for op, b in (
            ("review", {"decision": "satisfied", "note": "已阅"}),
            ("submit", {}),
        ):
            d = data(
                request(
                    m, "POST", p + "/" + op, {"expected_revision": d["revision"]} | b
                )
            )
        data(
            request(
                manager,
                "POST",
                BASE + "practice-candidates/" + sim + "/review",
                {
                    "expected_revision": d["revision"],
                    "decision": "recommend",
                    "note": "同一配置",
                },
            )
        )
        grant = data(
            request(m, "POST", agent + "/research/grants", {"source_id": src})
        )["id"]
        policy = BASE + "research-grants/" + grant + "/decision"
        decision = {"decision": "approve", "reason": "允许受控页"}
        assert request(m, "POST", policy, decision).status_code == 403
        data(request(manager, "POST", policy, decision))
        start = agent + "/research/jobs"
        b = {
            "grant_id": grant,
            "company_url": SITE,
            "research_goal": "储能设备",
            "criteria": RULES,
            "max_pages": 1,
        }
        assert request(m, "POST", start, b).status_code == 422
        assert request(other, "POST", start, b).status_code in (403, 404)
        j = data(request(m, "POST", start, b | {"accept_model_cost": True}))["id"]
        assert Worker(["research"]).tick()
        path = BASE + "research-jobs/" + j
        r = data(request(m, "GET", path))
        assert r["status"] == "ready", r
        assert r["report"]["usage"]["total_tokens"] == 15 and len(calls) == 2
        for c in (other, manager):
            assert request(c, "GET", path).status_code == 404
        assert (
            request(
                other,
                "POST",
                path + "/review",
                {"decision": "approved", "draft": "其他账号测试"},
            ).status_code
            == 404
        )
        assert con["id"] not in json.dumps(r) and provider.keys["b"] not in json.dumps(
            r
        )


def test_specific_robot_restrictions_cannot_be_relaxed_by_wildcard():
    rules = net.Robots(
        "User-agent: *\nAllow: /business/safe\nUser-agent: ChatMoneyResearch\nDisallow: /business"
    )
    assert not rules.allowed(SITE + "safe")
