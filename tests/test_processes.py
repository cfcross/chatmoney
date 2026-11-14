import json
import socket
import subprocess
import sys
import time
from datetime import timedelta
import httpx
from app import schema as s
from app.db import now, transaction, update
from tests.flows import ready, submit, drain, fetch


def test_worker_process_restart_after_acceptance(env):
    _, _, sid = ready(env, "toc")
    rid = submit(env, "toc", sid, "查询订单 O7").json()["data"]["run_id"]
    result = subprocess.run(
        [sys.executable, "-m", "tests.fault_worker"], capture_output=True, timeout=30
    )
    assert result.returncode == 87, result.stderr.decode(errors="replace")[-1000:]
    with transaction("toc") as c:
        assert len(c.execute(s.receipts.select()).all()) == 1
        update(c, s.runs, "toc", rid, lease_until=now() - timedelta(seconds=1))
    subprocess.run(
        [sys.executable, "-m", "app.worker", "--once", "--queues", "support"],
        check=True,
        timeout=30,
        capture_output=True,
    )
    assert fetch(env, "toc", rid)["result"]["delivery"] == "accepted"
    with transaction("toc") as c:
        assert len(c.execute(s.receipts.select()).all()) == 1


def test_real_sse_disconnect_and_resume(env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get("/docs").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            _, _, sid = ready(env)
            addr = "/api/v1/events/stream?session_id=" + sid
            headers = {"Authorization": "Bearer " + env.tokens["tob-admin"]}
            with client.stream("GET", addr, headers=headers) as stream:
                assert stream.status_code == 200 and stream.headers[
                    "content-type"
                ].startswith("text/event-stream")
                it = stream.iter_lines()
                assert next(it) == ": connected"
                r = submit(env, "tob", sid, "请给正式报价")
                assert r.status_code == 202
                first = next(line[4:] for line in it if line.startswith("id: "))
            drain()
            got = []
            with client.stream(
                "GET", addr, headers=headers | {"Last-Event-ID": first}
            ) as stream:
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        got.append(json.loads(line[6:]))
                        if got[-1]["type"] == "run.finished":
                            break
            assert "handoff.created" in {e["type"] for e in got}
            assert all(e["event_id"] != first and e["session_id"] == sid for e in got)
    finally:
        server.terminate()
        server.wait(timeout=10)
