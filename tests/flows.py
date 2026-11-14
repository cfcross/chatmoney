import uuid
from tests.conftest import published
from app.worker import Worker


def ready(
    e,
    t="tob",
    spec=None,
    agent_id=None,
    task_id=None,
    customer=None,
    cv=None,
    actor=None,
):
    a = agent_id or published(e, t, spec)
    who = actor or t + "-business"
    b = dict(
        agent_id=a,
        customer_id=customer or ("c1" if t == "tob" else "u2"),
        conversation_id=cv or ("cv1" if t == "tob" else "cv2"),
    )
    if t == "tob":
        if not task_id:
            tk = e.req(
                "POST",
                "/tasks",
                who,
                dict(agent_id=a, agent_version=1, purpose="outreach"),
            )
            assert tk.status_code == 201, tk.text
            task_id = tk.json()["data"]["task_id"]
            start = e.req("POST", f"/tasks/{task_id}/start", who, {})
            assert start.status_code == 202, start.text
        b["task_id"] = task_id
    r = e.req("POST", "/sessions", who, b)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    return a, data["task_id"], data["session_id"]


def submit(e, t, sid, text, external=None, key=None, actor=None):
    return e.req(
        "POST",
        f"/sessions/{sid}/messages",
        actor or t + "-business",
        dict(external_message_id=external or uuid.uuid4().hex, text=text),
        key=key,
    )


def drain(worker=None):
    worker = worker or Worker()
    for _ in range(50):
        if not worker.tick():
            return
    raise AssertionError("work queue did not drain")


def fetch(e, t, rid):
    r = e.req("GET", "/runs/" + rid, t + "-business")
    assert r.status_code == 200, r.text
    return r.json()["data"]
