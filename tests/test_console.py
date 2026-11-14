from html.parser import HTMLParser
from urllib.parse import urljoin

from tests.conftest import profile, published
from tests.flows import ready, submit, drain
from app.settings import settings
from app import schema as s
from app.db import insert


def read(e, path, actor="tob-admin"):
    return e.client.get(
        "/console/data/" + path, headers={"Authorization": "Bearer " + e.tokens[actor]}
    )


def test_console_assets_change_together_and_old_urls_revalidate(env, tmp_path):
    from app.console.routes import asset_revision

    class Modules(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attr = dict(attrs)
            if tag == "script" and attr.get("type") == "module":
                self.entry = attr["src"]

    parser = Modules()
    page = env.client.get("/console")
    assert "no-store" in page.headers["Cache-Control"]
    parser.feed(page.text)
    assert parser.entry.startswith("/console/assets/v/")
    main = env.client.get(parser.entry)
    assert main.status_code == 200 and '"./knowledge.js"' in main.text
    for path in (urljoin(parser.entry, "./knowledge.js"), "/console/assets/knowledge.js"):
        response = env.client.get(path)
        assert response.status_code == 200 and "删除资料库" in response.text
        assert "删除文件" in response.text and "must-revalidate" in response.headers["Cache-Control"]
    (tmp_path / "main.js").write_text('import "./knowledge.js"', encoding="utf-8")
    module = tmp_path / "knowledge.js"
    module.write_text("old library", encoding="utf-8")
    before = asset_revision(tmp_path)
    module.write_text("new library", encoding="utf-8")
    assert asset_revision(tmp_path) != before


def test_console_contract_and_workspace(env, monkeypatch):
    assert env.client.get("/console").status_code == 200
    assert 'lang="zh-CN"' in env.client.get("/console").text
    assert env.client.get("/console/data/workspace").status_code == 401
    assert env.client.get("/docs").status_code == 200
    before = read(env, "workspace").json()["data"]
    assert (
        before["model"] == "mock"
        and before["channel"] == "mock"
        and before["worker"] is None
    )
    assert before["tenant"] == "甲企业" and not any(
        x["id"] == "pB1" for x in before["products"]
    )
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    settings.cache_clear()
    v = read(env, "workspace").json()["data"]
    assert (
        v["model"] == "deepseek"
        and not v["model_configured"]
        and v["channel"] == "mock"
    )
    assert "key" not in v


def test_drafts_product_scope_and_preview(env):
    a = published(env)
    b = env.req("POST", "/agents", body=profile() | {"name": "仅配置员可见"}).json()[
        "data"
    ]["agent_id"]
    assert (
        read(env, "directory/agents?id=" + b, "tob-business").json()["data"]["total"]
        == 0
    )
    assert read(env, "directory/agents").json()["data"]["total"] == 2
    assert read(env, "directory/agents", "tob-business").json()["data"]["total"] == 1
    assert (
        read(env, "directory/agents?id=" + a, "toc-admin").json()["data"]["total"] == 0
    )
    with env.admin.begin() as c:
        insert(
            c,
            s.docs,
            "tob",
            "private",
            kb_id="kb",
            title="草稿文件",
            original="草稿原文",
            status="ready",
            actor="seed",
        )
        insert(c, s.products, "tob", "pOther", name="受限产品", version_ref="pOther@1")
        insert(c, s.kbs, "tob", "other", product_id="pOther", name="受限资料库")
    assert read(env, "documents/private").json()["data"]["text"] == "草稿原文"
    assert read(env, "documents/private", "tob-business").status_code == 404
    assert read(env, "documents/private", "toc-admin").status_code == 404
    assert read(env, "directory/books?product=pOther").status_code == 404
    assert read(env, "directory/agents", "tob-inbound").status_code == 403
    assert read(env, "directory/agents?search=仅配置员可见").json()["data"]["items"][0][
        "at"
    ]


def test_read_model_real_sessions_counts_and_roles(env):
    _, tid, sid = ready(env)
    rq = submit(env, "tob", sid, "请给正式报价。")
    drain()
    v = read(env, "directory/handoffs").json()["data"]
    assert (
        v["total"] == 1
        and v["items"][0]["id"] == sid
        and v["items"][0]["last_message"] == "请给正式报价。"
    )
    assert read(env, "workspace").json()["data"]["counts"]["handoffs"] == 1
    assert (
        read(env, "directory/runs?id=" + rq.json()["data"]["run_id"]).json()["data"][
            "items"
        ][0]["session_id"]
        == sid
    )
    assert read(env, "directory/sessions", "toc-business").json()["data"]["total"] == 0
    assert read(env, "directory/contacts", "tob-admin").status_code == 403
    assert read(env, "directory/contacts", "tob-business").json()["data"]["total"] == 2
