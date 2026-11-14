"""Browser acceptance on a dedicated mock workspace. Uses local Edge/Chromium over CDP."""

from argparse import ArgumentParser
from base64 import b64decode
from pathlib import Path
import json
import subprocess
import time
from uuid import uuid4
import httpx
from websockets.sync.client import connect


def quoted(v):
    return json.dumps(v, ensure_ascii=False)


class Browser:
    def __init__(self, base, binary, out, *, certificate_pin=None):
        self.base, self.out = base, out
        out.mkdir(exist_ok=True, parents=True)
        profile = (out / ("profile-" + uuid4().hex)).resolve()
        self.proc = subprocess.Popen(
            [
                str(binary),
                "--headless",
                *(["--ignore-certificate-errors-spki-list=" + certificate_pin] if certificate_pin else []),
                "--disable-gpu",
                "--disable-background-networking",
                "--no-default-browser-check",
                "--no-first-run",
                "--lang=zh-CN",
                "--remote-debugging-port=0",
                "--user-data-dir=" + str(profile),
                base + "/console",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        portfile = profile / "DevToolsActivePort"
        end = time.monotonic() + 12
        while not portfile.exists():
            if time.monotonic() > end:
                raise RuntimeError("Browser launch timeout")
            time.sleep(0.05)
        port = int(portfile.read_text().splitlines()[0])
        targets = httpx.get(
            f"http://127.0.0.1:{port}/json/list", trust_env=False
        ).json()
        url = next(
            t["webSocketDebuggerUrl"]
            for t in targets
            if t["type"] == "page" and "/console" in t["url"]
        )
        self.ws = connect(url, max_size=12_000_000, legacy=True)
        self.seq = 0
        self.exceptions = []
        self.cmd("Runtime.enable", {})
        self.size(1440, 900)

    def cmd(self, method, params):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        while True:
            r = json.loads(self.ws.recv(timeout=15))
            if r.get("method") == "Runtime.exceptionThrown":
                ex = r["params"]["exceptionDetails"]
                self.exceptions.append(
                    ex.get("exception", {}).get("description", ex.get("text", ""))
                )
            if r.get("id") == self.seq:
                return r

    def js(self, expression):
        r = self.cmd(
            "Runtime.evaluate",
            dict(expression=expression, returnByValue=True, awaitPromise=True),
        )
        if r.get("error") or r.get("result", {}).get("exceptionDetails"):
            raise AssertionError(
                "Browser expression failed: "
                + str(
                    r.get("result", {})
                    .get("exceptionDetails", {})
                    .get("text", r.get("error", ""))
                )
            )
        return r["result"]["result"].get("value")

    def wait(self, expr, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = self.js(expr)
            if v:
                return v
            time.sleep(0.09)
        text = self.js("document.body.innerText")
        raise AssertionError(
            f"Timeout: {expr[:120]}\n{text[-1300:]}\n{self.exceptions[-2:]}"
        )

    def text(self, t):
        return self.wait(
            "document.querySelector('main').innerText.includes(" + quoted(t) + ")"
        )

    def click(self, selector):
        q = "document.querySelector(" + quoted(selector) + ")"
        self.wait(q + " && !" + q + ".disabled")
        self.js(q + ".click()")

    def button(self, label):
        query = (
            "[...document.querySelectorAll('dialog[open] button, main button, main a')].find(x=>x.innerText.trim()==="
            + quoted(label)
            + "&&x.getClientRects().length&&!x.disabled)"
        )
        self.wait("!!(" + query + ")")
        self.js("(" + query + ").click()")

    def fill(self, name, value, root="main"):
        sel = root + ' [name="' + name + '"]'
        self.wait("!!document.querySelector(" + quoted(sel) + ")")
        self.js(
            "(()=>{const x=document.querySelector("
            + quoted(sel)
            + ");x.value="
            + quoted(value)
            + "; x.dispatchEvent(new Event('input',{bubbles:true}));x.dispatchEvent(new Event('change',{bubbles:true}))})()"
        )

    def nav(self, key):
        self.click('nav [href="#' + key + '"]')

    def dialog_submit(self):
        self.click("dialog[open] [type=submit]")
        self.wait("!document.querySelector('dialog').open")

    def logout(self):
        self.click("#switch")
        self.click('dialog [data-act="logout"]')
        self.wait("!!document.querySelector('#login [name=username]')")

    def login(self, username, password, first=False, *, landing="引擎运行正常"):
        if not first:
            self.logout()
        self.fill("username", username)
        self.fill("password", password)
        self.click("#login [type=submit]")
        self.text(landing)

    def size(self, w, h):
        self.cmd(
            "Emulation.setDeviceMetricsOverride",
            {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False},
        )

    def capture(self, label):
        self.wait("!document.querySelector('main .loading')")
        overflow = self.js("document.documentElement.scrollWidth>window.innerWidth+1")
        assert not overflow, label + " overflows viewport"
        collisions = self.js("""(()=>{for(const g of document.querySelectorAll('.tools,.actions,.form-actions,.steps,nav')){
        const r=[...g.children].filter(x=>x.getClientRects().length).map(x=>x.getBoundingClientRect());
        for(let i=0;i<r.length;i++)for(let j=0;j<i;j++)if(Math.min(r[i].right,r[j].right)-Math.max(r[i].left,r[j].left)>2 && Math.min(r[i].bottom,r[j].bottom)-Math.max(r[i].top,r[j].top)>2)return true}return false})()""")
        assert not collisions, label + " overlapping controls"
        snap = self.cmd(
            "Page.captureScreenshot", {"format": "png", "fromSurface": True}
        )
        (self.out / (label + ".png")).write_bytes(b64decode(snap["result"]["data"]))

    def close(self):
        try:
            self.cmd("Browser.close", {})
        except Exception:
            pass
        self.ws.close()
        self.proc.terminate()
        self.proc.wait(timeout=10)


def run(base, binary, tokens, out, passwords, *, cert=True, certificate_pin=None):
    http = httpx.Client(base_url=base, timeout=6, trust_env=False, verify=cert)

    def headers(who):
        return {"Authorization": "Bearer " + tokens[who]}

    def read(path, who="tob-admin"):
        r = http.get(path, headers=headers(who))
        r.raise_for_status()
        return r.json()["data"]

    assert read("/console/data/workspace")["model"] == "mock"
    prefix = "界面验收" + uuid4().hex[:6]
    book = prefix + "产品说明"
    agent = prefix + "顾问"
    task = prefix + "跟进"
    b = Browser(base, binary, out, certificate_pin=certificate_pin)
    progress = []

    def ok(label):
        progress.append(label)
        print(label, flush=True)

    def control(action, reason=None):
        b.click('[data-act="' + action + '"]')
        if reason:
            b.fill("reason", reason, "dialog")
        b.dialog_submit()

    try:
        b.login("tob-admin", passwords["tob-admin"], True)
        b.capture("01-overview")
        ok("总览与身份验证通过")
        b.nav("knowledge")
        b.click('[data-act="new"]')
        b.fill("name", book, "dialog")
        b.dialog_submit()
        b.text(book)
        b.click('[data-act="upload"]')
        b.fill("title", "产品服务范围", "dialog")
        file = out / "示例产品说明.md"
        file.write_text(
            "支持跨门店排班。\n\n合同及正式报价由吴姐处理。", encoding="utf8"
        )
        root = b.cmd("DOM.getDocument", {})["result"]["root"]["nodeId"]
        node = b.cmd(
            "DOM.querySelector", {"nodeId": root, "selector": "dialog input[type=file]"}
        )["result"]["nodeId"]
        b.cmd("DOM.setFileInputFiles", {"nodeId": node, "files": [str(file.resolve())]})
        b.dialog_submit()
        b.text("待批准")
        b.click('[data-act="preview"]')
        b.wait(
            "document.querySelector('dialog[open] pre')?.textContent.includes('跨门店排班')"
        )
        b.click("dialog [data-act=close]")
        b.click("[data-act=approve]")
        b.click("dialog input[name=documents]")
        b.dialog_submit()
        b.text("第 1 版已发布")
        b.capture("02-knowledge")
        ok("建立资料库、上传、预览、审核发布通过")
        b.nav("agents")
        b.button("创建助手")
        b.fill("name", agent)
        b.click("[data-act=next]")
        b.fill("persona", "产品商务顾问")
        b.fill("duty", "了解团队使用场景")
        b.fill("tone", "专业简洁")
        b.fill("goal", "记录客户需求")
        b.click("[data-act=next]")
        selector = (
            "[...document.querySelectorAll('label.check')].find(x=>x.innerText.includes("
            + quoted(book)
            + "))?.querySelector('input')"
        )
        b.wait("!!(" + selector + ")")
        b.js("(" + selector + ").click()")
        b.click('input[name=tools][value="channel.reply"]')
        b.fill("allowed", "已批准的产品功能")
        b.fill("forbidden", "承诺交付日期")
        b.click("[data-act=next]")
        b.click('input[name=handoff][value="formal_quote"]')
        b.capture("03-wizard")
        b.click("[data-act=save]")
        b.wait("!!document.querySelector('main table')")
        b.button(agent)
        b.click('a[href*="agents/edit/"]')
        b.fill("name", agent + "修订")
        b.click("[data-act=next]")
        b.click("[data-act=next]")
        b.click("[data-act=next]")
        b.click("[data-act=publish]")
        b.dialog_submit()
        b.text("已发布 · 第 1 版")
        ok("四步创建、保存草稿、编辑并发布助手通过")
        aid = b.js("location.hash.split('/').at(-1)")
        b.nav("sessions")
        b.click('a[href="#sessions/tasks"]')
        b.click("[data-act=new-task]")
        b.fill("agent", aid, "dialog")
        b.fill("name", task, "dialog")
        b.dialog_submit()
        b.text("待启动")
        control("start")
        b.wait("!!document.querySelector('[data-act=pause]')")
        tid = b.js("location.hash.split('/').at(-1)")
        control("pause", "核对资料")
        b.text("已暂停")
        control("resume", "继续")
        b.wait("!!document.querySelector('[data-act=pause]')")
        ok("任务创建、启动、暂停、恢复通过")
        b.login("tob-business", passwords["tob-business"])
        b.nav("sessions")
        b.click("[data-act=new-session]")
        b.wait("!!document.querySelector('dialog[open] select[name=choice]')")
        sel = (
            "Array.from(document.querySelector('dialog select').options).find(o=>o.textContent.includes("
            + quoted(task)
            + ") && o.textContent.includes('c1'))"
        )
        b.wait("!!(" + sel + ")")
        b.fill("choice", b.js("(" + sel + ").value"), "dialog")
        b.dialog_submit()
        b.text("完整对话")
        sid = b.js("location.hash.split('/').at(-1)")
        b.click("[data-act=schedule]")
        b.fill("kind", "follow_up", "dialog")
        b.fill("due", "2031-11-13T15:00", "dialog")
        b.dialog_submit()
        b.click("[data-act=inbound]")
        b.fill("text", "我们有20个账号，请给正式报价。", "dialog")
        b.dialog_submit()
        b.text("待人工认领")
        b.text("客户要求正式报价")
        ok("业务身份建立会话、定时跟进、展示原话和转人工通过")
        b.login("tob-admin", passwords["tob-admin"])
        b.nav("handoffs")
        b.click('main a[href="#handoffs/view/' + sid + '"]')
        b.text("客户要求正式报价")
        b.capture("04-handoff")
        control("claim")
        b.text("我（当前坐席）")
        b.click("[data-act=human]")
        b.fill("text", "我已接手，将核实报价。", "dialog")
        b.dialog_submit()
        b.text("平台已受理")
        b.click("[data-act=correct]")
        b.fill("key", "账号数量", "dialog")
        b.fill("value", "20", "dialog")
        b.fill("reason", "已与客户核实", "dialog")
        b.dialog_submit()
        assert read("/api/v1/sessions/" + sid + "/memory")["facts"][0]["corrected"]
        control("release", "已登记后续跟进")
        b.text("等待中")
        assert read("/api/v1/sessions/" + sid)["owner"] == "auto"
        ok("认领、纠正记忆、人工回复、查询真实回执、交回通过")
        b.click("[data-act=handoff]")
        b.fill("reason", "再次核实后结束", "dialog")
        b.dialog_submit()
        b.text("待人工认领")
        control("claim")
        b.text("人工处理中")
        control("close-session", "本轮沟通完成")
        b.text("已结束")
        ok("手动交接、处理完成并结束会话通过")
        b.js("location.hash=" + quoted("sessions/task/" + tid))
        b.text("任务进度")
        b.click("[data-act=stop]")
        b.click("dialog [data-act=cancel]")
        assert read("/api/v1/tasks/" + tid)["status"] != "cancelled"
        control("stop", "本次测试结束")
        b.text("已取消")
        b.js("location.hash=" + quoted("agents/view/" + aid))
        b.click("[data-act=disable]")
        b.fill("reason", "本次验收完成", "dialog")
        b.dialog_submit()
        b.text("已停用")
        b.nav("knowledge")
        b.button(book)
        b.click("[data-act=withdraw]")
        b.fill("reason", "替换旧修订", "dialog")
        b.dialog_submit()
        b.text("已撤回")
        ok("停止确认、取消确认、停用助手、撤回文件通过")
        b.login("toc-business", passwords["toc-business"])
        b.nav("agents")
        b.wait("!!document.querySelector('main [data-search]')")
        assert agent not in b.js("document.querySelector('main').innerText")
        b.js("location.hash='agents/new'")
        b.text("需要配置管理权限")
        ok("跨企业隐藏与配置权限不足中文提示通过")

        # Seed a published support role through v1 public endpoints; the browser handles its customer.
        def post(w, path, body=None, **kw):
            resp = http.post(
                "/api/v1" + path,
                headers=headers(w) | {"Idempotency-Key": uuid4().hex},
                json=body,
                **kw,
            )
            resp.raise_for_status()
            return resp.json()["data"]

        def done(w, rid):
            for _ in range(80):
                r = read("/api/v1/runs/" + rid, w)
                if r["status"] == "succeeded":
                    return r
                if r["status"] == "failed":
                    raise AssertionError(r["error"])
                time.sleep(0.12)
            raise AssertionError("worker timeout")

        who = "toc-admin"
        ctx = read("/console/data/workspace", who)
        prod = ctx["products"][0]
        kb = post(
            who,
            "/knowledge-bases",
            {"product_id": prod["id"], "name": prefix + "客服资料"},
        )["kb_id"]
        up = post(
            who,
            "/knowledge-bases/" + kb + "/documents",
            files={"file": ("service.md", "支持本人查询订单。".encode())},
            data={"title": "售后说明"},
        )
        done(who, up["run_id"])
        post(
            who,
            "/knowledge-bases/" + kb + "/publish",
            {
                "expected_snapshot": None,
                "documents": [{"document_id": up["document_id"], "revision": 1}],
            },
        )
        support = prefix + "客服"
        aid2 = post(
            who,
            "/agents",
            {
                "name": support,
                "workflow": "support",
                "product_id": prod["id"],
                "config": {
                    "persona": "客服",
                    "tone": "简短",
                    "goal": "解答问题",
                    "allowed": ["查询本人订单"],
                    "forbidden": ["承诺退款"],
                    "company_ref": ctx["company_ref"],
                    "product_ref": prod["version_ref"],
                    "policy_ref": ctx["rules"]["support"]["ref"],
                    "knowledge": [{"kb_id": kb, "snapshot": 1}],
                    "tool_allowlist": ["order.lookup", "channel.reply"],
                    "handoff_on": ["ask_human"],
                },
            },
        )["agent_id"]
        post(who, "/agents/" + aid2 + "/publish", {"expected_revision": 1})
        b.nav("sessions")
        b.click("[data-act=new-session]")
        b.wait("!!document.querySelector('dialog[open] select[name=choice]')")
        option = (
            "Array.from(document.querySelector('dialog select').options).find(o=>o.textContent.includes("
            + quoted(support)
            + ") && o.textContent.includes('u2'))"
        )
        b.fill("choice", b.js("(" + option + ").value"), "dialog")
        b.dialog_submit()
        b.text("完整对话")
        csid = b.js("location.hash.split('/').at(-1)")
        b.click("[data-act=inbound]")
        b.fill("text", "我的订单 O7 发货了吗？", "dialog")
        b.dialog_submit()
        b.text("已发货")
        b.text("平台已受理")
        b.click("main details summary")
        b.text("查询客户本人的订单")
        b.click("[data-act=inbound]")
        b.fill("text", "请查订单 O8", "dialog")
        b.dialog_submit()
        b.text("无法核实订单属于当前客户")
        b.capture("08-support-result")
        post(
            "toc-business",
            "/tasks/"
            + read("/api/v1/sessions/" + csid, "toc-business")["task_id"]
            + "/stop",
            {"reason": "浏览器验收结束"},
        )
        ok("客服身份查看本人订单模拟回执、工具明细与他人订单拒绝原因通过")
        b.login("tob-admin", passwords["tob-admin"])
        b.size(1280, 720)
        b.nav("overview")
        b.text("引擎运行正常")
        b.capture("05-desktop-1280")
        b.size(1024, 768)
        b.nav("agents")
        b.text("工作区助手")
        b.capture("06-desktop-1024")
        b.size(390, 844)
        b.nav("overview")
        b.text("引擎运行正常")
        b.capture("07-mobile")
        # Mobile and desktop transitions use the same real tenant-scoped endpoints.
        assert not b.exceptions, b.exceptions
        ok("1440、1280、1024 桌面与 390 窄屏无页面溢出或控件重叠")
        return {
            "checks": progress,
            "browser": "Edge",
            "sales_agent": aid,
            "session": sid,
            "task": tid,
        }
    finally:
        b.close()
        http.close()


if __name__ == "__main__":
    p = ArgumentParser()
    p.add_argument("--base", default="https://localhost:8443")
    p.add_argument("--passwords", required=True, help="Private JSON file of dedicated test account passwords")
    p.add_argument("--cert", help="PEM certificate for the HTTPS test service")
    p.add_argument("--tokens", default=".runtime/console-ui-tokens.json")
    p.add_argument("--out", default=".runtime/console-browser")
    p.add_argument(
        "--browser",
        default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    args = p.parse_args()
    result = run(
        args.base,
        Path(args.browser),
        json.loads(Path(args.tokens).read_text()),
        Path(args.out),
        json.loads(Path(args.passwords).read_text()), cert=args.cert or True,
    )
    (Path(args.out) / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
