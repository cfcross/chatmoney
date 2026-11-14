"""Deterministic offline adapter. Faults are injected at construction, never by user text."""

import re
from app.db import uid
from app.llm.base import ModelError, ModelOutput, ToolCall


class MockLLM:
    def __init__(self, fault=None):
        self.fault = fault

    def generate(self, req):
        if self.fault in {"timeout", "invalid_json", "error", "rate_limit"}:
            code = {
                "timeout": "MODEL_TIMEOUT",
                "invalid_json": "MODEL_INVALID_JSON",
                "error": "MODEL_UPSTREAM",
                "rate_limit": "MODEL_RATE_LIMIT",
            }[self.fault]
            raise ModelError(code, True)
        msg = req.current_message
        info = req.context
        result = {}
        if req.mode == "json":
            facts = {}
            for key, pattern in [
                ("seats", r"(\d+)\s*(?:个)?(?:账号|席位)"),
                ("budget", r"预算\s*([\d.]+\s*[万千]?元?)"),
            ]:
                m = re.search(pattern, msg)
                if m:
                    facts[key] = m.group(1)
            result["structured_output"] = {
                "facts": facts,
                "need": msg[:120],
                "signals": [
                    name
                    for name, words in {
                        "formal_quote": ["正式报价", "盖章报价"],
                        "ask_human": ["真人", "人工客服"],
                        "refund": ["退款"],
                        "identity_question": ["你是不是机器人"],
                        "complaint": ["投诉"],
                    }.items()
                    if any(w in msg for w in words)
                ],
            }
        elif (
            req.mode == "tools"
            and not req.tool_history
            and (
                match := re.search(r"\b(O\d+)\b", msg, re.I)
                or re.search(r"(O\d+)", msg, re.I)
            )
        ):
            result["tool_calls"] = [
                ToolCall(
                    id=uid("tc"),
                    name="order.lookup",
                    args={"order_id": match.group(1).upper()},
                )
            ]
        elif req.tool_history:
            import json

            value = json.loads(req.tool_history[-1]["content"])
            result["text"] = f"您的 {value['order_id']} 订单{value['shipping']}。"
        else:
            facts = info.get("facts", {})
            chunks = info.get("chunks", [])
            if ("上次" in msg or "之前" in msg) and facts:
                answer = (
                    "上次确认的是"
                    + "、".join(f"{k}={v['value']}" for k, v in facts.items())
                    + "。"
                )
            elif chunks:
                answer = chunks[0]["body"]
            else:
                answer = "目前没有足够的已批准资料，我可以记录您的具体问题。"
            result["text"] = (
                f"{info.get('persona', '助手')}（{info.get('tone', '简短')}）：{answer}"
            )
        return ModelOutput(
            **result,
            finish_reason="tool_calls" if result.get("tool_calls") else "stop",
            actual_model="mock-local",
            usage=None,
            provider_request_id=uid("mock"),
        )

    def stream(self, req):
        out = self.generate(req)
        value = out.text or ""
        yield value[: len(value) // 2]
        if self.fault == "disconnect":
            raise ModelError("MODEL_STREAM_INCOMPLETE")
        yield value[len(value) // 2 :]
        yield out
