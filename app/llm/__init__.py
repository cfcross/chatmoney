from app.llm.base import ModelInput, ModelOutput, ModelError, complete
from app.llm.mock import MockLLM
from app.llm.deepseek import DeepSeek
from app.settings import settings

__all__ = [
    "ModelInput",
    "ModelOutput",
    "ModelError",
    "complete",
    "MockLLM",
    "DeepSeek",
    "adapter",
]


def adapter():
    cfg = settings()
    if cfg.llm_backend == "mock":
        return MockLLM()
    raise ModelError("MODEL_PERSONAL_CONNECTION_REQUIRED")
