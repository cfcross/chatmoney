from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    upload_actor_bytes: int = Field(2_000_000_000, ge=1_000_000_000)
    upload_workspace_bytes: int = Field(4_000_000_000, ge=1_000_000_000)
    upload_concurrency: int = Field(1, ge=1, le=4)
    upload_total_timeout_seconds: int = Field(3600, ge=1, le=86400)
    upload_idle_timeout_seconds: int = Field(120, ge=1, le=3600)
    upload_disk_reserve_bytes: int = Field(512_000_000, ge=100_000_000)
    model_config = SettingsConfigDict(extra="ignore")
    database_url: SecretStr
    llm_backend: Literal["mock", "deepseek"] = "mock"
    llm_stream_text: bool = False
    chatmoney_credential_encryption_key: SecretStr | None = None
    model_timeout: float = Field(20, gt=0, le=60)
    model_attempts: int = Field(2, ge=1, le=3)
    model_max_tokens: int = Field(800, ge=100, le=4000)
    tool_rounds: int = Field(2, ge=1, le=4)
    rate_per_hour: int = Field(30, ge=1)
    event_retention_hours: int = Field(168, ge=1)
    webhook_config_file: str | None = None
    model_rounds: int = Field(8, ge=2, le=24)
    run_token_budget: int = Field(24000, ge=500)


@lru_cache
def settings():
    return Settings()
