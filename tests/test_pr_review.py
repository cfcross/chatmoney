"""Review regressions use two isolated test tenants, real PostgreSQL locks and offline providers."""
import asyncio
import subprocess
import sys
import time

import anyio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select, text
from starlette.requests import Request
from starlette import formparsers

from app import knowledge_upload as upload, schema as s
from app.db import engine, now, Problem, transaction, update
from app.practice import runner as practice
from app.research import runner, assessment
from app.tools.gateway import Gateway
from app.worker import Worker
from tests.test_company_research import BASE, SOURCE, capture, setup, view
from tests.test_agent_templates import call
from tests.test_personal_models import data
from tests.test_large_knowledge import upload as upload_file, preview


def new_job(env, path, body, key):
    result = call(env, "POST", path + "/jobs", body, key=key)
    assert result.status_code == 202, result.text
    return data(result)["id"]


def read_row(env, job):
    with env.admin.begin() as db:
        return db.execute(select(s.research_jobs).where(s.research_jobs.c.id == job)).mappings().one()


def test_same_site_waits_then_completes_original_jobs_once(env, monkeypatch):
    _, _, _, path, body = setup(env)
    first = new_job(env, path, body, "first")
    second = new_job(env, path, body, "second")
    calls = capture(monkeypatch)
    entered, release = Event(), Event()
    read, compose = Gateway.research, assessment.compose
    readers, models = [], []

    def controlled_read(self, execution):
        readers.append(execution.id)
        if execution.id == first:
            entered.set()
            assert release.wait(12)
        return read(self, execution)

    def counted_compose(job, *args):
        models.append(job.id)
        return compose(job, *args)

    monkeypatch.setattr(Gateway, "research", controlled_read)
    monkeypatch.setattr(assessment, "compose", counted_compose)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(runner.tick, "tob")
        try:
            assert entered.wait(12)
            before = now()
            assert runner.tick("tob")
            waiting = read_row(env, second)
            assert waiting.status == "queued" and waiting.error is None and waiting.lease is None
            assert before + timedelta(seconds=3) < waiting.lease_until < now() + timedelta(seconds=9)
            assert [e["kind"] for e in waiting.events].count("site_wait") == 1
            assert not any(e["kind"] in {"failed", "claimed", "http", "model_started"} for e in waiting.events)
            assert readers == [first] and not calls and not models
            assert not runner.tick("tob")
        finally:
            release.set()
        assert running.result(timeout=12)
    assert view(env, first)["status"] == "ready"
    with transaction("tob") as db:
        update(db, s.research_jobs, "tob", second, lease_until=now()-timedelta(seconds=1))
    assert runner.tick("tob") and view(env, second)["status"] == "ready"
    assert readers == models == [first, second]
    assert len(calls) == 4 and not runner.tick("tob")


def test_disconnect_releases_lock_and_pre_io_claim_is_recoverable(env, monkeypatch):
    _, _, _, path, body = setup(env)
    job = new_job(env, path, body, "recover")
    capture(monkeypatch)
    # A disconnected worker cannot leave a server-side transaction lock in the pool.
    with engine().connect() as held:
        held.execute(text("select pg_advisory_xact_lock(hashtextextended('company-research-host:company.example',0))"))
        assert runner.tick("tob") and read_row(env, job).status == "queued"
        held.invalidate()
    with transaction("tob") as db:
        update(db, s.research_jobs, "tob", job, lease="previous-worker", lease_until=now()-timedelta(seconds=1))
    assert runner.tick("tob") and view(env, job)["status"] == "ready"
    assert not runner.tick("tob")


def test_terminated_worker_releases_site_lock_without_replaying_uncertain_read(env, monkeypatch, tmp_path):
    _, _, _, path, body = setup(env)
    first = new_job(env, path, body, "interrupted-first")
    second = new_job(env, path, body, "recover-next")
    calls = capture(monkeypatch)
    marker = tmp_path / "inside-guard"
    script = """
import sys, time
from pathlib import Path
from app.research import runner
from app.tools.gateway import Gateway
def held(self, execution):
    Path(sys.argv[1]).touch()
    time.sleep(60)
Gateway.research = held
runner.tick("tob")
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(marker)])
    try:
        deadline = time.monotonic() + 12
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.04)
        assert marker.exists() and read_row(env, first).status == "fetching"
        assert runner.tick("tob") and read_row(env, second).status == "queued"
        assert not calls
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
    with transaction("tob") as db:
        for job in (first, second):
            update(db, s.research_jobs, "tob", job, lease_until=now() - timedelta(seconds=1))
    assert runner.tick("tob")
    assert (read_row(env, first).status, read_row(env, first).error) == ("failed", "INTERRUPTED")
    assert not calls
    assert runner.tick("tob") and read_row(env, second).status == "ready"
    assert len(calls) == 2 and not runner.tick("tob")


def test_worker_rotates_busy_practice_research_queues_and_tenants(env, monkeypatch):
    handled = []
    def busy(lane):
        def tick(tenant, model):
            handled.append((lane, tenant))
            return True
        return tick
    monkeypatch.setattr(practice, "tick", busy("practice"))
    monkeypatch.setattr(runner, "tick", busy("research"))
    worker = Worker(["practice", "research"])
    assert all(worker.tick() for _ in range(8))
    assert len(set(handled[:4])) == 4 and handled[:4] == handled[4:]


def set_roles(env, *roles):
    with env.admin.begin() as c:
        update(c, s.identities, "tob", "tob-admin", roles=list(roles))


def test_member_sees_only_approved_source_and_no_licence_text(env):
    aid, src, grant, path, _ = setup(env)
    listing = BASE + "research-sources"
    extra = data(call(env, "POST", listing, SOURCE | {"name":"未分配", "base_url":"https://another.example/"}))["id"]
    foreign = data(call(env, "POST", listing, SOURCE, actor="toc-admin"))["id"]
    assert src != foreign
    set_roles(env, "own_agents")
    for visible in (data(call(env, "GET", listing))["items"], data(call(env, "GET", path))["sources"]):
        assert [r["id"] for r in visible] == [src]
        assert all("policy" not in row and "permission_reference" not in row for row in visible)
    assert data(call(env, "GET", listing, actor="toc-admin"))["items"][0]["id"] == foreign
    assert call(env, "GET", listing, actor="tob-business").status_code == 403
    with env.admin.begin() as c:
        update(c, s.research_grants, "tob", grant, status="revoked")
    assert data(call(env, "GET", listing))["items"] == []
    history = data(call(env, "GET", path))["grants"][0]
    assert history["scope"] is None and extra not in str(history)


@pytest.mark.parametrize("role", ["publish", "config"])
def test_policy_view_is_tenant_scoped_and_read_does_not_grant_write(env, role):
    listing = BASE + "research-sources"
    src = data(call(env, "POST", listing, SOURCE))["id"]
    data(call(env, "POST", listing, SOURCE, actor="toc-admin"))
    set_roles(env, role)
    visible = data(call(env, "GET", listing))["items"]
    assert [x["id"] for x in visible] == [src]
    assert visible[0]["policy"]["permission_reference"] == SOURCE["permission_reference"]
    if role == "config":
        assert call(env, "POST", listing + "/" + src + "/revoke", {}).status_code == 403


@pytest.mark.parametrize("change", [{"disabled": True}, {"published": 2}])
def test_unusable_agent_hides_previous_source(env, change):
    aid, _, _, path, _ = setup(env)
    with env.admin.begin() as c:
        update(c, s.agents, "tob", aid, **change)
    set_roles(env, "own_agents")
    assert data(call(env, "GET", BASE + "research-sources"))["items"] == []


def test_two_tenants_upload_independently_and_cannot_read_other_files(env):
    kb = env.req("POST", "/knowledge-bases", "toc-admin", body={"name":"隔离测试", "product_id":"pB1"}).json()["data"]["kb_id"]
    with transaction("tob") as conn:
        assert not conn.scalar(text("SELECT has_sequence_privilege(current_user, 'tenants_upload_lock_key_seq', 'USAGE')"))
    with upload.upload_slot("tob"):
        with pytest.raises(Problem) as rejected:
            with upload.upload_slot("tob"):
                pytest.fail("same-tenant capacity exceeded")
        assert rejected.value.code == "UPLOAD_BUSY"
        result = upload_file(env, kb, b"controlled fixture", actor="toc-admin")
        assert result.status_code == 202 and preview(env, result.json()["data"]["document_id"], "tob-admin").status_code == 404
    with upload.upload_slot("tob"):
        pass


def test_custom_tenant_capacity_and_release_after_error(env, monkeypatch):
    monkeypatch.setattr(upload.settings(), "upload_concurrency", 2)
    with upload.upload_slot("tob"):
        with pytest.raises(ValueError):
            with upload.upload_slot("tob"), upload.upload_slot("toc"):
                with pytest.raises(Problem, match="UPLOAD_BUSY"):
                    with upload.upload_slot("tob"):
                        pytest.fail("tenant capacity exceeded")
                raise ValueError("controlled upload error")
        with upload.upload_slot("tob"), upload.upload_slot("toc"):
            pass


def test_idle_upload_without_next_chunk_times_out(env, monkeypatch):
    monkeypatch.setattr(upload.settings(), "upload_idle_timeout_seconds", 0.06)
    class PausedRequest:
        headers = Request({"type":"http","headers":[(b"content-type",b"multipart/form-data; boundary=cm")]}).headers
        async def stream(self):
            yield b'--cm\r\n'
            await anyio.sleep_forever()
    with pytest.raises(Problem) as timeout:
        with upload.upload_slot("tob"):
            asyncio.run(upload.receive_form(PausedRequest()))
    assert timeout.value.code == "UPLOAD_INTERRUPTED"
    with upload.upload_slot("tob"):
        pass


@pytest.mark.parametrize("expire", ["total", "idle", "empty"])
def test_upload_deadlines_close_file_and_release_own_slot(env, monkeypatch, expire):
    clock = [0.]
    monkeypatch.setattr(upload, "monotonic", lambda: clock[0])
    cfg = upload.settings()
    monkeypatch.setattr(cfg, "upload_total_timeout_seconds", 10)
    monkeypatch.setattr(cfg, "upload_idle_timeout_seconds", 5)
    opened = []
    real = formparsers.SpooledTemporaryFile
    def spool(*a, **kw):
        f = real(*a, **kw)
        opened.append(f)
        return f
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    chunks = [(0, b'--cm\r\nContent-Disposition: form-data; name="file"; filename="sample.md"\r\n\r\nx')]
    chunks += {"total": [(4, b"y"), (8, b"z"), (10, b"a")], "idle": [(6, b"y")], "empty": [(3, b""), (5, b"")]}[expire]
    class TimedRequest:
        headers = Request({"type":"http","headers":[(b"content-type",b"multipart/form-data; boundary=cm")]}).headers
        async def stream(self):
            for when, chunk in chunks:
                clock[0] = when
                yield chunk
    with pytest.raises(Problem) as timeout:
        with upload.upload_slot("tob"):
            asyncio.run(upload.receive_form(TimedRequest()))
    assert timeout.value.code == "UPLOAD_INTERRUPTED" and opened and all(f.closed for f in opened)
    with upload.upload_slot("tob"):
        pass
