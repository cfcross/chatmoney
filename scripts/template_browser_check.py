"""Exercise the template workbench in real Edge on a disposable local PostgreSQL database."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app import schema as s
from app import web_identity as web
from app.db import insert
from app.team import PRODUCT, TEAM
from scripts.console_browser_check import Browser, quoted
from scripts.local_tls import create_cert
from scripts.workspace_accounts import ensure_team


@contextmanager
def private_database():
    urls = {
        key: make_url(os.environ[key])
        for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL")
    }
    if any(
        u.database != "chatmoney_test" or u.host not in ("127.0.0.1", "localhost")
        for u in urls.values()
    ):
        raise RuntimeError("Requires the local chatmoney_test environment")
    name = "chatmoney_tpl_" + uuid4().hex[:12] + "_test"
    maintenance = urls["MIGRATION_DATABASE_URL"].set(
        drivername="postgresql", database="postgres"
    )
    with psycopg.connect(
        maintenance.render_as_string(hide_password=False), autocommit=True
    ) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            for key, u in urls.items():
                os.environ[key] = u.set(database=name).render_as_string(
                    hide_password=False
                )
            subprocess.run([sys.executable, "-m", "scripts.bootstrap"], check=True)
            yield
        finally:
            for key, u in urls.items():
                os.environ[key] = u.render_as_string(hide_password=False)
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def seed():
    passwords = {
        user: secrets.token_urlsafe(24) for user in ["模板成员甲", "模板成员乙"]
    }
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    with engine.begin() as c:
        ensure_team(c)
        for username, password in passwords.items():
            insert(
                c,
                s.identities,
                TEAM,
                username,
                roles=["business", "own_agents"],
                products=[PRODUCT],
                token_hash=web.digest(secrets.token_urlsafe(32)),
            )
            insert(
                c,
                s.web_accounts,
                TEAM,
                username,
                username=username,
                password_hash=web.hash_password(password),
            )
        insert(
            c,
            s.kbs,
            TEAM,
            "sample-book",
            product_id=PRODUCT,
            name="已批准的产品资料",
            latest=1,
        )
        insert(
            c,
            s.docs,
            TEAM,
            "sample-doc",
            kb_id="sample-book",
            title="样例规格",
            original="支持排班。",
            status="ready",
            actor="seed",
        )
        insert(
            c,
            s.chunks,
            TEAM,
            "sample-chunk",
            document_id="sample-doc",
            ordinal=1,
            body="支持排班。",
        )
        insert(
            c,
            s.snapshots,
            TEAM,
            "sample-book:1",
            kb_id="sample-book",
            number=1,
            documents=[{"document_id": "sample-doc", "revision": 1}],
            actor="seed",
        )
    engine.dispose()
    return passwords


def serve(tls):
    import uvicorn

    from app.llm import DeepSeek
    from tests.model_service import Provider

    u = make_url(os.environ["DATABASE_URL"])
    if (
        u.host not in ("127.0.0.1", "localhost")
        or not u.database.startswith("chatmoney_tpl_")
        or not u.database.endswith("_test")
    ):
        raise RuntimeError("Test service requires an isolated local database")
    fake = Provider()
    fake.keys["a"] = os.environ["TEMPLATE_QA_KEY"]
    DeepSeek.client = lambda self: httpx.Client(
        transport=httpx.MockTransport(fake.handle),
        headers={"Authorization": "Bearer " + self.key},
        trust_env=False,
    )
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.environ["TEMPLATE_QA_PORT"]),
        ssl_certfile=str(tls / "localhost.crt"),
        ssl_keyfile=str(tls / "localhost.key"),
        ssl_keyfile_password=os.environ["TEMPLATE_QA_TLS"],
        access_log=False,
        log_level="error",
    )


@contextmanager
def server(tls):
    port = int(os.environ["TEMPLATE_QA_PORT"])
    base = f"https://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.template_browser_check", "--serve", str(tls)]
    )
    try:
        deadline = time.monotonic() + 20
        with httpx.Client(verify=str(tls / "localhost.crt"), trust_env=False) as h:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("Test API exited")
                try:
                    if h.get(base + "/console", timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Test API startup timed out")
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


def api(browser, path, method="GET", body=None):
    expr = """(async()=>{const mod=await import(new URL('app.js',document.querySelector('script[type=module]').src));
    try {return {ok:true,data:await mod.request(PATH,OPTIONS)}} catch(e){return {ok:false,status:e.status,code:e.code}}})()"""
    opts = {"method": method}
    if body is not None:
        opts["body"] = body
    return browser.js(
        expr.replace("PATH", quoted(path)).replace("OPTIONS", quoted(opts))
    )


def catalog(b, which):
    b.nav("agents")
    b.button("我的模板" if which == "personal" else "从模板创建")
    b.wait(
        "location.hash.endsWith("
        + quoted(which)
        + ") && !document.querySelector('main .loading')"
    )


def use(b, id):
    b.click('article[data-template="' + id + '"] a[href^="#agents/new/"]')
    b.text("已套用")


def name_product(b, name):
    b.fill("name", name)
    b.fill("product", PRODUCT)


def select_option(b, field, selector):
    val = b.wait("document.querySelector(" + quoted(selector) + ")?.value")
    b.fill(field, val)


def read_draft(b, name):
    rec = api(b, "/console/data/directory/agents?search=" + name)["data"]["items"]
    id = next(x["id"] for x in rec if x["name"] == name)
    return id, api(b, "/agents/" + id + "?view=draft")["data"]


def first_half(b, passwords, out):
    b.login("模板成员甲", passwords["模板成员甲"], True, landing="助手管理")
    b.button("我的模型连接")
    b.button("添加 DeepSeek 连接")
    b.fill("connectionName", "验收专用模型", "dialog")
    b.fill("api_key", os.environ["TEMPLATE_QA_KEY"], "dialog")
    b.dialog_submit()
    b.text("连接可用")
    catalog(b, "system")
    b.wait("document.querySelectorAll('article[data-template]').length===6")
    for w, h in [(1440, 900), (1280, 720), (1024, 768), (390, 844)]:
        b.size(w, h)
        b.capture(f"system-{w}x{h}")
    b.size(1440, 900)
    use(b, "sys_prospecting")
    name_product(b, "模板验收销售")
    b.button("下一步")
    b.wait(
        "!!document.querySelector('[name=persona]')?.value && document.querySelector('[name=goal]').value.includes('筛选')"
    )
    b.fill("tone", "亲切且专业")
    b.size(390, 844)
    b.capture("wizard-role-390")
    b.button("更换模板")
    b.fill("template", "sys_industry", "dialog")
    b.capture("switch-dialog-390")
    b.click("dialog[open] [data-act=cancel]")
    assert b.js("document.querySelector('[name=tone]').value") == "亲切且专业"
    b.size(1440, 900)
    b.fill("model_provider", "deepseek")
    select_option(
        b, "model_connection_id", "[name=model_connection_id] option:not([value=''])"
    )
    b.button("下一步")
    b.click('[name=knowledge][value="sample-book"]')
    b.button("保存草稿")
    b.text("我的助手")
    id, draft = read_draft(b, "模板验收销售")
    assert (
        draft["config"]["tone"] == "亲切且专业"
        and draft["config"]["model_connection_id"]
    )
    assert draft["config"]["knowledge"] == [{"kb_id": "sample-book", "snapshot": 1}]
    b.button("模板验收销售")
    b.button("检查并发布")
    b.dialog_submit()
    b.text("已发布 · 第 1 版")
    assert api(b, "/agents/" + id)["data"]["source_template"]["id"] == "sys_prospecting"
    b.capture("published-template-agent")
    catalog(b, "system")
    use(b, "sys_industry")
    name_product(b, "待补充助手")
    b.button("保存草稿")
    b.text("我的助手")
    incomplete, _ = read_draft(b, "待补充助手")
    b.button("待补充助手")
    b.button("检查并发布")
    b.wait(
        "document.querySelector('dialog[open]')?.innerText.includes('请选择个人模型连接')"
    )
    b.capture("publish-check")
    b.click("dialog[open] [data-act=cancel]")
    refusal = api(
        b, "/agents/" + incomplete + "/publish", "POST", {"expected_revision": 1}
    )
    assert refusal["code"] == "TEMPLATE_INCOMPLETE"
    catalog(b, "personal")
    b.button("新建模板")
    b.size(390, 844)
    b.capture("personal-editor-390")
    b.size(1440, 900)
    for k, v in {
        "name": "我的行业配置",
        "description": "用于行业沟通草稿",
        "scenario": "工业设备",
        "persona": "技术销售助理",
        "goal": "确认实际工况",
        "tone": "平实",
        "allowed": "仅依据批准的规格",
    }.items():
        b.fill(k, v)
    b.button("保存模板")
    b.text("配置详情")
    custom = b.js("location.hash.split('/').pop()")
    assert (
        api(b, "/console/manage/templates/" + custom)["data"]["config"]["persona"]
        == "技术销售助理"
    )
    b.button("编辑模板")
    b.fill("tone", "亲切简明")
    b.button("保存模板")
    b.text("配置详情")
    b.button("复制为我的模板")
    b.dialog_submit()
    b.text("我的行业配置（副本）")
    b.logout()
    return id, custom


def second_half(b, passwords, agent, custom):
    b.login("模板成员甲", passwords["模板成员甲"], True, landing="助手管理")
    catalog(b, "personal")
    b.text("我的行业配置（副本）")
    use(b, custom)
    name_product(b, "独立新助手")
    b.button("下一步")
    assert b.js("document.querySelector('[name=tone]').value") == "亲切简明"
    b.fill("model_provider", "mock")
    b.button("下一步")
    b.click('[name=knowledge][value="sample-book"]')
    b.button("下一步")
    b.button("发布助手")
    b.dialog_submit()
    b.text("已发布 · 第 1 版")
    independent, _ = read_draft(b, "独立新助手")
    b.nav("agents")
    b.button("模板验收销售")
    b.button("保存为我的模板")
    b.text("专有信息")
    b.fill("name", "从已发布助手提取")
    b.fill("description", "只保留通用沟通")
    b.fill("scenario", "产品销售")
    b.fill("persona", "【产品】的研究助理")
    b.button("保存模板")
    b.text("配置详情")
    exported = b.js("location.hash.split('/').pop()")
    preview = api(b, "/console/manage/templates/" + exported)["data"]["config"]
    assert (
        preview["persona"] == "【产品】的研究助理"
        and "model_connection_id" not in preview
        and "knowledge" not in preview
    )
    b.button("使用此模板")
    name_product(b, "独立草稿")
    b.button("保存草稿")
    b.text("我的助手")
    _, fresh = read_draft(b, "独立草稿")
    assert (
        fresh["config"]["persona"] == preview["persona"]
        and not fresh["config"].get("model_connection_id")
        and fresh["config"]["knowledge"] == []
    )
    b.login("模板成员乙", passwords["模板成员乙"], landing="助手管理")
    catalog(b, "personal")
    b.text("还没有个人模板")
    endpoint = "/console/manage/templates/" + custom
    assert api(b, endpoint)["status"] == 404
    # Use a valid body so a refusal exercises ownership, not field validation.
    body = api(b, "/console/manage/templates/sys_industry")["data"]
    for k in ["id", "type", "version"]:
        body.pop(k)
    assert (
        api(b, endpoint, "PATCH", {"expected_version": 2, "content": body})["status"]
        == 404
    )
    b.login("模板成员甲", passwords["模板成员甲"], landing="助手管理")
    catalog(b, "personal")
    b.click('article[data-template="' + custom + '"] a[href*="/view/"]')
    b.button("删除模板")
    b.click("dialog[open] [data-act=cancel]")
    assert api(b, endpoint)["ok"]
    b.button("删除模板")
    b.dialog_submit()
    b.text("我的模板")
    assert api(b, endpoint)["status"] == 404
    p = api(b, "/agents/" + independent)["data"]
    assert (
        p["version"] == 1
        and p["config"]["tone"] == "亲切简明"
        and p["source_template"]["id"] == custom
    )
    b.cmd("Page.reload", {"ignoreCache": False})
    b.text("我的模板")
    assert not b.js(
        "document.querySelector('article[data-template='+ "
        + quoted(custom)
        + " +']')!==null"
    )
    b.size(390, 844)
    b.capture("personal-after-delete-390")
    b.nav("agents")
    b.button("创建助手")
    name_product(b, "保留本次名称")
    b.button("选择模板")
    b.fill("template", "sys_supplier", "dialog")
    b.dialog_submit()
    b.text("已套用：制造商与供应商开发助手")
    assert b.js("document.querySelector('[name=name]').value") == "保留本次名称"
    b.button("下一步")
    assert b.js("document.querySelector('[name=persona]').value.includes('供应链')")
    assert b.js("document.querySelector('[name=model_provider]').value") == ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--serve", type=Path)
    parser.add_argument(
        "--edge",
        type=Path,
        default=Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Microsoft/Edge/Application/msedge.exe",
    )
    a = parser.parse_args()
    if a.serve:
        return serve(a.serve)
    if not a.out or not a.edge.is_file():
        raise SystemExit("Supply --out and an installed Edge browser")
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    with private_database(), tempfile.TemporaryDirectory() as temp:
        tls = Path(temp)
        os.environ.update(
            LLM_BACKEND="mock",
            CHATMONEY_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            TEMPLATE_QA_TLS=secrets.token_urlsafe(20),
            TEMPLATE_QA_KEY=secrets.token_urlsafe(30),
        )
        os.environ.pop("DEEPSEEK_API_KEY", None)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["TEMPLATE_QA_PORT"] = str(sock.getsockname()[1])
        create_cert(tls, os.environ["TEMPLATE_QA_TLS"])
        pub = x509.load_pem_x509_certificate(
            (tls / "localhost.crt").read_bytes()
        ).public_key()
        pin = base64.b64encode(
            hashlib.sha256(
                pub.public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).digest()
        ).decode()
        passwords = seed()
        for stage in [1, 2]:
            with server(tls) as base, ExitStack() as stack:
                b = Browser(base, a.edge, a.out, certificate_pin=pin)
                stack.callback(b.close)
                if stage == 1:
                    aid, tid = first_half(b, passwords, a.out)
                else:
                    second_half(b, passwords, aid, tid)
                assert not b.exceptions, b.exceptions
                print("Edge stage", stage, "passed", flush=True)
        (a.out / "result.json").write_text(
            json.dumps(
                {
                    "browser": "Microsoft Edge",
                    "flows": 5,
                    "api_process_restarted": True,
                    "screens": ["1440x900", "1280x720", "1024x768", "390x844"],
                    "model_transport": "offline",
                    "checks": [
                        "system-template to published draft",
                        "missing bindings rejected",
                        "private CRUD across restart",
                        "selected assistant version exported and independently reused",
                        "cross-member access denied and delete without cascade",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf8",
        )


if __name__ == "__main__":
    main()
