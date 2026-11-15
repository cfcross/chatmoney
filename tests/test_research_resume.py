from datetime import timedelta
from sqlalchemy import select
import pytest
from app import schema as s
from app.db import now, update
from app.worker import Worker
from app.research import assessment, network as net
from app.research.extract import parse
from tests.test_company_research import (
    setup,
    run_job,
    view,
    capture,
    BASE,
    SOURCE,
    HTML,
    SITE,
)
from tests.test_agent_templates import call
from tests.test_personal_models import data


def test_saved_capture_resumes_without_rereading_network(env, monkeypatch):
    calls = capture(monkeypatch)
    _, src, _, path, b = setup(env)
    j = run_job(env, path, b)
    from types import SimpleNamespace

    recorded = assessment.collect(
        {
            "pages": [parse(HTML, SITE)[0]],
            "requests": [{"http": 200, "url": SITE, "bytes": len(HTML)}],
        },
        SimpleNamespace(policy=SOURCE, base_url=SITE),
    )
    with env.admin.begin() as c:
        update(
            c,
            s.research_jobs,
            "tob",
            j,
            status="captured",
            capture=recorded,
            lease="old",
            lease_until=now() - timedelta(seconds=2),
        )
    assert (
        Worker(["research"]).tick() and not calls and view(env, j)["status"] == "ready"
    )


def test_withdrawn_source_wipes_content_and_blocks_future_reads(env, monkeypatch):
    calls = capture(monkeypatch)
    _, src, _, path, b = setup(env)
    j = run_job(env, path, b)
    assert Worker(["research"]).tick() and len(calls) == 2
    data(call(env, "POST", BASE + "research-sources/" + src + "/revoke", {}))
    r = view(env, j)
    assert not r["target"] and r["report"] is None and r["status"] == "expired"
    assert call(env, "POST", path + "/jobs", b).status_code == 403


def test_no_site_storage_rights_no_registration_or_job(env):
    assert (
        call(
            env,
            "POST",
            BASE + "research-sources",
            SOURCE | {"allow_evidence_storage": False},
        ).status_code
        == 422
    )
    assert (
        call(
            env, "POST", BASE + "research-sources", SOURCE | {"allow_automation": False}
        ).status_code
        == 422
    )


def test_practice_only_uses_synthetic_company(monkeypatch, env):
    from tests.test_customer_practice import make, begin, step, EngineReply
    from tests.conftest import profile
    from app.llm import MockLLM, ModelOutput
    from app.llm.base import ToolCall

    class Sim(MockLLM):
        def generate(self, req):
            if req.mode == "tools" and not req.tool_history:
                return ModelOutput(
                    finish_reason="tool_calls",
                    actual_model="mock",
                    provider_request_id="qa",
                    tool_calls=[
                        ToolCall(id="one", name=net.TOOL, args={"company_url": SITE})
                    ],
                )
            if req.tool_history:
                return ModelOutput(
                    text="虚构企业资料待核实。",
                    actual_model="mock",
                    provider_request_id="qa",
                    finish_reason="stop",
                )
            return super().generate(req)

    calls = capture(monkeypatch)
    spec = profile()
    spec["config"].update(model_provider="mock", research_tools=[net.TOOL])
    a = make(env, spec)
    sim = begin(env, a, max_turns=1)
    r = step(env, sim, Sim())
    assert not calls
    with env.admin.connect() as c:
        tools = c.scalar(
            select(s.practice_turns.c.tools).where(s.practice_turns.c.sim_id == sim)
        )
    t = next(iter(tools.values()))
    assert t["external"] is False and t["data"]["mode"] == "simulation"
