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
    LLM_NUM_PREDICT: int = 2048

    OPENCLAW_GATEWAY_URL: str = ""
    OPENCLAW_TOKEN: str = ""

    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    MAX_DOCUMENT_CHARS: int = 6000  # gateway body limit headroom (PLAN.md §2)


@lru_cache
def get_settings() -> Settings:
    return Settings()
