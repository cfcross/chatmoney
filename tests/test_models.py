import httpx
import pytest
from app.llm import DeepSeek, MockLLM, ModelInput, ModelError, complete


def test_modes_and_mock_faults():
    m = MockLLM()
    q = ModelInput(instructions="帮助客户", current_message="要20个账号", mode="json")
    assert m.generate(q).structured_output["facts"]["seats"] == "20"
    assert m.generate(
        q.model_copy(update={"mode": "tools", "current_message": "订单 O7"})
    ).tool_calls[0].args == {"order_id": "O7"}
    for fault in ["invalid_json", "timeout", "error"]:
        attempts = []
        with pytest.raises(ModelError):
            complete(MockLLM(fault), q, lambda *x: attempts.append(x), attempts=2)
        assert len(attempts) == 2
    with pytest.raises(ModelError):
        list(MockLLM("disconnect").stream(q.model_copy(update={"mode": "text"})))


def test_deepseek_wire_protocol():
    import json

    q = ModelInput(
        instructions="规则",
        history=[{"role": "user", "content": "昨天"}],
        current_message="今天",
        mode="json",
    )

    def handle(r):
        d = json.loads(r.content)
        assert d["model"] == "deepseek-flash" and d["messages"][1]["content"] == "昨天"
        assert d["response_format"]["type"] == "json_object"
        return httpx.Response(
            200,
            json={
                "id": "provider1",
                "model": "deepseek-flash",
                "choices": [
                    {"message": {"content": '{"x":2}'}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
        )

    model = DeepSeek("deepseek-flash", "dummy", transport=httpx.MockTransport(handle))
    out = model.generate(q)
    assert out.structured_output == {"x": 2} and out.usage["total_tokens"] == 15
    with pytest.raises(ModelError, match="RATE_LIMIT"):
        DeepSeek(
            "deepseek-flash",
            "dummy",
            transport=httpx.MockTransport(lambda _: httpx.Response(429)),
        ).generate(q)


def test_deepseek_stream_end_required():
    import json

    q = ModelInput(instructions="", current_message="hi")
    chunk = {
        "id": "p",
        "model": "m",
        "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
    }

    def model(done):
        wire = (
            "data: " + json.dumps(chunk) + "\n\n" + ("data: [DONE]\n\n" if done else "")
        )
        return DeepSeek(
            "m",
            "dummy",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=wire)),
        )

    assert list(model(True).stream(q))[-1].text == "ok"
    with pytest.raises(ModelError, match="STREAM_INCOMPLETE"):
        list(model(False).stream(q))
