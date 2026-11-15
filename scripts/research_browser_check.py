"""Installed Edge: real API, isolated PostgreSQL and local HTTP origin. Never connect a production test database."""

import argparse, asyncio, base64, hashlib, json, os, secrets, socket, subprocess, sys, tempfile, time, threading
from pathlib import Path
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from app import schema as s, web_identity as web
from app.db import insert
from app.team import TEAM, PRODUCT
from scripts.console_browser_check import Browser, quoted
from scripts.template_browser_check import (
    private_database,
    seed,
    server,
    create_cert,
    api,
    catalog,
    use,
)
from scripts.practice_browser_check import go
from tests.test_company_research import HTML, SITE


def worker():
    u = make_url(os.environ["DATABASE_URL"])
    assert (
        u.host in ("127.0.0.1", "localhost")
        and u.database.startswith("chatmoney_tpl_")
        and u.database.endswith("_test")
    )
    from app.research import network
    from app.worker import Worker

    original = network.crawl
    port = int(os.environ["RESEARCH_QA_HTTP"])

    async def dns(host):
        assert host == "company.example"
        return ["93.184.216.34"]

    async def transport(url, ip):
        assert urlsplit(url).hostname == "company.example" and ip == "93.184.216.34"
        async with httpx.AsyncClient(trust_env=False) as h:
            r = await h.get(f"http://127.0.0.1:{port}" + urlsplit(url).path)
        return network.Reply(r.status_code, dict(r.headers), r.content)

    async def local(*a, **kw):
        return await original(*a, **kw, dns=dns, send=transport)

    network.crawl = local
    loop = Worker(["practice", "research"])
    while True:
        if not loop.tick():
            time.sleep(0.14)


def exercise(
    b,
    member,
    mempw,
    admin,
    adminpw,
    product,
    kb,
    target,
    scope,
    proof,
    out,
    *,
    online=False,
):
    # Use only synthetic identities. No real paid model in either run.
    B = "/console/manage/"
    landing = "助手管理" if not online else "引擎运行正常"

    def login(user, pw, first=False):
        b.login(user, pw, first, landing=landing)
        b.nav("agents")
        b.text("助手管理")

    def nav(path, needle):
        go(b, path)
        b.text(needle)

    def dlg():
        b.dialog_submit()
        b.wait("!document.querySelector('dialog[open]')")

    login(member, mempw, True)
    catalog(b, "system")
    use(b, "sys_software")
    b.fill("name", "企业研究验收助手")
    b.fill("product", product)
    b.button("下一步")
    b.fill("model_provider", "mock")
    b.button("下一步")
    b.click('[name=knowledge][value="' + kb + '"]')
    b.click("[name=research_wanted]")
    b.button("保存草稿并对练")
    b.text("选择这次练习")
    a = b.js("location.hash.split('/').pop()")
    b.fill("persona", "budget")
    b.fill("mode", "auto")
    b.button("开始模拟客户对练")
    b.text("对练进度")
    b.wait("document.querySelector('main').innerText.includes('评测结果')", timeout=45)
    b.button("我已查看本次模拟结果")
    b.fill("note", "已核对模拟记录。", "dialog")
    dlg()
    b.button("提交正式启用审核")
    b.text("待平台授权")
    nav("agents/view/" + a, "检查并发布")
    b.button("检查并发布")
    dlg()
    b.text("已发布")
    b.button("企业研究")
    b.text("尚未获得企业官网资料研究权限")
    assert api(b, B + "agents/" + a + "/research")["data"]["grants"] == []
    login(admin, adminpw)
    b.button("启用审核")
    b.button("记录审核意见")
    b.fill("note", "批准此配置作为只读候选。", "dialog")
    dlg()
    b.nav("agents")
    b.button("研究来源与授权")
    b.button("登记许可来源")
    for k, v in {
        "name": "验收专用公开样本",
        "base_url": scope,
        "permission_reference": proof,
        "valid_days": "1",
        "retention_days": "1",
    }.items():
        b.fill(k, v, "dialog")
    for k in ("allow_automation", "allow_evidence_storage"):
        b.click('dialog [name="' + k + '"]')
    dlg()
    b.text("验收专用公开样本")
    assert api(b, B + "agents/" + a + "/research").get("status") == 404
    login(member, mempw)
    nav("agents/research/" + a, "申请启用企业研究工具")
    b.button("申请启用企业研究工具")
    b.text("待审批")
    dash = api(b, B + "agents/" + a + "/research")["data"]
    grant = dash["grants"][0]["id"]
    body = {
        "grant_id": grant,
        "company_url": target,
        "research_goal": "核实官网公开的业务资料",
        "criteria": [{"field": "main_products", "include": ["储能"], "weight": 1}],
    }
    assert api(b, B + "agents/" + a + "/research/jobs", "POST", body)["status"] == 403
    assert api(b, B + "research-grants")["status"] == 403
    login(admin, adminpw)
    b.button("研究来源与授权")
    b.button("批准只读")
    b.fill("reason", "仅核验公开资料，不联系客户。", "dialog")
    dlg()
    b.text("只读已授权")
    login(member, mempw)
    nav("agents/research/" + a, "已获准使用")
    if online:
        b.size(1280,720)
        b.capture("public-approved-controls")
    b.fill("target", "https://localhost/")
    b.fill("goal", "核对业务与产品的匹配依据")
    b.fill("dimension", "main_products")
    b.fill("include", "Laptop" if online else "储能")
    b.button("开始企业研究")
    b.wait("document.body.innerText.includes('仅支持已批准网站')")
    assert api(b, B + "agents/" + a + "/research")["data"]["jobs"] == []
    b.fill("target", target)
    b.button("开始企业研究")
    b.text("执行进度")
    b.wait(
        "!!document.querySelector('[name=draft]') || document.querySelector('main').innerText.includes('研究失败')",
        timeout=65,
    )
    j = b.js("location.hash.split('/').pop()")
    path = B + "research-jobs/" + j
    item = api(b, path)["data"]
    assert item["status"] == "ready", (
        item["status"],
        item.get("error"),
        item.get("events"),
    )
    r = item["report"]
    assert (
        r["sample"]
        and r["evidence"]
        and r["requests"]
        and r["contacted"] is False
        and r["intent_score"] is r["conversion_probability"] is None
    )
    assert all(
        urlsplit(x["url"]).hostname == urlsplit(target).hostname for x in r["requests"]
    )
    assert all(x["id"] and x["url"] and x["quote"] for x in r["evidence"])
    if not online:
        assert (
            r["company_name"][0]["value"] == "晨星虚构样本" and r["fit"]["score"] == 100
        )
    b.text("证据和商务联系入口")
    for w, h in ((1440, 900), (1280, 720), (1024, 768), (390, 844)):
        b.size(w, h)
        assert b.js("""(()=>{ const n=document.querySelector('#identity strong');
          if (!n || !n.getClientRects().length) return true;
          const r=document.createRange(); r.selectNodeContents(n);
          return [...r.getClientRects()].every(x=>x.right<=document.querySelector('.sidebar').getBoundingClientRect().right+1); })()"""), "sidebar account label overflows"
        b.capture(("public" if online else "local") + "-" + str(w))
    b.size(1280, 720)
    b.fill("draft", "验收专用草稿，仅供人工核对。")
    b.button("审核通过并保存草稿")
    b.text("保存为测试线索")
    b.button("保存为测试线索")
    b.text("测试线索已保存")
    b.cmd("Page.reload", {"ignoreCache": False})
    b.text("测试线索已保存")
    assert api(b, path)["data"]["report"]["draft"] == "验收专用草稿，仅供人工核对。"
    login(admin, adminpw)
    assert api(b, path)["status"] == 404
    assert not b.exceptions, b.exceptions
    return {
        "source": target,
        "mode": "outside HTTPS" if online else "fixture HTTP via test-only adapter",
        "page_requests": r["requests"],
        "evidence": r["evidence"],
        "fit": r["fit"],
        "draft_before_edit": r["draft"],
        "product_quote": r["product_evidence"][0]["quote"],
        "company": r["company_name"],
        "unknown": r["unknown_fields"],
        "model": r["model"],
        "contact": r["public_business_contact"],
        "platform_writes": 0,
        "version": item["version"],
        "tool_version": item["tool_version"],
        "reviewed_sample_lead": True,
        "owner_isolation": True,
        "widths": [1440, 1280, 1024, 390],
        "asset": b.js(
            "document.querySelector('script[type=module]').src.split('/').slice(-2)[0]"
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--worker", action="store_true")
    args = ap.parse_args()
    if args.worker:
        return worker()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    hits = []

    class HTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            raw = (
                b"User-agent: *\nAllow: /business/\n"
                if self.path == "/robots.txt"
                else HTML
            )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain" if self.path == "/robots.txt" else "text/html",
            )
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    with (
        private_database(),
        tempfile.TemporaryDirectory() as temp,
        ExitStack() as stack,
    ):
        http = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        stack.callback(http.server_close)
        stack.callback(http.shutdown)
        os.environ.update(
            RESEARCH_QA_HTTP=str(http.server_port),
            LLM_BACKEND="mock",
            CHATMONEY_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            TEMPLATE_QA_TLS=secrets.token_urlsafe(20),
            TEMPLATE_QA_KEY=secrets.token_urlsafe(24),
        )
        os.environ.pop("DEEPSEEK_API_KEY", None)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["TEMPLATE_QA_PORT"] = str(sock.getsockname()[1])
        tls = Path(temp)
        create_cert(tls, os.environ["TEMPLATE_QA_TLS"])
        key = x509.load_pem_x509_certificate(
            (tls / "localhost.crt").read_bytes()
        ).public_key()
        pin = base64.b64encode(
            hashlib.sha256(
                key.public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).digest()
        ).decode()
        pw = seed()
        admin = "研究验收管理员"
        apw = secrets.token_urlsafe(24)
        db = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
        with db.begin() as c:
            insert(
                c,
                s.identities,
                TEAM,
                "research-reviewer",
                roles=["config", "publish", "business"],
                products=[PRODUCT],
                token_hash=web.digest(secrets.token_urlsafe(32)),
            )
            insert(
                c,
                s.web_accounts,
                TEAM,
                "research-reviewer",
                username=admin,
                password_hash=web.hash_password(apw),
            )
            c.execute(
                s.docs.update()
                .where(s.docs.c.tenant == TEAM, s.docs.c.id == "sample-doc")
                .values(title="产品服务与采购排班")
            )
        db.dispose()
        base = stack.enter_context(server(tls))
        proc = subprocess.Popen(
            [sys.executable, "-m", "scripts.research_browser_check", "--worker"]
        )

        def end():
            proc.terminate()
            proc.wait(timeout=12)

        stack.callback(end)
        b = Browser(
            base,
            Path(os.environ["ProgramFiles(x86)"])
            / "Microsoft/Edge/Application/msedge.exe",
            out,
            certificate_pin=pin,
        )
        stack.callback(b.close)
        r = exercise(
            b,
            "模板成员甲",
            pw["模板成员甲"],
            admin,
            apw,
            PRODUCT,
            "sample-book",
            SITE,
            SITE,
            "受控测试页作者许可读取并保留测试摘录一天。",
            out,
        )
        assert hits == ["/robots.txt", "/business/", "/business/products"], hits
        r["actual_fixture_http_requests"] = hits
        (out / "result.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(
            "Edge: approved read, refused pending/foreign, blocked local URL, source evidence, editable draft, sample lead and reload; four viewports."
        )


if __name__ == "__main__":
    main()
