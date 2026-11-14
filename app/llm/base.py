import time
from contextlib import nullcontext
from collections.abc import Iterator
from typing import Literal, Protocol
from pydantic import BaseModel, Field
from app.settings import settings


class ModelError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class ModelInput(BaseModel):
    instructions: str
    history: list[dict] = Field(default_factory=list)
    current_message: str
    mode: Literal["text", "json", "tools"] = "text"
    tools: list[dict] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)
    tool_history: list[dict] = Field(default_factory=list)


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict


class ModelOutput(BaseModel):
    text: str | None = None
    structured_output: dict | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str
    actual_model: str
    usage: dict | None = None
    provider_request_id: str


class Adapter(Protocol):
    def generate(self, request: ModelInput) -> ModelOutput: ...
    def stream(self, request: ModelInput) -> Iterator[str | ModelOutput]: ...


def complete(
    model: Adapter | None,
    req: ModelInput,
    audit=lambda *_: None,
    attempts=None,
    *,
    resolve=None,
):
    limit = attempts or settings().model_attempts
    for i in range(1, limit + 1):
        try:
            with resolve() if resolve else nullcontext(model) as client:
                if req.mode == "text" and settings().llm_stream_text:
                    out = None
                    for fragment in client.stream(req):
                        if isinstance(fragment, ModelOutput):
                            out = fragment
                    if out is None:
                        raise ModelError("MODEL_STREAM_INCOMPLETE", True)
                else:
                    out = client.generate(req)
            if out.finish_reason not in {"stop", "tool_calls"}:
                raise ModelError("MODEL_INCOMPLETE", True)
            if req.mode == "json" and out.structured_output is None:
                raise ModelError("MODEL_INVALID_JSON", True)
            audit(i, out, None)
            return out
        except ModelError as e:
            audit(i, None, e.code)
            if not e.retryable or i == limit:
                raise
            time.sleep(min(0.15 * 2 ** (i - 1), 1))
