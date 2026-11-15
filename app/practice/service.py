"""Scope, snapshot and lifecycle. No path here creates live tasks, grants or customers."""

import hashlib
import json
from typing import Literal
from sqlalchemy import select, func
from pydantic import Field
from app import catalog, schema as s
from app.contracts import Model, Pos
from app.db import insert, now, reject, row, uid, update
from app.practice import evaluation, personas


class Start(Model):
    source: Literal["draft", "published"] = "draft"
    version: Pos
    persona: str | None = None
    repeat_from: str | None = Field(None, max_length=100)
    mode: Literal["step", "auto"] = "step"
    max_turns: int = Field(8, ge=1, le=16)
    token_limit: int = Field(64000, ge=1000, le=150000)
    accept_cost: bool = False
    quality_assist: bool = False


class Control(Model):
    expected_revision: Pos


class Review(Control):
    decision: Literal["satisfied", "revise"]
    note: str = Field("", max_length=1000)


class Approval(Control):
    decision: Literal["recommend", "return"]
    note: str = Field(min_length=1, max_length=1000)


def own(c, w, aid):
    a = catalog.agent(c, w, aid, history=True)
    if a.owner_id != w.actor:
        reject(404, "NOT_FOUND", "仅助手创建者可查看对练。")
    return a


def fetch(c, w, id, locked=False):
    q = row(c, s.practice, w.tenant, id, locked=locked)
    if q.owner_id != w.actor:
        reject(404, "NOT_FOUND", "对练不存在或不可见。")
    w.product(q.snapshot["product"])
    return q


def pack(a, config, name):
    return dict(
        config=dict(config), name=name, product=a.product_id, workflow=a.workflow
    )


def fingerprint(snap):
    return hashlib.sha256(
        json.dumps(
            snap, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def snapshot(c, w, a, source, n):
    if source == "draft":
        if n != a.revision:
            reject(409, "REVISION_CONFLICT", "草稿发生变化，请重新打开。")
        return pack(a, a.draft, a.name)
    v = catalog.version(c, w.tenant, a.id, n)
    return pack(a, v.config, v.name)


def current(c, q):
    a = row(c, s.agents, q.tenant, q.agent_id)
    if a.disabled or a.deleted_at:
        return False
    if q.source == "draft":
        return (
            q.source_version == a.revision
            and fingerprint(pack(a, a.draft, a.name)) == q.fingerprint
        )
    if q.source_version != a.published:
        return False
    v = catalog.version(c, q.tenant, a.id, q.source_version)
    return fingerprint(pack(a, v.config, v.name)) == q.fingerprint


def checks(c, w, a, snap):
    if a.disabled or a.deleted_at:
        reject(409, "PROFILE_DISABLED", "助手已停用。")
    cfg = dict(snap["config"])
    if cfg.get("model_provider") not in ("mock", "deepseek"):
        reject(
            422, "PRACTICE_MODEL", "请先选择自己的模型或明确选择模拟模型，并保存草稿。"
        )
    if (a.template_origin or {}).get("requirements", {}).get(
        "knowledge", not a.template_origin
    ) and not cfg.get("knowledge"):
        reject(422, "PRACTICE_KNOWLEDGE", "请先选择并确认已发布的产品资料。")
    catalog.validate_profile(c, w, a, cfg)
    if set(cfg.get("tool_allowlist", [])) - set(
        catalog.product_tools(c, w.tenant, [a.product_id])[a.product_id]
    ):
        reject(422, "PRACTICE_TOOLS", "请检查所选工具的产品授权。")


def choices(c, w, aid):
    a = own(c, w, aid)
    role, topic, kind = personas.scenario(a.template_origin, a.draft)
    return dict(
        recommended=kind,
        role=role,
        personas=[
            dict(id=k, name=v[0], description=v[1], purpose=v[2])
            for k, v in personas.PERSONAS.items()
        ],
    )


def create(c, w, aid, b):
    a = own(c, w, aid)
    snap = snapshot(c, w, a, b.source, b.version)
    checks(c, w, a, snap)
    deep = snap["config"]["model_provider"] == "deepseek"
    if deep and not b.accept_cost:
        reject(422, "PRACTICE_COST", "双方共用你的个人模型，可能产生费用，请确认。")
    if b.quality_assist and not deep:
        reject(422, "PRACTICE_QUALITY", "模拟模型演示不提供模型质量评估。")
    active = c.scalar(
        select(func.count())
        .select_from(s.practice)
        .where(
            s.practice.c.tenant == w.tenant,
            s.practice.c.owner_id == w.actor,
            s.practice.c.status.in_(["queued", "running"]),
        )
    )
    if active >= 3:
        reject(409, "PRACTICE_BUSY", "已有三次对练排队或运行，请先暂停或结束。")
    role, topic, default = personas.scenario(a.template_origin, snap["config"])
    previous = fetch(c, w, b.repeat_from) if b.repeat_from else None
    if previous and (
        previous.agent_id != aid or (b.persona and previous.persona != b.persona)
    ):
        reject(422, "PRACTICE_SCENARIO", "只能复用同一助手、同一客户类型的场景。")
    if previous:
        role, topic = previous.setting["role"], previous.setting["topic"]
    facts = (
        row(c, s.buyer_private, w.tenant, previous.id).facts
        if previous
        else personas.hidden_facts(topic)
    )
    kind = b.persona or (previous.persona if previous else default)
    if kind not in personas.PERSONAS:
        reject(422, "INVALID_PERSONA", "请选择列表中的客户类型。")
    id = uid("sim")
    insert(
        c,
        s.practice,
        w.tenant,
        id,
        owner_id=w.actor,
        agent_id=a.id,
        source=b.source,
        source_version=b.version,
        snapshot=snap,
        fingerprint=fingerprint(snap),
        persona=kind,
        setting=dict(
            role=role,
            topic=topic,
            product=row(c, s.products, w.tenant, a.product_id).name,
            quality=b.quality_assist,
            repeat_from=b.repeat_from,
        ),
        status="queued",
        mode=b.mode,
        max_turns=b.max_turns,
        token_limit=b.token_limit,
    )
    insert(c, s.buyer_private, w.tenant, id, facts=facts)
    return 202, dict(simulation_id=id, status="queued", revision=1)


def turns(c, q):
    return (
        c.execute(
            select(s.practice_turns)
            .where(
                s.practice_turns.c.tenant == q.tenant, s.practice_turns.c.sim_id == q.id
            )
            .order_by(s.practice_turns.c.num)
        )
        .mappings()
        .all()
    )


def completed(c, q):
    return sum(v.action is not None for v in turns(c, q))


def brief(q):
    return dict(
        id=q.id,
        agent_id=q.agent_id,
        name=q.snapshot["name"],
        source=q.source,
        version=q.source_version,
        fingerprint=q.fingerprint,
        persona=q.persona,
        repeat_from=q.setting.get("repeat_from"),
        persona_name=personas.PERSONAS[q.persona][0],
        status=q.status,
        mode=q.mode,
        revision=q.revision,
        turns=q.turns,
        max_turns=q.max_turns,
        token_limit=q.token_limit,
        accounted=q.accounted,
        usage=q.usage,
        created=q.created.isoformat(),
        finished=q.finished.isoformat() if q.finished else None,
        stop_reason=q.stop_reason,
        stop_label=evaluation.label(q.stop_reason) if q.stop_reason else None,
        review=q.review,
        candidate=q.candidate,
        provider=q.snapshot["config"].get("model_provider"),
        model=q.snapshot["config"].get("model_name") or "模拟模型",
    )


def history(c, w, aid, before=None, limit=20, finished_only=False):
    own(c, w, aid)
    p = s.practice.c
    query = select(s.practice).where(
        p.tenant == w.tenant, p.owner_id == w.actor, p.agent_id == aid
    )
    if finished_only:
        query = query.where(p.status.in_(["finished", "failed"]))
    if before:
        anchor = fetch(c, w, before)
        if anchor.agent_id != aid:
            reject(404, "NOT_FOUND", "此历史位置不属于当前助手。")
        query = query.where(
            (p.created < anchor.created)
            | ((p.created == anchor.created) & (p.id < anchor.id))
        )
    rows = (
        c.execute(query.order_by(p.created.desc(), p.id.desc()).limit(limit + 1))
        .mappings()
        .all()
    )
    return dict(
        items=[brief(q) for q in rows[:limit]],
        next_before=rows[limit - 1].id if len(rows) > limit else None,
    )


def detail(c, w, id):
    q = fetch(c, w, id)
    rows = turns(c, q)
    calls = c.execute(
        select(
            s.practice_calls.c.num,
            s.practice_calls.c.stage,
            s.practice_calls.c.state,
            s.practice_calls.c.metrics,
            s.practice_calls.c.charged,
        )
        .where(s.practice_calls.c.tenant == q.tenant, s.practice_calls.c.sim_id == q.id)
        .order_by(s.practice_calls.c.created)
    ).mappings()
    traces = [dict(v) for v in calls]
    reviews = c.execute(
        select(s.practice_reviews)
        .where(
            s.practice_reviews.c.tenant == q.tenant, s.practice_reviews.c.sim_id == q.id
        )
        .order_by(s.practice_reviews.c.created)
    ).mappings()
    return brief(q) | dict(
        current=current(c, q),
        role=q.snapshot["config"].get("persona"),
        goal=q.snapshot["config"].get("goal"),
        messages=[
            dict(
                turn=v.num,
                buyer=v.buyer,
                seller=v.seller,
                action=v.action,
                reason=v.reason,
                reason_label=evaluation.label(v.reason) if v.reason else None,
                evidence=v.evidence,
                tools=list(v.tools.values()),
            )
            for v in rows
            if v.buyer
        ],
        events=traces,
        evaluation=evaluation.report(q, rows),
        review_history=[
            dict(kind=v.kind, note=v.note, at=v.created.isoformat()) for v in reviews
        ],
        platform_access=False,
    )


def guard(q, b):
    if q.revision != b.expected_revision:
        reject(409, "PRACTICE_CONFLICT", "对练已发生变化，请刷新后再操作。")


def change(c, q, **kw):
    update(c, s.practice, q.tenant, q.id, revision=q.revision + 1, updated=now(), **kw)


def control(c, w, id, action, b):
    q = fetch(c, w, id, True)
    # Stopping/pausing a live loop must still win if a turn just finished.
    if action not in {"stop", "pause"}:
        guard(q, b)
    if q.status in {"finished", "failed"}:
        reject(409, "STATE_CONFLICT", "对练已经结束")
    if action == "stop":
        change(
            c,
            q,
            status="finished",
            stop_reason="manual",
            finished=now(),
            turns=completed(c, q),
        )
    elif action == "pause" and q.status in {"running", "ready", "queued"}:
        change(c, q, status="paused")
    elif action in {"next", "auto", "resume"} and q.status in {"ready", "paused"}:
        change(
            c,
            q,
            status="queued",
            mode=q.mode
            if action == "resume"
            else ("auto" if action == "auto" else "step"),
        )
    else:
        reject(409, "STATE_CONFLICT", "当前不能重复推进或执行这个操作")
    rr = row(c, s.practice, w.tenant, id)
    return (202 if rr.status == "queued" else 200), dict(
        id=id, status=rr.status, revision=rr.revision
    )


def record(c, w, q, kind, note, **kw):
    insert(
        c,
        s.practice_reviews,
        w.tenant,
        uid("review"),
        sim_id=q.id,
        actor=w.actor,
        kind=kind,
        note=note,
        fingerprint=q.fingerprint,
    )
    change(c, q, **kw)
    return 200, dict(id=q.id, revision=q.revision + 1, platform_access=False, **kw)


def review(c, w, id, b):
    q = fetch(c, w, id, True)
    guard(q, b)
    if q.status not in {"finished", "failed"} or q.candidate in {
        "submitted",
        "pending_platform",
    }:
        reject(409, "STATE_CONFLICT", "请先结束对练，已提交记录不能改写")
    return record(c, w, q, b.decision, b.note, review=b.decision, candidate="none")


def submit(c, w, id, b):
    q = fetch(c, w, id, True)
    guard(q, b)
    if (
        q.review != "satisfied"
        or q.candidate != "none"
        or not current(c, q)
        or not evaluation.report(q, turns(c, q))["eligible"]
    ):
        reject(
            409, "PRACTICE_REVIEW", "需确认有效对练、未变更的配置快照，且无硬规则违规。"
        )
    a = own(c, w, q.agent_id)
    checks(c, w, a, q.snapshot)
    return record(
        c,
        w,
        q,
        "submitted",
        "已审阅的配置提交审核；未获得平台权限。",
        candidate="submitted",
    )


def inbox(c, w):
    items = []
    for q in c.execute(
        select(s.practice)
        .where(s.practice.c.tenant == w.tenant, s.practice.c.candidate == "submitted")
        .order_by(s.practice.c.updated)
        .limit(150)
    ).mappings():
        if q.snapshot["product"] in w.products:
            summary = evaluation.report(q, turns(c, q))
            items.append(
                dict(
                    id=q.id,
                    name=q.snapshot["name"],
                    source=q.source,
                    version=q.source_version,
                    revision=q.revision,
                    turns=summary["actual_turns"],
                    findings=sorted({i["rule"] for i in summary["findings"]}),
                    stop_label=evaluation.label(q.stop_reason),
                    provider=q.snapshot["config"]["model_provider"],
                    current=current(c, q),
                    fingerprint=q.fingerprint,
                )
            )
    return dict(items=items)


def approval(c, w, id, b):
    q = row(c, s.practice, w.tenant, id, locked=True)
    w.product(q.snapshot["product"])
    guard(q, b)
    if q.candidate != "submitted" or (b.decision == "recommend" and not current(c, q)):
        reject(409, "PRACTICE_REVIEW", "候选已变更或不在审核队列。")
    return record(
        c,
        w,
        q,
        b.decision,
        b.note,
        candidate="pending_platform" if b.decision == "recommend" else "returned",
    )
