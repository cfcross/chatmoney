from datetime import timedelta
import pytest
from sqlalchemy import select
from app import schema as s
from app.channels import Sender, MockChannel, reconcile
from app.db import now, transaction, update
from app.worker import Worker, claim, process
from tests.flows import ready, submit, drain, fetch


def test_handoff_between_enqueue_and_dispatch_and_return(env):
    _, tid, sid = ready(env, "toc")
    p = f"/sessions/{sid}"

    def race():
        r = env.req("POST", p + "/handoff", "toc-admin", {"reason": "吴姐接手"})
        assert r.status_code == 201, r.text

    rid = submit(env, "toc", sid, "订单 O7 发货了吗").json()["data"]["run_id"]
    drain(Worker(sender=Sender(after_intent=race)))
    assert fetch(env, "toc", rid)["status"] == "cancelled"
    with transaction("toc") as c:
        assert not c.execute(s.receipts.select()).first()
        assert c.scalar(select(s.sends.c.status)) == "cancelled"
    se = env.req("GET", p, "toc-admin").json()["data"]
    assert se["status"] == "handoff_pending"
    b = {"expected_epoch": se["owner_epoch"], "handoff_id": se["handoff_id"]}
    take = env.req("POST", p + "/takeover", "toc-admin", b)
    assert take.status_code == 200
    assert env.req("POST", p + "/takeover", "toc-admin", b).status_code == 409
    assert (
        submit(env, "toc", sid, "转交后追加").json()["data"]["status"]
        == "recorded_only"
    )
    epoch = take.json()["data"]["owner_epoch"]
    human = env.req(
        "POST",
        p + "/human-messages",
        "toc-admin",
        {"text": "已经人工核对，明天联系您。", "expected_epoch": epoch},
    )
    assert human.status_code == 202, human.text
    drain()
    assert (
        fetch(env, "toc", human.json()["data"]["run_id"])["result"]["delivery"]
        == "accepted"
    )
    back = env.req(
        "POST",
        p + "/release",
        "toc-admin",
        b | dict(expected_epoch=epoch, resolution="resume", note="已回复"),
    )
    assert back.status_code == 200, back.text
    r = submit(env, "toc", sid, "排班功能是什么")
    drain()
    assert (
        fetch(env, "toc", r.json()["data"]["run_id"])["result"]["delivery"]
        == "accepted"
    )


def test_paused_task_future_timer_and_optout(env):
    _, tid, sid = ready(env)
    p = "/tasks/" + tid
    action = env.req(
        "POST",
        f"/sessions/{sid}/actions",
        "tob-business",
        dict(
            kind="follow_up",
            source_ref="lead-c1",
            due_at=(now() + timedelta(days=3)).isoformat(),
        ),
    )
    assert action.status_code == 202
    rid = action.json()["data"]["run_id"]
    drain()
    assert fetch(env, "tob", rid)["status"] == "queued"
    assert env.req("POST", p + "/pause", body={"reason": "复核"}).status_code == 200
    assert fetch(env, "tob", rid)["status"] == "cancelled"
    assert submit(env, "tob", sid, "排班功能").status_code == 200
    assert env.req("POST", p + "/resume", body={"reason": "继续"}).status_code == 202
    assert submit(env, "tob", sid, "不要联系我").json()["data"]["run_id"] is None
    drain()
    assert env.req("POST", p + "/stop", body={"reason": "结束"}).status_code == 200
    assert env.req("POST", p + "/resume", body={"reason": "再试"}).status_code == 409
    with transaction("tob") as c:
        assert not c.execute(s.receipts.select()).first()


def test_worker_death_after_accept_before_checkpoint(env):
    class Crash(BaseException):
        pass

    _, tid, sid = ready(env, "toc")
    rid = submit(env, "toc", sid, "查 O7 订单").json()["data"]["run_id"]
    job = claim("toc", ["support"])

    def kill():
        raise Crash()

    with pytest.raises(Crash):
        process("toc", job, sender=Sender(after_accept=kill))
    with transaction("toc") as c:
        assert c.scalar(select(s.sends.c.status)) == "dispatching"
        update(c, s.runs, "toc", rid, lease_until=now() - timedelta(seconds=1))
    drain()
    res = fetch(env, "toc", rid)
    assert res["result"]["delivery"] == "accepted", res
    with transaction("toc") as c:
        assert len(c.execute(s.receipts.select()).all()) == 1


def test_unknown_acceptance_blocks_human_and_is_not_retried(env):
    class MissingReceipt(MockChannel):
        def accept(self, c, t, v):
            raise TimeoutError()

    _, _, sid = ready(env, "toc")
    rid = submit(env, "toc", sid, "查订单 O7").json()["data"]["run_id"]
    drain(Worker(sender=Sender(MissingReceipt())))
    result = fetch(env, "toc", rid)["result"]
    assert result["delivery"] == "unknown"
    p = f"/sessions/{sid}"
    h = env.req("POST", p + "/handoff", "toc-admin", {"reason": "核对"}).json()["data"]
    b = {"handoff_id": h["handoff_id"], "expected_epoch": h["owner_epoch"]}
    e = env.req("POST", p + "/takeover", "toc-admin", b).json()["data"]["owner_epoch"]
    assert (
        env.req(
            "POST",
            p + "/human-messages",
            "toc-admin",
            {"text": "接手", "expected_epoch": e},
        ).json()["error"]["code"]
        == "SEND_UNRESOLVED"
    )
    assert (
        env.req(
            "POST",
            p + "/release",
            "toc-admin",
            b | dict(expected_epoch=e, resolution="resume", note="检查"),
        ).status_code
        == 409
    )
    drain()
    with transaction("toc") as c:
        v = c.execute(s.sends.select()).mappings().one()
        assert reconcile(c, "toc", v) == "unknown"
        assert not c.execute(s.receipts.select()).first()
