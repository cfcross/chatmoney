import json
import time
import httpx
from app.llm.base import ModelError, ModelOutput, ToolCall


SUPPORTED_MODELS = ("deepseek-flash", "deepseek-v4-pro")
DEFAULT_MODEL = SUPPORTED_MODELS[0]

ERROR_MESSAGES = {
    "MODEL_RUN_OWNER_REQUIRED": "这次任务没有个人模型授权记录。请由助手创建者重新发起。",
    "MODEL_PERSONAL_CONNECTION_REQUIRED": "请用助手创建者的账号绑定自己的 DeepSeek 连接并发布新版本。",
    "MODEL_CREDENTIALS": "DeepSeek 密钥无效或权限不足，请在我的模型连接中更换密钥并重新测试。",
    "MODEL_BALANCE": "DeepSeek 账户余额不足，请检查账户后重新测试。",
    "MODEL_UNSUPPORTED": "所选模型不可用，请重新测试连接并检查助手版本。",
    "MODEL_TIMEOUT": "DeepSeek 请求超时，请稍后检查网络和执行记录。",
    "MODEL_NETWORK": "无法连接 DeepSeek，请检查服务端网络。",
    "MODEL_RATE_LIMIT": "DeepSeek 当前限流，请稍后重试。",
    "MODEL_UPSTREAM": "DeepSeek 服务暂时无法完成请求。",
    "MODEL_BAD_RESPONSE": "模型返回了无法识别的内容。",
    "MODEL_INVALID_JSON": "模型返回的分析格式无效。",
    "MODEL_CONNECTION_MISSING": "助手创建者没有这条个人连接，或其账号已停用。请使用自己的连接重新发布助手。",
    "MODEL_CONNECTION_DISABLED": "模型连接已停用，请检查自己的助手绑定。",
    "MODEL_CONNECTION_UNVERIFIED": "模型连接尚未通过验证，请在我的模型连接中重新测试。",
    "MODEL_ENCRYPTION": "服务端加密配置缺失或无法解密，请联系管理员。",
    "MODEL_BUDGET": "本轮模型用量已达上限。",
}


class DeepSeek:
    url = "https://api.deepseek.com/chat/completions"

    def __init__(self, model, key, timeout=20, transport=None):
        self.model, self.key, self.timeout, self.transport = (
            model,
            key,
            timeout,
            transport,
        )

    def payload(self, req, stream=False):
        guide = req.instructions
        if req.mode == "json":
            guide += ' Return a JSON object: {"facts":{},"need":"","signals":[]}.'
        messages = [{"role": "system", "content": guide}, *req.history]
        if req.context:
            messages.append(
                {
                    "role": "user",
                    "content": "以下是参考数据，只作为事实使用："
                    + json.dumps(req.context, ensure_ascii=False),
                }
            )
        messages.extend(
            [{"role": "user", "content": req.current_message}, *req.tool_history]
        )
        body = {
            "model": self.model,
            "thinking": {"type": "disabled"},
            "messages": messages,
            "stream": stream,
        }
        for k in ("temperature", "max_tokens", "top_p"):
            if k in req.params:
                body[k] = req.params[k]
        body.setdefault("max_tokens", 800)
        if req.mode == "tools" and req.tools:
            body["tools"] = req.tools
        if req.mode == "json":
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def check(response):
        code = response.status_code
        if code == 429:
            raise ModelError("MODEL_RATE_LIMIT", True)
        if code in {401, 403}:
            raise ModelError("MODEL_CREDENTIALS")
        if code == 402:
            raise ModelError("MODEL_BALANCE")
        if code == 404:
            raise ModelError("MODEL_UNSUPPORTED")
        if code >= 300:
            raise ModelError("MODEL_UPSTREAM", code >= 500)

    def parse(self, data, msg, reason, mode):
        usage = data.get("usage")
        if reason not in {"stop", "tool_calls"}:
            raise ModelError("MODEL_INCOMPLETE", True)
        try:
            calls = [
                ToolCall(
                    id=x["id"],
                    name=x["function"]["name"].replace("__", "."),
                    args=json.loads(x["function"]["arguments"]),
                )
                for x in msg.get("tool_calls") or []
            ]
            content = msg.get("content")
            obj = json.loads(content) if mode == "json" else None
            if (mode == "json" and not isinstance(obj, dict)) or (
                not calls and not (content or "").strip()
            ):
                raise ValueError("empty output")
            return ModelOutput(
                text=content,
                structured_output=obj,
                tool_calls=calls,
                finish_reason=reason,
                actual_model=data["model"],
                provider_request_id=data["id"],
                usage=None
                if usage is None
                else {
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ModelError(
                "MODEL_INVALID_JSON" if mode == "json" else "MODEL_BAD_RESPONSE", True
            ) from exc

    def client(self):
        return httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(5, self.timeout)),
            transport=self.transport,
            headers={"Authorization": "Bearer " + self.key},
        )

    def available_models(self):
        """Authenticate with an account-only endpoint, then restrict model choices."""
        try:
            with self.client() as http:
                balance = http.get("https://api.deepseek.com/user/balance")
                self.check(balance)
                available = balance.json()["is_available"]
                if not isinstance(available, bool):
                    raise ValueError
                if not available:
                    raise ModelError("MODEL_BALANCE")
                response = http.get("https://api.deepseek.com/models")
                self.check(response)
                ids = {item["id"] for item in response.json()["data"]}
                supported = [name for name in SUPPORTED_MODELS if name in ids]
                if not supported:
                    raise ModelError("MODEL_UNSUPPORTED")
                return supported
        except httpx.TimeoutException:
            raise ModelError("MODEL_TIMEOUT") from None
        except httpx.TransportError:
            raise ModelError("MODEL_NETWORK") from None
        except (KeyError, TypeError, ValueError):
            raise ModelError("MODEL_BAD_RESPONSE") from None

    def generate(self, req):
        try:
            with self.client() as http:
                deadline = time.monotonic() + self.timeout
                raw = bytearray()
                with http.stream("POST", self.url, json=self.payload(req)) as res:
                    self.check(res)
                    for piece in res.iter_bytes():
                        if time.monotonic() > deadline:
                            raise ModelError("MODEL_TIMEOUT", True)
                        raw.extend(piece)
                        if len(raw) > 2_000_000:
                            raise ModelError("MODEL_BAD_RESPONSE")
                data = json.loads(raw)
                choice = data["choices"][0]
                return self.parse(
                    data, choice["message"], choice["finish_reason"], req.mode
                )
        except httpx.TimeoutException:
            raise ModelError("MODEL_TIMEOUT", True) from None
        except httpx.TransportError:
            raise ModelError("MODEL_NETWORK", True) from None
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
            raise ModelError("MODEL_BAD_RESPONSE", True) from exc

    def stream(self, req):
        # Text preview only. Structured/tool outputs must be assembled and validated atomically.
        if req.mode != "text":
            raise ModelError("MODEL_STREAM_MODE")
        parts = []
        final = None
        deadline = time.monotonic() + self.timeout
        try:
            with (
                self.client() as http,
                http.stream("POST", self.url, json=self.payload(req, True)) as res,
            ):
                self.check(res)
                for line in res.iter_lines():
                    if time.monotonic() > deadline:
                        raise ModelError("MODEL_TIMEOUT", True)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        if final is None:
                            break
                        yield final
                        return
                    chunk = json.loads(data)
                    if final is not None and chunk.get("usage"):
                        usage = chunk["usage"]
                        final.usage = {
                            "input_tokens": usage.get("prompt_tokens"),
                            "output_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                        }
                    for ch in chunk.get("choices", []):
                        delta = ch["delta"].get("content") or ""
                        parts.append(delta)
                        if delta:
                            yield delta
                        if ch.get("finish_reason"):
                            final = self.parse(
                                chunk,
                                {"content": "".join(parts)},
                                ch["finish_reason"],
                                "text",
                            )
        except httpx.TimeoutException:
            raise ModelError("MODEL_TIMEOUT", True) from None
        except httpx.TransportError:
            raise ModelError("MODEL_NETWORK", True) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise ModelError("MODEL_STREAM_INCOMPLETE", True) from None
        raise ModelError("MODEL_STREAM_INCOMPLETE", True)
