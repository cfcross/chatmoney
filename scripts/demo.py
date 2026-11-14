"""Exercise both complete business paths through HTTP only. Generates no real external messages."""

import argparse
import json
from pathlib import Path
import time
import uuid
import httpx


def demo(base, tokens):
    http = httpx.Client(
        base_url=base.rstrip("/") + "/api/v1/", timeout=10, trust_env=False
    )

    def call(t, method, path, body=None, **kw):
        headers = {"Authorization": "Bearer " + tokens[t]}
        if method in {"POST", "PATCH"}:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = http.request(method, path.lstrip("/"), json=body, headers=headers, **kw)
        if not resp.is_success:
            raise RuntimeError(f"{method} {path}: {resp.status_code} {resp.text}")
        return resp.json()["data"]

    def wait(t, id):
        for _ in range(160):
            val = call(t, "GET", "/runs/" + id)
            if val["status"] in {"failed", "cancelled"}:
                raise RuntimeError(str(val))
            if val["status"] == "succeeded":
                return val
            time.sleep(0.2)
        raise TimeoutError(f"Worker did not finish {id}")

    def setup(t, p, workflow, customer, cv):
        admin = t + "-admin"
        biz = t + "-business"
        k = call(
            admin, "POST", "/knowledge-bases", {"name": "A1 产品资料", "product_id": p}
        )["kb_id"]
        d = call(
            admin,
            "POST",
            f"/knowledge-bases/{k}/documents",
            files={"file": ("A1.md", "A1 支持跨门店排班。".encode(), "text/markdown")},
            data={"title": "排班"},
        )
        wait(admin, d["run_id"])
        call(
            admin,
            "POST",
            f"/knowledge-bases/{k}/publish",
            {
                "expected_snapshot": None,
                "documents": [{"document_id": d["document_id"], "revision": 1}],
            },
        )
        cfg = {
            "persona": "企业顾问" if workflow == "sales" else "店铺客服",
            "tone": "专业" if workflow == "sales" else "简短",
            "goal": "根据产品资料帮助客户",
            "allowed": ["回答已批准的事实"],
            "forbidden": ["承诺交期"],
            "product_ref": p + "@1",
            "company_ref": t + "@1",
            "knowledge": [{"kb_id": k, "snapshot": 1}],
            "tool_allowlist": ["channel.reply"]
            + (["order.lookup"] if workflow == "support" else []),
            "policy_ref": workflow + ".policy@1",
            "handoff_on": [
                "ask_human",
                "identity_question",
                ("formal_quote" if workflow == "sales" else "refund"),
            ],
        }
        a = call(
            admin,
            "POST",
            "/agents",
            {
                "product_id": p,
                "name": cfg["persona"],
                "workflow": workflow,
                "config": cfg,
            },
        )["agent_id"]
        call(admin, "POST", f"/agents/{a}/publish", {"expected_revision": 1})
        bind = {"agent_id": a, "customer_id": customer, "conversation_id": cv}
        if workflow == "sales":
            tk = call(
                biz,
                "POST",
                "/tasks",
                {"agent_id": a, "agent_version": 1, "purpose": "outreach"},
            )["task_id"]
            wait(biz, call(biz, "POST", f"/tasks/{tk}/start", {})["run_id"])
            bind["task_id"] = tk
        state = call(biz, "POST", "/sessions", bind)
        return a, state

    def say(t, sid, msg):
        biz = t + "-business"
        out = call(
            biz,
            "POST",
            f"/sessions/{sid}/messages",
            {"external_message_id": uuid.uuid4().hex, "text": msg},
        )
        return wait(biz, out["run_id"])

    evidence = {}
    try:
        a, ss = setup("tob", "pA1", "sales", "c1", "cv1")
        b, cs = setup("toc", "pB1", "support", "u2", "cv2")
        sid, cid = ss["session_id"], cs["session_id"]
        quote = say("tob", sid, "我们有20个账号，请给正式报价。")
        good = say("toc", cid, "我的订单 O7 发货了吗？")
        denied = say("toc", cid, "请查 O8 的发货记录。")
        assert (
            quote["result"]["action"] == "handoff"
            and quote["result"]["delivery"] == "not_sent"
        )
        assert (
            good["result"]["delivery"] == "accepted"
            and "已发货" in good["result"]["text"]
        )
        assert denied["result"][
            "reason"
        ] == "ORDER_ACCESS_DENIED" and "备货中" not in str(denied)
        p = "/sessions/" + sid
        msgs = call("tob-admin", "GET", p + "/messages")["items"]
        assert all(m["kind"] == "inbound" for m in msgs)
        se = call("tob-admin", "GET", p)
        h = {"expected_epoch": se["owner_epoch"], "handoff_id": se["handoff_id"]}
        claimed = call("tob-admin", "POST", p + "/takeover", h)
        human = wait(
            "tob-admin",
            call(
                "tob-admin",
                "POST",
                p + "/human-messages",
                {
                    "text": "我已接手，会另外确认需求。",
                    "expected_epoch": claimed["owner_epoch"],
                },
            )["run_id"],
        )
        released = call(
            "tob-admin",
            "POST",
            p + "/release",
            h
            | {
                "expected_epoch": claimed["owner_epoch"],
                "resolution": "resume",
                "note": "人工沟通已记录",
            },
        )
        evidence = {
            "sales": {
                "agent_id": a,
                "session_id": sid,
                "task_id": ss["task_id"],
                "run_id": quote["run_id"],
                "action": quote["result"]["action"],
                "delivery": quote["result"]["delivery"],
                "auto_quote_count": 0,
                "handoff_id": se["handoff_id"],
                "human_delivery": human["result"]["delivery"],
                "released": released["status"],
            },
            "support": {
                "agent_id": b,
                "session_id": cid,
                "task_id": cs["task_id"],
                "run_id": good["run_id"],
                "result": good["result"],
                "tools": good["tool_executions"],
                "denied_run": denied["run_id"],
                "denied": denied["result"],
            },
        }
        # Close the demonstration tasks so subsequent demos can reuse the mock conversations.
        for t, st in [("tob", ss), ("toc", cs)]:
            call(
                t + "-business",
                "POST",
                "/tasks/" + st["task_id"] + "/stop",
                {"reason": "模拟联调完成"},
            )
        return evidence
    finally:
        http.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--tokens", default=".runtime/dev-tokens.json")
    args = p.parse_args()
    print(
        json.dumps(
            demo(args.base, json.loads(Path(args.tokens).read_text())),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
