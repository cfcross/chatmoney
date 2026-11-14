from app import schema as s, workflows
from app.db import transaction, update, row, insert
from app.execution import Execution
from app.llm import MockLLM
from tests.conftest import published


def prepare(env, t="tob", customer=None, conversation=None, cfg=None):
    a = published(env, t, cfg)
    body = dict(
        agent_id=a,
        customer_id=customer or ("c1" if t == "tob" else "u2"),
        conversation_id=conversation or ("cv1" if t == "tob" else "cv2"),
    )
    if t == "tob":
        with transaction(t) as c:
            insert(
                c,
                s.tasks,
                t,
                "sales-fixed",
                agent_id=a,
                agent_version=1,
                purpose="outreach",
                status="running",
            )
        body["task_id"] = "sales-fixed"
    r = env.req("POST", "/sessions", t + "-business", body)
    assert r.status_code == 201, r.text
    return a, r.json()["data"]["session_id"]


def plan(env, t, sid, text, num="e1", model=None, **kw):
    q = env.req(
        "POST",
        f"/sessions/{sid}/messages",
        t + "-business",
        {"text": text, "external_message_id": num},
    )
    rid = q.json()["data"]["run_id"]
    with transaction(t) as c:
        update(c, s.runs, t, rid, status="running", lease_token="test")
    return Execution(t, rid, "test", model or MockLLM(), **kw)


def test_handoff_and_checkpoint(env):
    _, sid = prepare(env)
    ex = plan(env, "tob", sid, "我们有20个账号，请给正式报价。")
    res = workflows.drive(ex, sid)
    assert res["action"] == "handoff" and res["delivery"] == "not_sent"
    again = workflows.drive(ex, sid)
    assert again == res
    with transaction("tob") as db:
        assert (
            row(db, s.memory, "tob", sid).facts["seats"]["source_ref"]
            == res["evidence_refs"][0]
        )
        assert row(db, s.conversations, "tob", "cv1").owner == "pending"
        assert not db.execute(s.sends.select()).first()
    with workflows.checkpoint("tob") as saver:
        graph = workflows.build_graph(ex, saver)
        assert graph.get_state({"configurable": {"thread_id": f"tob:{sid}"}}).next == (
            "await_human",
        )


def test_reopen_graph_and_recover_memory(env):
    _, sid = prepare(env, "toc")
    ex = plan(env, "toc", sid, "需要20个账号")
    workflows.drive(ex, sid)
    # A fresh graph, connection and model instance restore the same thread.
    ex2 = plan(env, "toc", sid, "上次要几个？", "e2")
    res = workflows.drive(ex2, sid)
    assert "20" in res["text"] and res["action"] == "draft"
    assert (
        env.req("GET", f"/sessions/{sid}/memory", "toc-business").json()["data"][
            "facts"
        ][0]["value"]
        == "20"
    )
