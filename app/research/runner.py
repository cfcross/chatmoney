"""Durable, two-stage research graph. No live sales Session is created."""

import logging
from random import uniform
from contextlib import contextmanager
from datetime import timedelta
from typing import TypedDict
from urllib.parse import urlsplit
from sqlalchemy import and_, or_, select, text
from langgraph.graph import StateGraph, START, END
from app import schema as s, knowledge
from app.db import (
    engine,
    transaction,
    row,
    update,
    lock,
    uid,
    now,
    model_scope,
    Problem,
)
from app.llm import ModelError
from app.llm.deepseek import ERROR_MESSAGES
from app.tools.gateway import Gateway
from app.research import assessment, service as svc
from app.research.network import Denied


class Stale(Exception):
    pass


class State(TypedDict):
    stage: str


class Execution:
    def __init__(self, t, id, lease, model=None):
        self.tenant, self.id, self.lease, self.model = t, id, lease, model

    @contextmanager
    def tx(self, verify=True):
        with transaction(self.tenant) as c:
            lock(c, "mutation", self.tenant)
            j = row(c, s.research_jobs, self.tenant, self.id, locked=True)
            if (
                j.lease != self.lease
                or j.status not in svc.ACTIVE
                or j.expires <= now()
            ):
                raise Stale()
            if verify:
                model_scope(c, j.owner_id)
                w = svc.identity(c, self.tenant, j.owner_id)
                g = row(c, s.research_grants, self.tenant, j.grant_id)
                a, v, src = svc.verify(c, w, g)
                if (
                    a.id != j.agent_id
                    or a.product_id != j.product_id
                    or j.version != g.version
                ):
                    raise Denied("SCOPE_CHANGED")
                yield c, j, v, src
            else:
                yield c, j, None, None

    def permit(self):
        with self.tx() as (_, j, v, src):
            if j.status != "fetching":
                raise Stale()
            return dict(
                target=j.target,
                scope=src.base_url,
                limits={
                    k: j.input[k]
                    for k in ("max_pages", "max_depth", "timeout", "request_budget")
                },
            )

    def guard(self):
        try:
            self.permit()
        except (Problem, Stale):
            raise Denied("SCOPE_CHANGED") from None

    def audit(self, status, size):
        with self.tx() as (c, j, _, __):
            update(
                c,
                s.research_jobs,
                j.tenant,
                j.id,
                events=j.events + [svc.event("http", status=status, bytes=size)],
                updated=now(),
            )

    def stage(self, stage):
        with self.tx() as (_, j, __, ___):
            return {"stage": j.status}

    def fetch(self, _):
        # One physical connection protects the website read and capture commit. PostgreSQL
        # releases this transaction lock on rollback or disconnect, including worker death.
        with engine().begin() as host_guard:
            with self.tx() as (c, job, _, __):
                if job.status != "queued":
                    raise Stale()
                hostname = urlsplit(job.target).hostname
                acquired = host_guard.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "company-research-host:" + hostname},
                )
                if not acquired:
                    events = job.events
                    if not events or events[-1]["kind"] != "site_wait":
                        events = events + [svc.event("site_wait")]
                    update(c, s.research_jobs, job.tenant, job.id,
                           lease=None, lease_until=now() + timedelta(seconds=uniform(4, 8)),
                           updated=now(), events=events)
                    return {"stage": "queued"}
                update(c, s.research_jobs, job.tenant, job.id, status="fetching",
                       updated=now(), events=job.events + [svc.event("claimed")])
            return self.capture()

    def capture(self):
        raw = Gateway().research(self)
        with self.tx() as (c, j, _, src):
            cap = assessment.collect(raw, src)
            update(
                c,
                s.research_jobs,
                j.tenant,
                j.id,
                status="captured",
                capture=cap,
                updated=now(),
                events=j.events
                + [
                    svc.event(
                        "read", requests=len(cap["requests"]), pages=len(raw["pages"])
                    )
                ],
            )
        return {"stage": "captured"}

    def analyze(self, _):
        with self.tx() as (c, j, v, src):
            if j.status != "captured":
                raise Stale()
            cfg = v.config
            if cfg["model_provider"] == "deepseek" and not (
                src.policy["allow_model_processing"] and j.input["accept_model_cost"]
            ):
                raise Denied("SCOPE_CHANGED")
            refs = knowledge.retrieve(
                c, j.tenant, j.product_id, cfg, j.input["research_goal"]
            )
            if not refs:
                refs = knowledge.retrieve(
                    c, j.tenant, j.product_id, cfg, "", product_overview=True
                )
            update(
                c,
                s.research_jobs,
                j.tenant,
                j.id,
                status="model_started",
                updated=now(),
                events=j.events + [svc.event("model_started")],
            )
        result = assessment.compose(j, cfg, j.capture, refs, self.model)
        with self.tx() as (c, j, _, src):
            update(
                c,
                s.research_jobs,
                j.tenant,
                j.id,
                status="ready",
                capture=None,
                report=result | {"sample": src.policy["purpose"] == "acceptance_only"},
                lease=None,
                lease_until=None,
                updated=now(),
                events=j.events + [svc.event("ready")],
            )
        return {"stage": "ready"}

    def drive(self):
        graph = StateGraph(State)
        graph.add_node("resume", self.stage)
        graph.add_node("read", self.fetch)
        graph.add_node("analyze", self.analyze)
        graph.add_edge(START, "resume")
        graph.add_conditional_edges(
            "resume",
            lambda x: "analyze" if x["stage"] == "captured" else "read",
            ["read", "analyze"],
        )
        graph.add_conditional_edges(
            "read", lambda state: "analyze" if state["stage"] == "captured" else END,
            ["analyze", END],
        )
        graph.add_edge("analyze", END)
        graph.compile().invoke({"stage": ""})


def tick(t, model=None):
    tab = s.research_jobs
    with transaction(t) as c:
        lock(c, "mutation", t)
        svc.scrub(c, t, tab.c.expires <= now())
        j = (
            c.execute(
                select(tab)
                .where(
                    tab.c.tenant == t,
                    or_(
                        and_(tab.c.status == "queued",
                             or_(tab.c.lease_until.is_(None), tab.c.lease_until <= now())),
                        and_(
                            tab.c.status.in_(["fetching", "captured", "model_started"]),
                            tab.c.lease_until <= now(),
                        ),
                    ),
                )
                .order_by(tab.c.created, tab.c.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .first()
        )
        if not j:
            return False
        if j.status in ("model_started", "fetching"):
            # A previous network/model request may already have left. Never silently repeat it.
            update(
                c,
                tab,
                t,
                j.id,
                status="failed",
                error="INTERRUPTED",
                lease=None,
                lease_until=None,
                capture=None,
                updated=now(),
                events=j.events + [svc.event("interrupted")],
            )
            return True
        lease = uid("lease")
        update(
            c,
            tab,
            t,
            j.id,
            lease=lease,
            lease_until=now() + timedelta(seconds=180),
            updated=now(),
        )
    ex = Execution(t, j.id, lease, model)
    try:
        ex.drive()
    except Stale:
        pass
    except Exception as e:
        code = (
            e.code
            if isinstance(e, (Denied, ModelError))
            else ("SCOPE_CHANGED" if isinstance(e, Problem) else "NETWORK")
        )
        logging.getLogger("chatmoney.research").warning(
            "research=%s failure=%s type=%s", j.id, code, type(e).__name__
        )
        try:
            with ex.tx(False) as (c, j, _, __):
                update(
                    c,
                    tab,
                    t,
                    j.id,
                    status="failed",
                    error=ERROR_MESSAGES.get(code, code),
                    capture=None,
                    lease=None,
                    lease_until=None,
                    updated=now(),
                    events=j.events + [svc.event("failed", code=code)],
                )
        except Stale:
            pass
    return True
