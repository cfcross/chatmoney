"""Only synthetic credentials. The transport records which fixture key reached each endpoint."""

import json
import secrets

import httpx
from cryptography.fernet import Fernet
from app.llm import DeepSeek
from app.llm.deepseek import SUPPORTED_MODELS
from app.settings import settings

BASE = "/api/model-admin/v1"


class Provider:
    def __init__(self):
        self.keys = {
            k: secrets.token_urlsafe(30) for k in ("a", "b", "rotated", "bad", "legacy")
        }
        self.calls = []
        self.errors = {}
        self.available = list(SUPPORTED_MODELS)
        self.block = None

    def handle(self, req):
        assert req.url.host == "api.deepseek.com" and req.url.scheme == "https"
        value = req.headers["Authorization"].removeprefix("Bearer ")
        who = next(
            (tag for tag, secret in self.keys.items() if value == secret), "unknown"
        )
        path = req.url.path
        body = json.loads(req.content) if req.content else {}
        self.calls.append((who, path, body))
        if self.block and path == "/chat/completions":
            self.block()
        error = self.errors.get(path)
        if error == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=req)
        if error == "network":
            raise httpx.ConnectError("synthetic network failure", request=req)
        if error == "invalid":
            return httpx.Response(200, content=b"{}")
        if isinstance(error, int) or who in {"unknown", "bad"}:
            # An upstream error can contain secrets. It must never be recorded or shown.
            return httpx.Response(
                error if isinstance(error, int) else 401, json={"error": value}
            )
        if path == "/user/balance":
            return httpx.Response(200, json={"is_available": error != "empty"})
        if path == "/models":
            return httpx.Response(
                200, json={"data": [{"id": i} for i in self.available]}
            )
        assert path == "/chat/completions" and req.method == "POST"
        content = (
            '{"facts":{},"need":"排班","signals":[]}'
            if "response_format" in body
            else "根据已批准的资料，产品支持排班。"
        )
        return httpx.Response(
            200,
            json={
                "id": "synthetic-upstream",
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 4,
                    "total_tokens": 11,
                },
            },
        )


def setup_provider(monkeypatch):
    monkeypatch.setenv(
        "CHATMONEY_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    settings.cache_clear()
    provider = Provider()

    def client(adapter):
        return httpx.Client(
            transport=httpx.MockTransport(provider.handle),
            headers={"Authorization": "Bearer " + adapter.key},
            trust_env=False,
        )

    monkeypatch.setattr(DeepSeek, "client", client)
    return provider
