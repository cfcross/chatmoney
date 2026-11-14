"""Edge acceptance with a real HTTPS API, restricted PostgreSQL worker, and an offline model transport."""

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from argparse import ArgumentParser
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from app import schema as s, web_identity as web
from app.db import insert
from app.llm import DeepSeek
from app.settings import settings
from app.worker import Worker
from scripts.console_browser_check import Browser, run as previous_suite
from scripts.local_tls import create_cert
from scripts.seed import seed
from scripts.workspace_accounts import prepare, ensure_team
from app.team import TEAM, PRODUCT
from tests.model_service import BASE, Provider


def setup_database():
    source = make_url(os.environ["MIGRATION_DATABASE_URL"])
    conn = make_url(os.environ["DATABASE_URL"])
    if not source.database.endswith("_test") or source.database != conn.database:
        raise RuntimeError("Use the dedicated test environment")
    name = "chatmoney_models_ui_test"
    for var, url in [("DATABASE_URL", conn), ("MIGRATION_DATABASE_URL", source)]:
        os.environ[var] = url.set(database=name).render_as_string(hide_password=False)
    subprocess.run([sys.executable, "-m", "scripts.test_database"], check=True)
    tokens = {
        f"{t}-{r}": uuid4().hex
        for t in ("tob", "toc")
        for r in ("admin", "business", "inbound")
    }
    passwords = {
        name: secrets.token_urlsafe(24)
        for name in tokens
        if not name.endswith("-inbound")
    }
    admin = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    with admin.begin() as db:
        from sqlalchemy import text

        db.execute(
            text(
                "TRUNCATE "
                + ", ".join(s.meta.tables)
                + ", checkpoints, checkpoint_blobs, checkpoint_writes RESTART IDENTITY CASCADE"
            )
        )
        seed(db, tokens)
        # This simulated operator both owns and runs the paid assistant in the browser check.
        db.execute(
            s.identities.update()
            .where(s.identities.c.tenant == "tob", s.identities.c.id == "tob-admin")
            .values(roles=s.identities.c.roles.concat(["business"]))
        )
        ensure_team(db)
        for name, password in passwords.items():
            insert(
                db,
                s.web_accounts,
                name.split("-")[0],
                name,
                username=name,
                password_hash=web.hash_password(password),
            )
        for t, pid in [("tob", "pA1"), ("toc", "pB1"), (TEAM, PRODUCT)]:
            insert(
                db, s.kbs, t, "book", product_id=pid, name="已批准的演示资料", latest=1
            )
            insert(
                db,
                s.docs,
                t,
                "doc",
                kb_id="book",
                title="排班说明",
                original="产品支持排班。",
                status="ready",
                actor="seed",
            )
            insert(
                db,
                s.chunks,
                t,
                "doc:1",
                document_id="doc",
                ordinal=1,
                body="产品支持排班。",
            )
            insert(
                db,
                s.snapshots,
                t,
                "book:1",
                kb_id="book",
                number=1,
                documents=[dict(document_id="doc", revision=1)],
                actor="seed",
            )
    admin.dispose()
    return tokens, passwords


def launch(stack, settings_kwargs):
    from app.main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            proxy_headers=False,
            log_level="error",
            **settings_kwargs,
        )
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()

    def stop():
        server.should_exit = True
        thread.join(8)
        sock.close()

    stack.callback(stop)
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Test API failed to start")
        time.sleep(0.05)
    return port


def check_knowledge_edits(b, folder):
    b.nav("knowledge")
    b.button("新建资料库")
    b.fill("name", "资料修改演练", "dialog")
    b.dialog_submit()
    b.button("编辑资料库")
    b.fill("name", "产品样本演练", "dialog")
    b.dialog_submit()
    b.text("产品样本演练")
    file = folder / "示例说明.md"
    file.write_text("拼豆套装内含说明书。", encoding="utf8")

    def upload(title):
        b.button("上传资料")
        b.fill("title", title, "dialog")
        root = b.cmd("DOM.getDocument", {})["result"]["root"]["nodeId"]
        node = b.cmd(
            "DOM.querySelector",
            {"nodeId": root, "selector": "dialog[open] input[type=file]"},
        )["result"]["nodeId"]
        b.cmd("DOM.setFileInputFiles", {"nodeId": node, "files": [str(file.resolve())]})
        b.dialog_submit()
        b.text("待批准")

    upload("套装说明")
    b.button("批准并发布")
    b.click("dialog input[name=documents]")
    b.dialog_submit()
    b.text("第 1 版已发布")
    b.button("编辑文件")
    b.fill("title", "最新版套装说明", "dialog")
    b.fill("text", "蓝色套装内含两份说明书。", "dialog")
    b.dialog_submit()
    b.text("最新版套装说明")
    b.button("查看原文")
    b.wait(
        "document.querySelector('dialog[open] pre')?.textContent.includes('两份说明书')"
    )
    b.button("关闭")
    b.click("main details summary")
    b.button("查阅已发布原文")
    b.wait(
        "document.querySelector('dialog[open] pre')?.textContent==='拼豆套装内含说明书。'"
    )
    b.button("关闭")
    b.size(390, 844)
    b.capture("team-knowledge-revisions-mobile")
    b.size(1440, 900)
    b.button("批准并发布")
    b.wait(
        "document.querySelector('dialog select[name=documents]')?.selectedOptions[0]?.textContent.includes('保留已批准')"
    )
    b.dialog_submit()
    b.text("当前第 2 版")
    b.button("批准并发布")
    b.js("document.querySelector('dialog select[name=documents]').selectedIndex=1")
    b.dialog_submit()
    b.text("第 3 版已发布")
    b.button("撤回")
    b.fill("reason", "已撤回文件的删除入口验收", "dialog")
    b.dialog_submit()
    b.wait("!![...document.querySelectorAll('main tbody tr')].find(r=>r.innerText.includes('已撤回') && r.querySelector('[data-act=delete-doc]'))")
    b.capture("admin-delete-withdrawn-file")
    b.button("删除文件")
    b.click("dialog[open] [data-act=cancel]")
    b.text("最新版套装说明")
    b.button("删除文件")
    b.dialog_submit()
    b.text("这里还没有文件")
    upload("删除目录时一并删除")
    b.button("返回资料库目录")
    b.text("资料库目录")
    b.size(390, 844)

    def row_action(name):
        selector = (
            "[...document.querySelectorAll('main tbody tr')].find(el=>el.innerText.includes('产品样本演练')).querySelector('[data-act="
            + name
            + "]')"
        )
        b.wait("!!(" + selector + ")")
        b.js("(" + selector + ").scrollIntoView({block:'center'})")
        assert b.js("(" + selector + ").getBoundingClientRect().right<=innerWidth")
        b.js("(" + selector + ").click()")

    row_action("edit-book")
    b.wait("document.querySelector('dialog[open] [name=name]')?.value==='产品样本演练'")
    b.click("dialog[open] [data-act=cancel]")
    b.capture("team-knowledge-library-actions-mobile")
    row_action("delete-book")
    b.wait("document.querySelector('dialog[open]')?.innerText.includes('产品样本演练')")
    b.dialog_submit()
    b.size(1440, 900)
    b.text("资料库目录")
    assert not b.js("document.querySelector('main').innerText.includes('产品样本演练')")


def team_exercise(b, setup_page, base, provider):
    def type_field(selector, value):
        target = "document.querySelector(" + json.dumps(selector) + ")"
        b.wait(target + " && !" + target + ".readOnly && !" + target + ".disabled")
        b.js(target + ".focus();" + target + ".select()")
        b.cmd("Input.insertText", {"text": value})
        assert b.js(target + ".value") == value

    def key(code, number):
        for direction in ("keyDown", "keyUp"):
            b.cmd(
                "Input.dispatchKeyEvent",
                {
                    "type": direction,
                    "key": code,
                    "code": code,
                    "windowsVirtualKeyCode": number,
                },
            )

    def form_field(name, value):
        type_field("dialog[open] [name=" + name + "]", value)

    admin_pw = secrets.token_urlsafe(20)
    member_pw = secrets.token_urlsafe(20)
    new_pw = secrets.token_urlsafe(20)
    b.cmd("Page.navigate", {"url": setup_page.resolve().as_uri()})
    b.wait("!!document.querySelector('#login [name=confirm]')")
    type_field("#login [name=username]", "团队负责人")
    key("Tab", 9)
    assert b.js("document.activeElement.name") == "password"
    type_field("#login [name=password]", admin_pw)
    type_field("#login [name=confirm]", admin_pw)
    b.click("#login [type=submit]")
    b.text("成员账号")
    assert b.js("location.hash") == "#members"

    def personal_connection(label, key):
        b.nav("agents")
        b.button("我的模型连接")
        b.text("还没有模型连接")
        b.button("添加 DeepSeek 连接")
        b.fill("connectionName", label, "dialog")
        b.fill("api_key", key, "dialog")
        b.dialog_submit()
        b.text("连接可用")
        return b.js(
            "document.querySelector('article[data-connection]').dataset.connection"
        )

    check_knowledge_edits(b, setup_page.parent)
    admin_model = personal_connection("负责人专用", provider.keys["a"])
    b.nav("members")
    b.click("main [data-act=add]")
    form_field("username", "成员一甲")
    key("Backspace", 8)
    assert b.js("document.activeElement.value") == "成员一"
    form_field("password", member_pw)
    b.dialog_submit()
    b.text("成员一")
    b.click("main [data-act=add]")
    form_field("username", "成员二")
    second_pw = secrets.token_urlsafe(20)
    form_field("password", second_pw)
    b.dialog_submit()
    b.text("成员二")
    b.js(
        "[...document.querySelectorAll('main li')].find(x=>x.querySelector('strong').textContent==='成员一').querySelector('[data-act^=issue-]').click()"
    )
    form_field("username", "产品同学")
    b.dialog_submit()
    b.text("产品同学")
    b.js(
        "[...document.querySelectorAll('main li')].find(x=>x.querySelector('strong').textContent==='产品同学').querySelector('[data-act^=issue-]').click()"
    )
    member_pw = secrets.token_urlsafe(20)
    form_field("password", member_pw)
    b.dialog_submit()
    b.text("产品同学")
    b.size(390, 844)
    b.capture("team-members-mobile")
    b.size(1440, 900)
    b.logout()
    b.cmd("Page.navigate", {"url": base + "/console"})
    b.wait("!!document.querySelector('#login [name=username]')")
    b.login("产品同学", member_pw, True, landing="我的助手")
    member_model = personal_connection("组员专用", provider.keys["b"])
    assert member_model != admin_model
    assert "负责人专用" not in b.js("document.querySelector('main').innerText")
    b.capture("team-member-private-model")
    b.nav("agents")
    b.button("创建助手")
    type_field("main [name=name]", "拼豆销售助手")
    b.click("[data-act=save]")
    b.button("拼豆销售助手")
    b.text("工作草稿")
    b.button("编辑配置")
    b.click("[data-act=next]")
    b.fill("model_provider", "deepseek")
    b.wait(
        "!!document.querySelector('select[name=model_connection_id] option[value="
        + member_model
        + "]')"
    )
    assert admin_model not in b.js(
        "document.querySelector('[name=model_connection_id]').innerHTML"
    )
    b.fill("model_connection_id", member_model)
    for name, value in (
        ("persona", "拼豆产品顾问"),
        ("duty", "介绍团队批准的资料"),
        ("tone", "亲切"),
        ("goal", "回答产品问题"),
    ):
        b.fill(name, value)
    b.click("[data-act=next]")
    b.click("input[name=knowledge]")
    b.fill("allowed", "已批准的产品资料")
    b.click("[data-act=next]")
    b.click("[data-act=publish]")
    b.dialog_submit()
    b.text("已发布 · 第 1 版")
    b.text("组员专用")
    b.button("前往任务管理")
    b.text("当前只显示这位助手的任务")
    b.button("创建任务")
    b.fill("name", "初次邀约", "dialog")
    b.fill("note", "仅模拟", "dialog")
    b.dialog_submit()
    b.text("初次邀约")
    b.button("编辑任务")
    b.fill("name", "拼豆二次邀约", "dialog")
    b.fill("note", "等对方确定时间", "dialog")
    b.dialog_submit()
    b.text("等对方确定时间")
    b.button("启动任务")
    b.dialog_submit()
    b.wait(
        "!!document.querySelector('main [data-act=pause]') && /运行中|等待中/.test(document.querySelector('main').innerText)"
    )
    b.capture("team-member-task")
    b.button("删除任务")
    b.dialog_submit()
    b.text("任务列表")
    assert not b.js("document.querySelector('main').innerText.includes('拼豆二次邀约')")
    b.nav("knowledge")
    b.text("资料库目录")
    assert not b.js("!!document.querySelector('main [data-act=new]')")
    b.button("查看资料")
    b.text("资料文件")
    assert not b.js(
        "!!document.querySelector('[data-act=delete-doc],[data-act=edit-doc],[data-act=upload]')"
    )
    b.click("#switch")
    b.click("dialog [data-act=edit-self]")
    form_field("username", "拼豆同学")
    form_field("current_password", member_pw)
    form_field("password", new_pw)
    b.dialog_submit()
    b.wait("!!document.querySelector('#login [name=username]')")
    b.login("拼豆同学", new_pw, True, landing="我的助手")
    b.nav("agents")
    b.text("拼豆销售助手")
    b.capture("team-personal-agents")
    b.login("成员二", second_pw, landing="我的助手")
    b.nav("agents")
    b.button("我的模型连接")
    b.text("还没有模型连接")
    b.nav("agents")
    b.text("我的助手")
    assert not b.js("document.querySelector('main').innerText.includes('拼豆销售助手')")
    b.button("创建助手")
    type_field("main [name=name]", "新能源产品顾问")
    b.click("[data-act=save]")
    b.text("新能源产品顾问")
    b.button("新能源产品顾问")
    b.button("删除助手")
    b.dialog_submit()
    b.text("我的助手")
    assert not b.js(
        "document.querySelector('main').innerText.includes('新能源产品顾问')"
    )
    b.logout()
    b.cmd("Page.navigate", {"url": base + "/console"})
    b.wait("!!document.querySelector('#login [name=username]')")
    b.login("团队负责人", admin_pw, True, landing="成员账号")
    b.nav("agents")
    b.button("我的模型连接")
    b.text("负责人专用")
    assert "组员专用" not in b.js("document.querySelector('main').innerText")
    b.nav("members")
    b.text("成员二")
    b.js(
        "[...document.querySelectorAll('main li')].find(x=>x.querySelector('strong').textContent==='成员二').querySelector('[data-act^=delete-member-]').click()"
    )
    b.dialog_submit()
    b.wait(
        "![...document.querySelectorAll('main li strong')].some(x=>x.textContent==='成员二')"
    )
    b.logout()
    b.cmd("Page.navigate", {"url": base + "/console"})
    b.wait("!!document.querySelector('#login [name=username]') && !location.hash")
    provider.calls.clear()  # Later editor checks start with a fresh offline call log.
    return "同团队管理员与成员各自添加个人连接，成员选用自己的连接发布助手，其他账号的管理页与向导互不可见。管理员从目录直接删除资料库，在文件行删除文件，普通成员只读。团队管理通过。管理员增删改查资料库与文件、查看已发布旧原文、删除成员账号。成员开设、修改、发布和删除自己的助手，创建、查看、修改、启动和删除任务；两个成员互相隔离。成员可阅读已发布资料。"


def exercise(b, tokens, passwords, p, base, cert):
    checks = []

    def passed(message):
        checks.append(message)
        print(message, flush=True)

    def edit(name, workflow="sales"):
        b.nav("agents")
        b.button("创建助手")
        b.fill("name", name)
        b.fill("workflow", workflow)
        b.click("[data-act=next]")
        b.fill("persona", "产品顾问")
        b.fill("duty", "记录产品问题")
        b.fill("tone", "简洁")
        b.fill("goal", "核实需求")

    def remaining(draft=False):
        b.click("[data-act=next]")
        b.click("input[name=knowledge]")
        b.fill("allowed", "已批准的排班资料")
        b.click("[data-act=next]")
        if draft:
            b.click("[data-act=save]")
            b.button("企业销售一号")
            b.text("工作草稿")
            b.click("[data-act=publish]")
        else:
            b.click("[data-act=publish]")
        b.dialog_submit()
        b.text("已发布 · 第 1 版")
        return b.js("location.hash.split('/').at(-1)")

    def manage():
        b.nav("agents")
        b.button("我的模型连接")
        b.text("DeepSeek 连接")

    def read(url, actor="tob-admin"):
        response = http.get(url, headers={"Authorization": "Bearer " + tokens[actor]})
        response.raise_for_status()
        return response.json()["data"]

    def write(url, body, actor="tob-admin"):
        res = http.post(
            url,
            headers={
                "Authorization": "Bearer " + tokens[actor],
                "Idempotency-Key": uuid4().hex,
            },
            json=body,
        )
        res.raise_for_status()
        return res.json()["data"]

    def run_sales(agent):
        task = write(
            "/api/v1/tasks", dict(agent_id=agent, agent_version=1, purpose="outreach")
        )["task_id"]
        write("/api/v1/tasks/" + task + "/start", {})
        sid = write(
            "/api/v1/sessions",
            dict(agent_id=agent, customer_id="c3", conversation_id="cv3", task_id=task),
        )["session_id"]
        rid = write(
            "/api/v1/sessions/" + sid + "/messages",
            dict(external_message_id=uuid4().hex, text="排班功能？"),
        )["run_id"]
        for _ in range(80):
            run = read("/api/v1/runs/" + rid)
            if run["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.12)
        assert run["status"] == "succeeded" and run["model_calls"]
        return run

    with httpx.Client(base_url=base, verify=str(cert), trust_env=False) as http:
        b.wait("!!document.querySelector('#login [name=username]')")
        b.capture("00-login")
        assert (
            b.js("document.querySelector('#login [name=password]').type") == "password"
        )
        assert not b.js("document.body.innerText.includes('工作区访问凭证')")
        b.login("tob-admin", passwords["tob-admin"], True)
        b.js("location.reload()")
        b.text("引擎运行正常")
        b.click("#switch")
        b.click("dialog [data-act=members]")
        b.click("dialog [data-act=add]")
        b.fill("username", "验收成员", "dialog")
        b.fill("delivery", "link", "dialog")
        b.click("dialog[open] [type=submit]")
        b.wait("!!document.querySelector('dialog[open] [name=invite]')")
        invitation = b.js("document.querySelector('dialog [name=invite]').value")
        assert invitation.startswith(base + "/console#activate?")
        b.click("dialog [data-act=done]")
        b.click("dialog[open] [data-act=close]")
        b.logout()
        b.cmd("Page.navigate", {"url": invitation})
        b.wait("!!document.querySelector('#login [name=confirm]')")
        assert b.js("location.hash") == ""
        personal_password = secrets.token_urlsafe(24)
        b.fill("password", personal_password)
        b.fill("confirm", personal_password)
        b.click("#login [type=submit]")
        b.text("引擎运行正常")
        b.click("#switch")
        assert not b.js("!!document.querySelector('dialog [data-act=members]')")
        b.click("dialog [data-act=cancel]")
        b.logout()
        b.login("验收成员", personal_password, True)
        assert b.js("document.cookie") == "" and "cm-console-identity" not in b.js(
            "Object.keys(sessionStorage)"
        )
        b.login("tob-admin", passwords["tob-admin"])
        passed(
            "账号密码登录、刷新保持、管理员在网页发邀请、成员自行设密和再次登录、权限区分、退出通过。"
        )
        # A manager can distribute credentials without visiting any agent or sales pages.
        b.cmd("Page.navigate", {"url": base + "/accounts"})
        b.click("main [data-act=add]")
        b.fill("username", "发号验收", "dialog")
        assert (
            b.js("document.querySelector('dialog [name=delivery]').value")
            == "temporary_password"
        )
        b.click("dialog[open] [type=submit]")
        b.wait("!!document.querySelector('dialog[open] [name=initial_password]')")
        initial = b.js("document.querySelector('dialog [name=initial_password]').value")
        b.click("dialog [data-act=done]")
        b.wait(
            "!document.querySelector('dialog[open]') && document.querySelector('main').innerText.includes('发号验收')"
        )
        b.js(
            "[...document.querySelectorAll('main li')].find(li=>li.querySelector('strong').textContent==='发号验收').querySelector('[data-act^=delegate-]').click()"
        )
        b.dialog_submit()
        b.text("可开通成员账号")
        b.size(390, 844)
        b.capture("account-management-mobile")
        b.size(1440, 900)
        b.click("main [data-act=logout]")
        b.wait("!!document.querySelector('#login [name=username]')")
        b.fill("username", "发号验收")
        b.fill("password", initial)
        b.click("#login [type=submit]")
        b.wait("!!document.querySelector('#login [name=confirm]')")
        assert b.js("location.hash") == "#members" and not b.js(
            "document.querySelector('main [data-act=add]')"
        )
        new_password = secrets.token_urlsafe(22)
        b.fill("password", new_password)
        b.fill("confirm", new_password)
        b.click("#login [type=submit]")
        b.text("成员账号")
        b.wait("!!document.querySelector('main [data-act=add]')")
        assert not b.js("!!document.querySelector('main [data-act^=delegate-]')")
        b.click("main [data-act=add]")
        b.fill("username", "获发账号成员", "dialog")
        b.click("dialog[open] [type=submit]")
        b.wait("!!document.querySelector('dialog[open] [name=initial_password]')")
        b.click("dialog [data-act=done]")
        b.text("获发账号成员")
        b.click("main [data-act=logout]")
        b.cmd("Page.navigate", {"url": base + "/console"})
        b.wait("!!document.querySelector('#login [name=username]')")
        b.login("tob-admin", passwords["tob-admin"], True)
        passed(
            "本机专用发号页、管理员委托发号、成员凭初始密码首次改密、受托人开通普通账号及手机布局通过。"
        )
        edit("企业销售一号")
        b.fill("model_provider", "deepseek")
        assert not p.calls  # Opening the editor must not contact the provider.
        b.click("[data-act=credential-new]")
        b.fill("connectionName", "甲企业共用连接", "dialog")
        assert b.js("document.querySelector('input[name=api_key]').type") == "password"
        b.fill("api_key", p.keys["a"], "dialog")
        b.dialog_submit()
        b.wait(
            "document.querySelector('[name=model_connection_id]')?.selectedOptions[0]?.textContent==='甲企业共用连接'"
        )
        assert b.js("document.querySelector('input[name=api_key]')?.value || ''") == ""
        assert all(x[1] in ["/models", "/user/balance"] for x in p.calls)
        b.size(390, 844)
        b.js(
            "document.querySelector('[name=model_provider]').scrollIntoView({block:'start'})"
        )
        b.capture("01-mobile-selector")
        b.size(1440, 900)
        b.capture("02-selected")
        sale = remaining(draft=True)
        b.text("甲企业共用连接")
        cid = read("/api/v1/agents/" + sale)["config"]["model_connection_id"]
        passed("销售助手在创建向导添加连接、隐藏并清空密钥、保存草稿、发布后查看绑定。")
        edit("共用客服二号", "support")
        b.fill("model_provider", "deepseek")
        b.fill("model_connection_id", cid)
        b.fill("model_name", "deepseek-v4-pro")
        other = remaining()
        assert read("/api/v1/agents/" + other)["config"]["model_connection_id"] == cid
        b.text("DeepSeek V4 Pro")
        passed(
            "同一账号的客服助手复用自己的连接，选择另一模型并发布，全程未再输入密钥。"
        )
        b.login("toc-admin", passwords["toc-admin"])
        manage()
        b.text("还没有模型连接")
        edit("隔离校验", "support")
        b.fill("model_provider", "deepseek")
        assert cid not in b.js(
            "document.querySelector('[name=model_connection_id]').innerHTML"
        )
        passed("乙企业管理员在管理页和创建向导均看不到甲企业连接。")
        b.login("tob-admin", passwords["tob-admin"])
        manage()
        b.text("甲企业共用连接")
        b.click("[data-act=rotate]")
        b.fill("api_key", p.keys["rotated"], "dialog")
        b.dialog_submit()
        b.text("连接可用")
        assert read(BASE + "/connections")[0]["id"] == cid
        p.calls.clear()
        run_sales(sale)
        assert p.calls and all(x[0] == "rotated" for x in p.calls)
        b.click("[data-act=usage]")
        b.wait(
            "document.querySelector('dialog[open]')?.innerText.includes('共用客服二号')"
        )
        b.click("dialog [data-act=close]")
        b.click("[data-act=remove]")
        b.wait("document.querySelector('dialog[open]')?.innerText.includes('无法删除')")
        b.click("dialog [data-act=close]")
        b.click("[data-act=disable-connection]")
        b.wait(
            "document.querySelector('dialog[open]')?.innerText.includes('企业销售一号')"
        )
        b.click("dialog [data-act=cancel]")
        assert read(BASE + "/connections")[0]["status"] == "ready"
        b.click("[data-act=rename]")
        b.fill("name", "甲企业主连接", "dialog")
        b.dialog_submit()
        b.text("甲企业主连接")
        b.size(390, 844)
        b.capture("03-mobile-manager")
        b.size(1280, 720)
        b.capture("04-connections")
        passed(
            "轮换后连接编号不变；原销售助手经 Worker 使用新密钥；检查引用、删除阻止、停用取消、改名和窄屏。"
        )
        b.click("[data-act=conn-add]")
        b.fill("connectionName", "错误密钥示例", "dialog")
        b.fill("api_key", p.keys["bad"], "dialog")
        b.dialog_submit()
        b.text("密钥无效")
        b.capture("05-test-error")
        b.click("[data-act=disable-connection]")
        b.dialog_submit()
        b.text("已停用")
        assert all(
            key
            not in b.js(
                "JSON.stringify([localStorage,sessionStorage,location.href,document.body.innerHTML])"
            )
            for key in p.keys.values()
        )
        passed(
            "模拟 401 显示中文原因，连接保存和测试结果分别可见。检查浏览器存储、网址、页面与确认停用。"
        )
        assert not b.exceptions
    return checks


def main():
    parser = ArgumentParser()
    parser.add_argument("--out", default=".runtime/model-browser")
    parser.add_argument(
        "--browser",
        default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tokens, passwords = setup_database()
    os.environ["LLM_BACKEND"] = "mock"
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ["CHATMONEY_CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    settings.cache_clear()
    p = Provider()
    original = DeepSeek.client
    DeepSeek.client = lambda self: httpx.Client(
        transport=httpx.MockTransport(p.handle),
        headers={"Authorization": "Bearer " + self.key},
        trust_env=False,
    )
    with ExitStack() as stack:
        from tempfile import TemporaryDirectory

        tls = Path(stack.enter_context(TemporaryDirectory()))
        password = uuid4().hex
        create_cert(tls, password)
        cert = tls / "localhost.crt"
        public = (
            x509.load_pem_x509_certificate(cert.read_bytes())
            .public_key()
            .public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        pin = base64.b64encode(hashlib.sha256(public).digest()).decode()
        stack.callback(setattr, DeepSeek, "client", original)
        httpsport = launch(
            stack,
            dict(
                ssl_certfile=str(cert),
                ssl_keyfile=str(tls / "localhost.key"),
                ssl_keyfile_password=password,
            ),
        )
        stop = threading.Event()
        errors = []

        def tick():
            worker = Worker()
            try:
                while not stop.is_set():
                    if not worker.tick():
                        stop.wait(0.07)
            except Exception as exc:
                errors.append(type(exc).__name__)

        worker = threading.Thread(target=tick, daemon=True)
        worker.start()

        def stop_worker():
            stop.set()
            worker.join(6)

        stack.callback(stop_worker)
        base = f"https://127.0.0.1:{httpsport}"
        browser = Browser(base, Path(args.browser), out, certificate_pin=pin)
        try:
            setup_page = prepare(base + "/console", tls / "enrollment", team_only=True)
            team_result = team_exercise(browser, setup_page, base, p)
            print(team_result, flush=True)
            checks = [team_result, *exercise(browser, tokens, passwords, p, base, cert)]
        finally:
            browser.close()
        regression = previous_suite(
            base,
            Path(args.browser),
            tokens,
            out / "original",
            passwords,
            cert=str(cert),
            certificate_pin=pin,
        )
        assert not errors
        checks.append(
            "回归原 Edge 全流程：模拟销售报价转人工、认领和交回、客服 O7 与 O8 隔离、四种窗口尺寸。"
        )
        (out / "result.json").write_text(
            json.dumps(
                {
                    "browser": "Microsoft Edge",
                    "provider": "offline HTTP transport",
                    "channel": "mock",
                    "checks": checks,
                    "original": regression["checks"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf8",
        )
        print(
            "Edge model connection and original console scenarios passed.", flush=True
        )


if __name__ == "__main__":
    main()
