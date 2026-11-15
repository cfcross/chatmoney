"""Opt-in Windows/Edge size acceptance. Uses and removes a NEW localhost test database only."""
import argparse
import base64
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def rss():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in "peak current pp p np n page peakpage".split()]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = (ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong)
    assert fn(ctypes.c_void_p(-1), ctypes.byref(c), c.cb)
    return {"working": c.current, "peak": c.peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--edge", type=Path, default=Path(os.environ.get("ProgramFiles(x86)", "")) /
                        "Microsoft/Edge/Application/msedge.exe")
    opts = parser.parse_args()
    if os.name != "nt" or not opts.edge.is_file():
        raise SystemExit("Requires Windows and Microsoft Edge")
    opts.out.mkdir(exist_ok=True, parents=True)
    old = {k: make_url(os.environ[k]) for k in ("MIGRATION_DATABASE_URL","DATABASE_URL")}
    if {u.database for u in old.values()} != {"chatmoney_test"} or any(
        u.host not in ("127.0.0.1","localhost") for u in old.values()):
        raise SystemExit("Refusing non-local or non-test input URLs")
    if shutil.disk_usage(opts.out).free < 6_000_000_000:
        raise SystemExit("Insufficient scratch space")
    name = "chatmoney_upl_" + uuid4().hex[:12] + "_test"
    maintenance = old["MIGRATION_DATABASE_URL"].set(database="postgres",drivername="postgresql").render_as_string(hide_password=False)
    def drop():
        with psycopg.connect(maintenance, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    results = {"unit":"decimal bytes","db":name,"uploads":[]}
    def record():
        (opts.out / "boundary.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    with ExitStack() as stack:
        with psycopg.connect(maintenance,autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        stack.callback(drop)
        for k,v in old.items():
            os.environ[k] = v.set(database=name).render_as_string(hide_password=False)
        os.environ["LLM_BACKEND"] = "mock"
        os.environ.pop("DEEPSEEK_API_KEY",None)
        subprocess.run([sys.executable,"-m","scripts.bootstrap"],check=True)
        from app import schema as s, knowledge_upload as up, web_identity as web
        from app.db import insert, transaction, engine
        from app.worker import Worker
        from scripts.seed import seed
        from scripts.model_browser_check import launch, create_cert, x509, serialization, Browser
        stack.callback(engine().dispose)
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=opts.out)))
        spool = scratch / "spool"
        spool.mkdir()
        tempfile.tempdir = str(spool)
        stack.callback(setattr,tempfile,"tempdir",None)
        tokens = {f"{a}-{b}":secrets.token_urlsafe(24) for a in ("tob","toc") for b in ("admin","inbound","business")}
        pw = secrets.token_urlsafe(24)
        admin = create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True)
        with admin.begin() as db:
            seed(db,tokens)
            insert(db,s.web_accounts,"tob","tob-admin",username="tob-admin",password_hash=web.hash_password(pw))
        admin.dispose()
        certpw = uuid4().hex
        create_cert(scratch,certpw)
        cert = scratch / "localhost.crt"
        pin = base64.b64encode(hashlib.sha256(x509.load_pem_x509_certificate(cert.read_bytes()).public_key().public_bytes(
            serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
        base = "https://127.0.0.1:" + str(launch(stack,dict(ssl_certfile=str(cert),ssl_keyfile=str(scratch/"localhost.key"),ssl_keyfile_password=certpw)))
        http = stack.enter_context(httpx.Client(base_url=base,trust_env=False,timeout=300,verify=ssl.create_default_context(cafile=str(cert))))
        def headers():
            return {"Authorization":"Bearer "+tokens["tob-admin"],"Idempotency-Key":uuid4().hex}
        def data(r):
            if r.is_error:
                raise AssertionError((r.status_code,r.text[:500]))
            return r.json()["data"]
        kb = data(http.post("/api/v1/knowledge-bases",headers=headers(),json={"name":"Boundary","product_id":"pA1"}))["kb_id"]
        prefix = b'--cm-limit\r\nContent-Disposition: form-data; name="title"\r\n\r\nLimit\r\n--cm-limit\r\nContent-Disposition: form-data; name="file"; filename="bulk.txt"\r\nContent-Type: text/plain\r\n\r\n'
        suffix = b"\r\n--cm-limit--\r\n"
        unit = (b"Alpha product approved for test only.\n"*8192)[:262144]
        worker = Worker()
        results["start_rss"] = rss()
        for n in (10_000_000,100_000_000,999_999_999,1_000_000_000,1_000_000_001):
            started = time.monotonic()
            digest = hashlib.sha256()
            probes = []
            def feed():
                yield prefix
                left = n
                while left:
                    part = unit[:min(left,len(unit))]
                    yield part
                    digest.update(part)
                    left -= len(part)
                    if not probes and left < n//2:
                        t = time.monotonic()
                        page = http.get("/api/v1/knowledge-bases/"+kb,headers=headers())
                        assert page.status_code == 200
                        probes.append(round(time.monotonic()-t,3))
                yield suffix
            h = dict(headers(), **{"Content-Type":"multipart/form-data; boundary=cm-limit"})
            if n != 1_000_000_001:
                h["Content-Length"] = str(n+len(prefix)+len(suffix))
            result = http.post("/api/v1/knowledge-bases/"+kb+"/documents",headers=h,content=feed())
            item = {"bytes":n,"http":result.status_code,"upload_seconds":round(time.monotonic()-started,2),
                    "concurrent_book_read_seconds":probes}
            if n <= up.FILE_LIMIT:
                assert result.status_code == 202, result.text[:300]
                doc = data(result)["document_id"]
                assert worker.tick()
                assert data(http.get("/api/v1/knowledge-bases/"+kb,headers=headers()))["documents"][0]["status"]=="ready"
                if n == up.FILE_LIMIT:
                    match = hashlib.sha256()
                    total = 0
                    with transaction("tob") as db:
                        for part in up.blocks(db,"tob",doc):
                            match.update(part)
                            total += len(part)
                    assert total == n and match.digest() == digest.digest()
                    item["sha256_round_trip"] = True
                r = http.delete("/console/manage/knowledge-bases/"+kb+"/documents/"+doc,headers=headers())
                assert r.status_code == 200, r.text[:300]
            else:
                assert result.status_code==413 and result.json()["error"]["code"]=="FILE_TOO_LARGE",result.text[:300]
            item["rss_bytes"] = rss()
            item["remaining_spool_files"] = len(list(spool.iterdir()))
            assert not item["remaining_spool_files"]
            results["uploads"].append(item)
            print(item,flush=True)
            record()
        # Actual UI upload uses a real file selected in Edge, then worker, review and publication.
        browser = Browser(base,opts.edge,scratch/"edge",certificate_pin=pin)
        try:
            browser.login("tob-admin",pw,True)
            browser.nav("knowledge")
            browser.click("[data-act=new]")
            browser.fill("name","容量验收","dialog")
            browser.dialog_submit()
            browser.click("[data-act=upload]")
            browser.wait("document.querySelector('dialog[open]').innerText.includes('1 GB')")
            browser.fill("title","完整资料","dialog")
            sample = scratch/"sample.md"
            sample.write_bytes(("企业资料翻页检查。"*90000).encode())
            node = browser.cmd("DOM.getDocument",{})["result"]["root"]["nodeId"]
            f = browser.cmd("DOM.querySelector",{"nodeId":node,"selector":"dialog input[type=file]"})["result"]["nodeId"]
            browser.cmd("DOM.setFileInputFiles",{"nodeId":f,"files":[str(sample.resolve())]})
            browser.dialog_submit()
            assert worker.tick()
            browser.click("[data-act=refresh]")
            browser.text("待批准")
            browser.click("[data-act=preview]")
            browser.wait("document.querySelector('dialog[open] pre')?.textContent.includes('翻页检查')")
            browser.click("dialog [data-act=next-text]")
            browser.wait("!!document.querySelector('dialog[open] [data-act=prev-text]')")
            browser.click("dialog [data-act=close]")
            browser.click("[data-act=approve]")
            browser.click("dialog input[name=documents]")
            browser.dialog_submit()
            browser.text("第 1 版已发布")
            browser.capture("upload-approved")
            shutil.copyfile(browser.out/"upload-approved.png",opts.out/"edge-upload-approved.png")
            results["browser"]="Edge: HTTPS login, multipart upload, name/limit, worker, source pagination, approve/publish"
            assert not browser.exceptions,browser.exceptions
        finally:
            browser.close()
    results["isolated_database_dropped"]=True
    record()


if __name__ == "__main__":
    main()
