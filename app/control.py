from sqlalchemy import select
from app import schema as s
from app.catalog import agent, require_model_operator
from app.chat import access, append, source_message
from app.channels import barrier, create_intent, reconcile, set_status, unresolved
from app.db import insert, now, reject, row, uid, update
from app.handoff import cancel_automatic, freeze
from app.work import cancel, change_state, enqueue


def task_access(c, w, id, *, history=False):
    tk = row(c, s.tasks, w.tenant, id)
    if tk.deleted_at and not history:
        reject(404, "NOT_FOUND", "任务已删除或不可见")
    return tk, agent(c, w, tk.agent_id, history=history)


def create_task(c, w, b):
    a = agent(c, w, b.agent_id)
    if a.disabled:
        reject(409, "PROFILE_DISABLED", "角色已停用")
    require_model_operator(c, w, a, b.agent_version)
    if (a.workflow == "sales") != (b.purpose == "outreach"):
        reject(422, "VALIDATION_ERROR", "任务用途与工作流不符")
    id = insert(
        c,
        s.tasks,
        w.tenant,
        uid("task"),
        agent_id=a.id,
        agent_version=b.agent_version,
        purpose=b.purpose,
        external_ref=b.external_ref,
        display_name=b.name,
        note=b.note,
        status="pending",
    )
    return 201, dict(task_id=id, agent_version=b.agent_version, status="pending")


def latest_pending(c, t, se, tk, a, cv):
    if (
        cv.owner != "auto"
        or cv.opted_out
        or not cv.permitted
        or cv.active_session != se.id
    ):
        return
    q = (
        select(s.messages)
        .where(
            s.messages.c.tenant == t,
            s.messages.c.session_id == se.id,
            s.messages.c.kind == "inbound",
            ~s.messages.c.handled,
        )
        .order_by(s.messages.c.seq.desc())
    )
    m = c.execute(q.limit(1)).mappings().first()
    if not m:
        return
    # Reassess the latest outstanding input. A prior human reply closes all earlier inputs.
    previous = row(c, s.runs, t, m.run_id) if m.run_id else None
    if previous and previous.status in {"queued", "retry_wait", "running"}:
        return
    rid = enqueue(
        c,
        t,
        a.product_id,
        "inbound",
        task=tk.id,
        sid=se.id,
        queue=a.workflow,
        payload={"message_id": m.id, "epoch": cv.epoch, "model_owner": a.owner_id},
    )
    update(c, s.messages, t, m.id, run_id=rid)
    c.execute(
        s.messages.update()
        .where(
            s.messages.c.tenant == t,
            s.messages.c.session_id == se.id,
            s.messages.c.kind == "inbound",
            s.messages.c.seq < m.seq,
        )
        .values(handled=True)
    )


def task_command(c, w, id, cmd, reason=None, *, retiring=False):
    t = w.tenant
    tk, a = task_access(c, w, id)
    rules = {
        "start": ({"pending"}, "running", "started"),
        "resume": ({"paused"}, "running", "resumed"),
        "pause": ({"running", "waiting"}, "paused", None),
        "stop": ({"pending", "running", "waiting", "paused"}, "cancelled", None),
    }
    if cmd in {"start", "resume"}:
        require_model_operator(c, w, a, tk.agent_version)
    allowed, status, operation = rules[cmd]
    if retiring and cmd == "stop" and tk.status in {"cancelled", "completed", "failed"}:
        allowed, status = {tk.status}, tk.status
    if tk.status not in allowed or (a.disabled and cmd in {"start", "resume"}):
        reject(409, "STATE_CONFLICT", "当前任务状态不允许该操作")
    update(c, s.tasks, t, id, status=status, reason=reason)
    for se in c.execute(
        select(s.sessions).where(s.sessions.c.tenant == t, s.sessions.c.task_id == id)
    ).mappings():
        cv = row(c, s.conversations, t, se.conversation_id)
        if se.status == "ended" or cv.active_session != se.id:
            continue
        if cmd in {"stop", "pause"}:
            cancel_automatic(c, t, se.id, status)
            if cmd == "stop" or cv.owner == "auto":
                change_state(
                    c, t, se, tk, a.product_id, "ended" if cmd == "stop" else "paused"
                )
                fields = {"epoch": cv.epoch + 1}
                if cmd == "stop":
                    fields.update(active_session=None, owner="auto")
                    if se.handoff_id:
                        update(
                            c,
                            s.handoffs,
                            t,
                            se.handoff_id,
                            status="closed",
                            note="任务已停止",
                        )
                        update(c, s.sessions, t, se.id, handoff_id=None)
                update(c, s.conversations, t, cv.id, **fields)
        if cmd == "resume" and cv.owner == "auto":
            enqueue(
                c,
                t,
                a.product_id,
                "release_graph",
                sid=se.id,
                task=tk.id,
                queue=a.workflow,
            )
            change_state(c, t, se, tk, a.product_id, "waiting")
            latest_pending(c, t, se, tk, a, cv)
    if cmd in {"pause", "stop"}:
        control_runs = select(s.runs).where(
            s.runs.c.tenant == t,
            s.runs.c.task_id == id,
            s.runs.c.kind.in_(["started", "resumed"]),
            s.runs.c.status.in_(["queued", "retry_wait", "running"]),
        )
        for old in c.execute(control_runs).mappings():
            cancel(c, t, old, status)
    if operation:
        rid = enqueue(c, t, a.product_id, operation, task=id, queue=a.workflow)
        return 202, dict(task_id=id, status=status, run_id=rid)
    return 200, dict(task_id=id, status=status, auto_allowed=False)


def handoff(c, w, sid, b):
    se, tk, a, cv = access(c, w, sid)
    if (
        se.status == "ended"
        or tk.status in {"cancelled", "failed", "completed"}
        or cv.active_session != sid
    ):
        reject(409, "STATE_CONFLICT", "对话已关闭")
    for mid in b.evidence_refs:
        source_message(c, w.tenant, sid, mid)
    h, epoch, created = freeze(c, w.tenant, se, tk, a, cv, b.reason, b.evidence_refs)
    return 201 if created else 200, dict(
        handoff_id=h,
        session_id=sid,
        status="handoff_pending" if created else se.status,
        owner_epoch=epoch,
    )


def claim(c, w, sid, b):
    t = w.tenant
    se, tk, a, cv = access(c, w, sid)
    if (
        se.handoff_id != b.handoff_id
        or cv.epoch != b.expected_epoch
        or cv.owner != "pending"
    ):
        reject(409, "OWNER_CHANGED", "认领人或发送权已变化")
    update(c, s.handoffs, t, b.handoff_id, owner=w.actor, status="human_owned")
    update(c, s.conversations, t, cv.id, owner=w.actor, epoch=cv.epoch + 1)
    change_state(c, t, se, tk, a.product_id, "human_owned")
    return 200, dict(
        handoff_id=b.handoff_id,
        status="human_owned",
        owner=w.actor,
        owner_epoch=cv.epoch + 1,
    )


def holding(c, w, sid, epoch):
    se, tk, a, cv = access(c, w, sid)
    if (
        cv.owner != w.actor
        or cv.epoch != epoch
        or cv.active_session != sid
        or se.status != "human_owned"
    ):
        reject(409, "OWNER_CHANGED", "发言权已变化")
    return se, tk, a, cv


def human_message(c, w, sid, b):
    t = w.tenant
    se, tk, a, cv = holding(c, w, sid, b.expected_epoch)
    if unresolved(c, t, cv):
        reject(409, "SEND_UNRESOLVED", "上一条发送需要核对")
    rid = enqueue(
        c, t, a.product_id, "human_send", sid=sid, task=tk.id, queue=a.workflow
    )
    r = row(c, s.runs, t, rid)
    denied = barrier(c, t, r, w.actor, cv.epoch, [])
    if denied:
        reject(409, "STATE_CONFLICT", "渠道当前禁止回复")
    mid = append(c, t, se, cv, "human", b.text, run_id=rid)
    send = create_intent(c, t, r, se, cv, w.actor, cv.epoch, mid, [])
    return 202, dict(message_id=mid, send_id=send, run_id=rid, status="queued")


def release(c, w, sid, b):
    t = w.tenant
    se, tk, a, cv = holding(c, w, sid, b.expected_epoch)
    if se.handoff_id != b.handoff_id:
        reject(409, "OWNER_CHANGED", "工单已变化")
    acc = row(c, s.accounts, t, cv.account_id)
    pending = c.scalar(
        select(s.sends.c.id)
        .where(
            s.sends.c.tenant == t,
            s.sends.c.conversation_id == cv.id,
            s.sends.c.status == "pending",
        )
        .limit(1)
    )
    if pending or unresolved(c, t, cv):
        reject(409, "RELEASE_BLOCKED", "尚有待发送或待核对回执")
    closing = b.resolution == "close"
    if not closing:
        require_model_operator(c, w, a, tk.agent_version)
    if not closing and (
        a.disabled
        or cv.opted_out
        or not cv.permitted
        or not acc.enabled
        or (cv.window_until and cv.window_until <= now())
        or tk.status in {"cancelled", "failed", "completed"}
    ):
        reject(409, "RELEASE_BLOCKED", "仍有停发条件")
    state = "ended" if closing else ("paused" if tk.status == "paused" else "waiting")
    update(c, s.handoffs, t, b.handoff_id, status="closed", note=b.note)
    update(
        c,
        s.conversations,
        t,
        cv.id,
        owner="auto",
        epoch=cv.epoch + 1,
        active_session=None if closing else sid,
    )
    update(c, s.sessions, t, sid, handoff_id=None)
    change_state(c, t, se, tk, a.product_id, state)
    if closing and tk.purpose == "support":
        update(c, s.tasks, t, tk.id, status="completed")
    if state not in {"paused", "ended"}:
        enqueue(
            c, t, a.product_id, "release_graph", sid=sid, task=tk.id, queue=a.workflow
        )
        latest_pending(c, t, se, tk, a, row(c, s.conversations, t, cv.id))
    return 200, dict(
        handoff_id=b.handoff_id,
        status=state,
        owner="none" if closing else "auto",
        owner_epoch=cv.epoch + 1,
    )


def edit_task(c, w, id, body):
    task, _ = task_access(c, w, id)
    if task.edit_revision != body.expected_revision:
        reject(409, "REVISION_CONFLICT", "任务资料已改变，请刷新。")
    note = body.note if "note" in body.model_fields_set else task.note
    update(
        c,
        s.tasks,
        w.tenant,
        id,
        display_name=body.name,
        note=note,
        edit_revision=task.edit_revision + 1,
    )
    return 200, {
        "task_id": id,
        "name": body.name,
        "note": note,
        "edit_revision": task.edit_revision + 1,
    }


def remove_task(c, w, id):
    task, _ = task_access(c, w, id, history=True)
    if not task.deleted_at:
        task_command(c, w, id, "stop", "已删除任务", retiring=True)
        # Include human replies and graph-resume jobs that ordinary pause/stop need not drain.
        open_sends = (
            select(s.sends)
            .join(
                s.runs,
                (s.runs.c.tenant == s.sends.c.tenant)
                & (s.runs.c.id == s.sends.c.run_id),
            )
            .where(
                s.sends.c.tenant == w.tenant,
                s.runs.c.task_id == id,
                s.sends.c.status.in_(["pending", "unknown", "dispatching"]),
            )
        )
        for ledger in c.execute(open_sends).mappings():
            if ledger.status == "pending":
                set_status(
                    c,
                    w.tenant,
                    row(c, s.runs, w.tenant, ledger.run_id),
                    ledger,
                    "cancelled",
                )
            else:
                reconcile(c, w.tenant, ledger)
        runs = select(s.runs).where(
            s.runs.c.tenant == w.tenant,
            s.runs.c.task_id == id,
            s.runs.c.status.in_(["queued", "running", "retry_wait"]),
        )
        for run in c.execute(runs).mappings():
            cancel(c, w.tenant, run, "task_deleted")
        update(c, s.tasks, w.tenant, id, deleted_at=now())
    return 200, {"task_id": id, "deleted": True}


def remove_agent(c, w, id):
    a = agent(c, w, id, history=True)
    if not a.deleted_at:
        ids = c.scalars(
            select(s.tasks.c.id).where(
                s.tasks.c.tenant == w.tenant,
                s.tasks.c.agent_id == id,
                s.tasks.c.deleted_at.is_(None),
            )
        ).all()
        for tid in ids:
            remove_task(c, w, tid)
        update(c, s.agents, w.tenant, id, disabled=True, deleted_at=now())
    return 200, {"agent_id": id, "deleted": True}
