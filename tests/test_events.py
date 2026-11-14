from datetime import timedelta
import httpx
from app.api.runtime import cursor_start, event_page
from app.http import Identity
from app.db import now, transaction, update
from app import schema as s
from app.notifications import deliver_events, Subscription, verify
from tests.flows import ready, submit, drain


def test_signed_retry_and_event_cursor(env):
    _, tid, sid = ready(env)
    w = Identity("tob", "tob-admin", {"seat"}, {"pA1"})
    f = {"session_id": sid, "task_id": None, "run_id": None}
    last = cursor_start(w, f, None)
    assert submit(env, "tob", sid, "请给正式报价").status_code == 202
    drain()
    page = event_page(w, f, last)
    e = next(ev for _, ev in page if ev["type"] == "handoff.created")
    i = cursor_start(w, f, e["event_id"])
    assert all(n > i for n, _ in event_page(w, f, i))
    sub = Subscription(
        id="test",
        tenant="tob",
        products=["pA1"],
        types=["handoff.created"],
        url="https://callback.invalid/events",
        secret="test-only",
    )
    requests = []
    consumed = set()

    def receiver(req):
        stamp = req.headers["x-timestamp"]
        sig = req.headers["x-signature"]
        assert verify("test-only", stamp, req.content, sig)
        assert not verify("wrong", stamp, req.content, sig)
        requests.append(req.headers["x-event-id"])
        consumed.add(requests[-1])
        return httpx.Response(503 if len(requests) == 1 else 204)

    transport = httpx.MockTransport(receiver)
    deliver_events([sub], transport)
    assert (
        env.req("GET", f"/sessions/{sid}", "tob-admin").json()["data"]["status"]
        == "handoff_pending"
    )
    with transaction("tob") as db:
        db.execute(s.webhooks.update().values(due=now() - timedelta(seconds=1)))
    deliver_events([sub], transport)
    assert requests == [e["event_id"]] * 2 and len(consumed) == 1
    assert (
        env.req("GET", "/events/stream?session_id=" + sid, "toc-admin").status_code
        == 404
    )
    with transaction("tob") as db:
        update(db, s.events, "tob", e["event_id"], created=now() - timedelta(days=9))
    assert (
        env.client.get(
            "/api/v1/events/stream",
            headers={
                "Authorization": "Bearer " + env.tokens["tob-admin"],
                "Last-Event-ID": e["event_id"],
            },
        ).status_code
        == 410
    )
