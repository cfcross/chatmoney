"""Session binding, original messages and source-backed memory."""

from sqlalchemy import select, func
from app import schema as s
from app.catalog import agent, require_model_operator
from app.db import insert, reject, row, uid, update, now
from app.work import enqueue


def access(c, w, id, *, history=False):
    se = row(c, s.sessions, w.tenant, id)
    task = row(c, s.tasks, w.tenant, se.task_id)
    if task.deleted_at and not history:
        reject(404, "NOT_FOUND", "任务已删除或不可见")
    a = agent(c, w, task.agent_id, history=history)
    return se, task, a, row(c, s.conversations, w.tenant, se.conversation_id)


def valid_source(c, t, id, customer, product):
    if id:
        lead = row(c, s.leads, t, id)
        if lead.product_id != product or lead.customer_id != customer:
            reject(404, "NOT_FOUND", "来源不属于当前客户")


def create(c, w, b):
    t = w.tenant
    a = agent(c, w, b.agent_id)
    if a.disabled:
        reject(409, "PROFILE_DISABLED", "角色已停用")
    cv = row(c, s.conversations, t, b.conversation_id)
    account = row(c, s.accounts, t, cv.account_id)
    if cv.customer_id != b.customer_id or account.product_id != a.product_id:
        reject(404, "NOT_FOUND", "客户或渠道不可见")
    valid_source(c, t, b.context.source_ref, b.customer_id, a.product_id)
    if cv.active_session:
        se, task, _, _ = access(c, w, cv.active_session)
        if task.agent_id == a.id and (
            b.task_id == task.id or not b.task_id and a.workflow == "support"
        ):
            return 200, dict(
                session_id=se.id,
                task_id=task.id,
                agent_version=task.agent_version,
                status=se.status,
                owner_epoch=cv.epoch,
            )
        reject(409, "CONVERSATION_BUSY", "渠道已有负责会话")
    if b.task_id:
        task = row(c, s.tasks, t, b.task_id)
        if task.deleted_at:
            reject(404, "NOT_FOUND", "任务已删除或不可见")
        if task.agent_id != a.id:
            reject(422, "VALIDATION_ERROR", "角色与任务不匹配")
        if task.status not in {"running", "waiting"}:
            reject(409, "STATE_CONFLICT", "任务未启动")
        require_model_operator(c, w, a, task.agent_version)
        tid, n = task.id, task.agent_version
    else:
        if a.workflow != "support":
            reject(422, "VALIDATION_ERROR", "销售会话需要 task_id")
        require_model_operator(c, w, a, a.published)
        n = a.published
        tid = insert(
            c,
            s.tasks,
            t,
            uid("t"),
            agent_id=a.id,
            agent_version=n,
            purpose="support",
            status="running",
        )
    sid = insert(
        c,
        s.sessions,
        t,
        uid("s"),
        task_id=tid,
        conversation_id=cv.id,
        customer_id=b.customer_id,
        status="waiting",
        context=b.context.model_dump(exclude_none=True),
    )
    insert(c, s.memory, t, sid)
    update(c, s.conversations, t, cv.id, active_session=sid)
    from app.work import emit

    emit(
        c,
        t,
        a.product_id,
        "session.state_changed",
        sid=sid,
        task=tid,
        data={"status": "waiting"},
    )
    return 201, dict(
        session_id=sid,
        task_id=tid,
        agent_version=n,
        status="waiting",
        owner_epoch=cv.epoch,
    )


def append(c, t, se, cv, kind, body, **fields):
    if kind == "draft" and fields.get("run_id"):
        existing = c.scalar(
            select(s.messages.c.id).where(
                s.messages.c.tenant == t,
                s.messages.c.session_id == se.id,
                s.messages.c.run_id == fields["run_id"],
                s.messages.c.kind == "draft",
            )
        )
        if existing:
            return existing
    seq = c.scalar(
        select(func.coalesce(func.max(s.messages.c.seq), 0) + 1).where(
            s.messages.c.tenant == t, s.messages.c.session_id == se.id
        )
    )
    return insert(
        c,
        s.messages,
        t,
        uid("m"),
        session_id=se.id,
        account_id=cv.account_id,
        kind=kind,
        body=body,
        seq=seq,
        **fields,
    )


def is_optout(value):
    return any(
        v in value.lower()
        for v in (
            "不要联系",
            "别再联系",
            "退订",
            "unsubscribe",
            "停止联系",
            "不要再联系",
        )
    )


def inbound(c, w, sid, b):
    t = w.tenant
    se, task, a, cv = access(c, w, sid)
    valid_source(c, t, b.context.source_ref, se.customer_id, a.product_id)
    prev = (
        c.execute(
            select(s.messages).where(
                s.messages.c.tenant == t,
                s.messages.c.account_id == cv.account_id,
                s.messages.c.external_id == b.external_message_id,
            )
        )
        .mappings()
        .first()
    )
    if prev:
        if prev.body != b.text or prev.session_id != sid:
            reject(409, "IDEMPOTENCY_CONFLICT", "外部编号与已有来信冲突")
        return (202 if prev.run_id else 200), dict(
            message_id=prev.id,
            run_id=prev.run_id,
            status="queued" if prev.run_id else "recorded_only",
        )
    mid = append(
        c,
        t,
        se,
        cv,
        "inbound",
        b.text,
        external_id=b.external_message_id,
        occurred=b.occurred_at or now(),
    )
    if is_optout(b.text):
        update(
            c,
            s.conversations,
            t,
            cv.id,
            opted_out=True,
            permitted=False,
            epoch=cv.epoch + 1,
        )
        from app.handoff import cancel_automatic

        cancel_automatic(c, t, se.id, "opted_out")
    if (
        a.disabled
        or cv.opted_out
        or is_optout(b.text)
        or cv.owner != "auto"
        or cv.active_session != sid
        or task.status not in {"running", "waiting"}
        or se.status in {"ended", "error", "paused"}
    ):
        return 200, dict(message_id=mid, run_id=None, status="recorded_only")
    # An authenticated inbound channel delivers a customer's reply to an already bound session.
    if w.session_id or "adapter" not in w.roles:
        require_model_operator(c, w, a, task.agent_version)
    rid = enqueue(
        c,
        t,
        a.product_id,
        "inbound",
        sid=sid,
        task=task.id,
        queue=a.workflow,
        payload={"message_id": mid, "epoch": cv.epoch, "model_owner": a.owner_id},
    )
    update(c, s.messages, t, mid, run_id=rid)
    update(c, s.tasks, t, task.id, status="running")
    return 202, dict(message_id=mid, run_id=rid, status="queued")


def records(c, t, sid, limit, after):
    q = select(s.messages).where(
        s.messages.c.tenant == t, s.messages.c.session_id == sid
    )
    if after:
        cur = c.execute(q.where(s.messages.c.id == after)).mappings().first()
        if not cur:
            reject(422, "INVALID_CURSOR", "会话分页游标无效")
        q = q.where(s.messages.c.seq > cur.seq)
    rows = list(c.execute(q.order_by(s.messages.c.seq).limit(limit + 1)).mappings())
    out = []
    for m in rows[:limit]:
        delivery = c.scalar(
            select(s.sends.c.status).where(
                s.sends.c.tenant == t, s.sends.c.message_id == m.id
            )
        )
        out.append(
            dict(
                message_id=m.id,
                kind=m.kind,
                text=m.body,
                seq=m.seq,
                delivery=delivery or "not_sent",
                at=m.created.isoformat(),
            )
        )
    return {
        "items": out,
        "next_cursor": out[-1]["message_id"] if len(rows) > limit else None,
    }


def memory_view(c, t, sid):
    m = row(c, s.memory, t, sid)
    return dict(
        revision=m.revision,
        summary=m.summary,
        pending=m.pending,
        facts=[{"key": k, **v} for k, v in m.facts.items()],
    )


def source_message(c, t, sid, ref):
    m = row(c, s.messages, t, ref)
    if m.session_id != sid:
        reject(404, "NOT_FOUND", "来源不属于该会话")
    return m


def correct(c, w, sid, b):
    access(c, w, sid)
    m = row(c, s.memory, w.tenant, sid)
    if m.revision != b.expected_revision:
        reject(409, "REVISION_CONFLICT", "记忆已变更")
    source_message(c, w.tenant, sid, b.source_ref)
    facts = m.facts | {
        b.key: dict(value=b.value, source_ref=b.source_ref, corrected=True)
    }
    insert(
        c,
        s.history,
        w.tenant,
        uid("mc"),
        session_id=sid,
        revision=m.revision + 1,
        key=b.key,
        old=m.facts.get(b.key),
        value=b.value,
        source_ref=b.source_ref,
        actor=w.actor,
        reason=b.reason,
    )
    update(c, s.memory, w.tenant, sid, revision=m.revision + 1, facts=facts)
    return 200, dict(
        revision=m.revision + 1, key=b.key, value=b.value, source_ref=b.source_ref
    )
