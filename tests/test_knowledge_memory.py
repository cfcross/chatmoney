from tests.conftest import published
from app import knowledge
from app.db import transaction


def test_upload_approval_and_withdrawal(env):
    kb = env.req(
        "POST", "/knowledge-bases", body={"product_id": "pA1", "name": "A1资料"}
    ).json()["data"]["kb_id"]

    def upload(raw):
        return env.req(
            "POST",
            f"/knowledge-bases/{kb}/documents",
            files={"file": ("A1.md", raw, "text/markdown")},
            data={"title": "排班"},
        )

    assert upload(b"\x00bad").status_code == 415
    res = upload("支持跨门店排班。".encode())
    assert res.status_code == 202, res.text
    d = res.json()["data"]["document_id"]
    refs = [dict(document_id=d, revision=1)]
    path = f"/knowledge-bases/{kb}"
    assert env.req("GET", path, "tob-business").json()["data"]["documents"] == []
    b = {"expected_snapshot": None, "documents": refs}
    assert env.req("POST", path + "/publish", body=b).status_code == 409
    with transaction("tob") as c:
        knowledge.index(c, "tob", d)
    assert env.req("POST", path + "/publish", body=b).status_code == 201
    cfg = {"knowledge": [{"kb_id": kb, "snapshot": 1}]}
    with transaction("tob") as c:
        assert knowledge.retrieve(c, "tob", "pA1", cfg, "排班")[0]["document_id"] == d
    assert env.req("GET", path, "toc-admin").status_code == 404
    assert (
        env.req(
            "POST", path + "/withdraw", body=refs[0] | {"reason": "资料过期"}
        ).status_code
        == 200
    )
    with transaction("tob") as c:
        assert not knowledge.retrieve(c, "tob", "pA1", cfg, "排班")


def test_message_scope_and_memory_correction(env):
    a = published(env, "toc")

    def make(u, cv):
        return env.req(
            "POST",
            "/sessions",
            "toc-business",
            dict(agent_id=a, customer_id=u, conversation_id=cv),
        ).json()["data"]["session_id"]

    s1, s2 = make("u2", "cv2"), make("u4", "cv4")
    path = f"/sessions/{s1}"
    b = {"external_message_id": "e1", "text": "预算2万"}
    r = env.req("POST", path + "/messages", "toc-business", b)
    assert r.status_code == 202, r.text
    mid = r.json()["data"]["message_id"]
    cor = dict(
        expected_revision=1,
        key="budget",
        value="2万",
        source_ref=mid,
        reason="客户确认",
    )
    assert (
        env.req("PATCH", f"/sessions/{s2}/memory", "toc-admin", cor).status_code == 404
    )
    assert env.req("PATCH", path + "/memory", "toc-admin", cor).status_code == 200
    assert (
        env.req("GET", path + "/memory", "toc-business").json()["data"]["facts"][0][
            "source_ref"
        ]
        == mid
    )
    assert (
        env.req("GET", f"/sessions/{s2}/memory", "toc-business").json()["data"]["facts"]
        == []
    )
    assert (
        env.req("GET", path + "/messages", "toc-business").json()["data"]["items"][0][
            "text"
        ]
        == "预算2万"
    )
    assert env.req("GET", path + "/memory", "tob-business").status_code == 404
