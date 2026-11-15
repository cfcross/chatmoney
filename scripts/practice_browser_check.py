"""Exercise V1.3 in installed Edge, on a disposable HTTPS workbench with offline model HTTP."""

import argparse, base64, hashlib, json, os, secrets, socket, subprocess, sys, tempfile, time
from contextlib import ExitStack
from pathlib import Path
import httpx
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
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
    name_product,
    select_option,
)


def qa_worker():
    from app.worker import Worker
    from app.llm import DeepSeek

    url = make_url(os.environ["DATABASE_URL"])
    if (
        url.host != "127.0.0.1"
        or not url.database.startswith("chatmoney_tpl_")
        or not url.database.endswith("_test")
    ):
        raise RuntimeError("Dedicated local test database required")

    def provider(req):
        assert (
            req.url.host == "api.deepseek.com" and req.url.path == "/chat/completions"
        )
        assert req.headers["Authorization"] == "Bearer " + os.environ["TEMPLATE_QA_KEY"]
        body = json.loads(req.content)
        messages = body["messages"]
        guide = messages[0]["content"]
        if Path(os.environ["PRACTICE_QA_TIMEOUT"]).is_file():
            raise httpx.ReadTimeout("offline timeout")
        time.sleep(0.22)
        ctx = next(
            (
                json.loads(m["content"].split("：", 1)[1])
                for m in messages
                if isinstance(m.get("content"), str)
                and m["content"].startswith("以下是参考数据")
            ),
            {},
        )
        if "虚构客户" in guide:
            prior = next(
                (m["content"] for m in reversed(messages[:-2]) if m["role"] == "user"),
                "",
            )
            content = (
                "我们要了解产品的排班能力。"
                if not prior
                else (
                    "你提到排班，我还想了解服务范围。"
                    if "排班" in prior
                    else "方案有依据吗？"
                )
            )
        elif "response_format" in body:
            content = json.dumps(
                {"facts": {}, "signals": [], "need": messages[-1]["content"][:50]},
                ensure_ascii=False,
            )
        elif "复核对话" in guide:
            txt = json.loads(messages[-1]["content"])["销售"]
            content = json.dumps(
                {
                    "quote": txt[:8],
                    "observation": "说明了能力范围",
                    "suggestion": "下一步可以询问实际需求",
                },
                ensure_ascii=False,
            )
        else:
            content = (
                ctx["persona"] + "：" + ctx["chunks"][0]["body"] + "您主要想确认什么？"
            )
        return httpx.Response(
            200,
            json={
                "id": "local-practice",
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 7,
                    "total_tokens": 20,
                },
            },
        )

    DeepSeek.client = lambda adapter: httpx.Client(
        transport=httpx.MockTransport(provider),
        headers={"Authorization": "Bearer " + adapter.key},
        trust_env=False,
    )
    w = Worker(["practice"])
    while True:
        if not w.tick():
            time.sleep(0.08)


def make_agent(b, template, name, provider):
    catalog(b, "system")
    use(b, template)
    name_product(b, name)
    b.button("下一步")
    b.fill("persona", name + "的产品顾问")
    b.fill("model_provider", provider)
    if provider == "deepseek":
        select_option(
            b,
            "model_connection_id",
            "[name=model_connection_id] option:not([value=''])",
        )
    b.button("下一步")
    b.click('[name=knowledge][value="sample-book"]')
    b.button("保存草稿并对练")
    b.text("选择这次练习")
    return b.js("location.hash.split('/').pop()")


def start(b, persona, mode="step", turns=8, paid=False, judge=False):
    b.fill("persona", persona)
    b.fill("mode", mode)
    b.fill("turns", str(turns))
    if paid:
        b.click("[name=cost]")
    if judge:
        b.click("details summary")
        b.click("[name=judge]")
    b.button("开始模拟客户对练")
    b.text("对练进度")
    return b.js("location.hash.split('/').pop()")


def read(b, id):
    return api(b, "/console/manage/practices/" + id)["data"]


def go(b, hash):
    b.js("location.hash=" + quoted(hash))


def exercise(b, passwords, out, fault):
    b.login("模板成员甲", passwords["模板成员甲"], True, landing="助手管理")
    b.button("我的模型连接")
    b.button("添加 DeepSeek 连接")
    b.fill("connectionName", "对练测试连接", "dialog")
    b.fill("api_key", os.environ["TEMPLATE_QA_KEY"], "dialog")
    b.dialog_submit()
    b.text("连接可用")
    aid = make_agent(b, "sys_prospecting", "企业开发演练", "deepseek")
    b.text("可能产生 API 调用费用")
    for w, h in [(1440, 900), (1280, 720), (1024, 768), (390, 844)]:
        b.size(w, h)
        b.capture(f"setup-{w}x{h}")
    b.size(1440, 900)
    sim = start(b, "interested", paid=True, judge=True)
    b.text("等待下一轮")
    first = read(b, sim)
    assert any(
        f["kind"] == "model" and f["quote"] in first["messages"][0]["seller"]
        for f in first["evaluation"]["findings"]
    )
    assert (
        "企业开发演练的产品顾问" in first["messages"][0]["seller"]
        and "支持排班" in first["messages"][0]["seller"]
    )
    assert first["messages"][0]["evidence"]["sources"][0]["document_id"] == "sample-doc"
    b.button("下一轮")
    b.wait("document.querySelector('main').innerText.includes('2 / 8 轮')")
    b.button("暂停")
    b.text("已暂停")
    b.button("继续")
    b.wait("document.querySelector('main').innerText.includes('3 / 8 轮')")
    b.button("结束模拟并查看结果")
    b.text("评测结果")
    d = read(b, sim)
    assert (
        d["turns"] == 3 and "你提到排班" in d["messages"][1]["buyer"] and d["usage"] > 0
    )
    for w, h in [(1440, 900), (1280, 720), (1024, 768), (390, 844)]:
        b.size(w, h)
        b.capture(f"review-{w}x{h}")
    b.size(1440, 900)
    b.button("我已查看本次模拟结果")
    b.fill("note", "已核对批准资料与三轮对话。", "dialog")
    b.dialog_submit()
    b.button("提交正式启用审核")
    b.text("已提交审核 · 待平台授权")
    assert not read(b, sim)["platform_access"]
    make_agent(b, "sys_software", "商务交接演练", "mock")
    s2 = start(b, "budget", "auto")
    b.text("评测结果")
    d2 = read(b, s2)
    assert (
        d2["stop_reason"] == "handoff"
        and "正式报价" in d2["messages"][-1]["buyer"]
        and d2["messages"][-1]["seller"] is None
    ), (
        d2["stop_reason"],
        [(m["action"], m["reason"], m["buyer"], m["seller"]) for m in d2["messages"]],
    )
    b.text("模拟转人工")
    b.capture("quote-handoff")
    go(b, "agents/practice/" + aid)
    b.text("选择这次练习")
    b.button("编辑助手草稿")
    b.button("下一步")
    b.fill("tone", "先简短确认目标，再问一个问题")
    b.button("保存草稿并对练")
    b.text("选择这次练习")
    s3 = start(b, "interested", "auto", 2, True)
    b.text("评测结果")
    b.button("对照结果")
    b.text("规则或沟通问题")
    assert (
        read(b, s3)["fingerprint"] != first["fingerprint"]
        and not read(b, sim)["current"]
    )
    b.capture("compare")
    b.cmd("Page.reload", {"ignoreCache": False})
    b.text("评测结果")
    assert read(b, s3)["turns"] == 2
    make_agent(b, "sys_prospecting", "冷淡客户演练", "mock")
    s4 = start(b, "cold", "auto")
    b.text("评测结果")
    cold = read(b, s4)
    assert cold["stop_reason"] == "refusal" and cold["messages"][-1]["action"] == "stop"
    b.capture("cold-refusal")
    b.button("用同一客户重练")
    b.text("复用上次虚构客户")
    assert b.js("document.querySelector('[name=persona]').disabled")
    paired = start(b, "cold", "auto", 1)
    b.text("评测结果")
    assert read(b, paired)["repeat_from"] == s4
    # Exercise later pages through the actual API, exclusively in this disposable mock workspace.
    archive = "/console/manage/agents/" + cold["agent_id"] + "/practices"
    for _ in range(20):
        entry = api(
            b, archive, "POST", {"version": 1, "persona": "cold", "max_turns": 1}
        )
        assert entry["ok"]
        closed = api(
            b,
            "/console/manage/practices/"
            + entry["data"]["simulation_id"]
            + "/control/stop",
            "POST",
            {"expected_revision": 1},
        )
        assert closed["ok"] or closed.get("code") == "STATE_CONFLICT"
    go(b, "agents/practice/" + cold["agent_id"])
    b.button("加载更早记录")
    b.wait("document.querySelectorAll('#practice-history .practice-row').length>20")
    go(b, "agents/practice/" + cold["agent_id"] + "/" + s4)
    b.text("评测结果")
    b.button("加载更早的对照记录")
    b.button("用同一客户重练")
    b.text("复用上次虚构客户")
    go(b, "agents/practice/" + aid)
    b.text("选择这次练习")
    fault.touch()
    err = start(b, "interested", paid=True)
    b.text("模型请求超时")
    fault.unlink()
    assert read(b, err)["status"] == "failed"
    b.login("模板成员乙", passwords["模板成员乙"], landing="助手管理")
    assert (
        api(b, "/console/manage/practices/" + sim)["status"] == 404
        and api(b, "/console/manage/practice-candidates")["status"] == 403
    )
    b.login("演练审核员", passwords["演练审核员"], landing="助手管理")
    b.nav("agents")
    b.button("启用审核")
    b.text("企业开发演练")
    assert api(b, "/console/manage/practices/" + sim)["status"] == 404
    # An updated draft must not pass on an old test; reviewer may only return it.
    b.button("记录审核意见")
    b.fill("decision", "return", "dialog")
    b.fill("note", "配置已经变更，请提交新快照。", "dialog")
    b.dialog_submit()
    b.text("没有待审核的配置")
    assert not b.exceptions, b.exceptions
    return dict(
        browser="Microsoft Edge",
        flow_count=5,
        sizes=["1440x900", "1280x720", "1024x768", "390x844"],
        paid_model=False,
        checks=[
            "personal DeepSeek via offline HTTP: template role and approved chunk in actual graph",
            "manual, pause/resume, stop",
            "formal quote to simulated handoff",
            "cold refusal stops",
            "different draft snapshots compared and persisted on reload",
            "acknowledgement and scoped review; no platform permission",
            "model timeout visible",
            "member and admin privacy",
            "quality opinion verified against quoted text; paired scenario with fresh conversation",
            "no JS exceptions",
        ],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        return qa_worker()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    edge = (
        Path(os.environ["ProgramFiles(x86)"]) / "Microsoft/Edge/Application/msedge.exe"
    )
    if not edge.is_file():
        raise RuntimeError("Microsoft Edge required")
    with (
        private_database(),
        tempfile.TemporaryDirectory() as temp,
        ExitStack() as stack,
    ):
        tls = Path(temp)
        fault = tls / "offline-fault"
        os.environ.update(
            LLM_BACKEND="mock",
            CHATMONEY_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            TEMPLATE_QA_TLS=secrets.token_urlsafe(20),
            TEMPLATE_QA_KEY=secrets.token_urlsafe(32),
            PRACTICE_QA_TIMEOUT=str(fault),
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
        pw = seed()
        pw["演练审核员"] = secrets.token_urlsafe(28)
        own = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
        with own.begin() as c:
            # This exercise's catalogue describes the terms our fictional buyers ask about.
            c.execute(
                s.docs.update()
                .where(s.docs.c.tenant == TEAM, s.docs.c.id == "sample-doc")
                .values(title="产品服务与排班规格")
            )
            insert(
                c,
                s.identities,
                TEAM,
                "practice-reviewer",
                roles=["publish", "config", "business"],
                products=[PRODUCT],
                token_hash=web.digest(secrets.token_urlsafe(30)),
            )
            insert(
                c,
                s.web_accounts,
                TEAM,
                "practice-reviewer",
                username="演练审核员",
                password_hash=web.hash_password(pw["演练审核员"]),
            )
        own.dispose()
        base = stack.enter_context(server(tls))
        worker = subprocess.Popen(
            [sys.executable, "-m", "scripts.practice_browser_check", "--worker"]
        )

        def clean():
            worker.terminate()
            worker.wait(timeout=10)

        stack.callback(clean)
        b = Browser(base, edge, out, certificate_pin=pin)
        stack.callback(b.close)
        result = exercise(b, pw, out, fault)
        (out / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(
            "Edge: five workflows; four sizes; private snapshots, review, errors and isolation verified."
        )


if __name__ == "__main__":
    main()
