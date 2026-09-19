"""Runtime settings. Single place for every env-driven knob (PLAN.md D4)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    MODE: Literal["mock", "live"] = "mock"

    LLM_GATEWAY_URL: str = ""
    LLM_GATEWAY_API_KEY: str = ""
    LLM_MODEL: str = "sonnet4.5:latest"
    # Low-stakes text only (change explanations, D18); "" = same as LLM_MODEL. Not "haiku:latest" by default:
    # the gateway refused it on 2026-09-19 ("Only the approved model is allowed" for claude-3-haiku).
    LLM_MODEL_FAST: str = ""
    LLM_NUM_PREDICT: int = 2048
    # replay_or_record = spend a token only on a cache miss; replay_only = offline/CI (PLAN.md risk 2b)
    LLM_CACHE_MODE: Literal["replay_or_record", "record", "replay_only"] = "replay_or_record"
    LLM_TIMEOUT_S: float = 60
    LLM_MAX_BODY_BYTES: int = 7000  # WAF rejects ~8 KiB; headroom for the JSON envelope (docs/INFRA.md)

    OPENCLAW_GATEWAY_URL: str = ""
    OPENCLAW_TOKEN: str = ""

    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    MAX_DOCUMENT_CHARS: int = 6000  # gateway body limit headroom (PLAN.md §2)


@lru_cache
def get_settings() -> Settings:
    return Settings()
