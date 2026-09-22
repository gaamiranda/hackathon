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
    # Our own payload cap. The starter kit's ~8 KiB WAF limit does not apply: OpenClaw sends 52–60 KB turns to the
    # same gateway successfully (docs/OPENCLAW.md), so 32000 leaves room for a 6000-char document plus the prompt.
    LLM_MAX_BODY_BYTES: int = 32000

    # D11 option C: MODE=live routes every agent call through OpenClaw's /v1/chat/completions on the Lightsail box
    # (dedicated tool-less `procureai` agent) with the direct gateway as automatic fallback; "gateway" = as before.
    LLM_BACKEND: Literal["gateway", "openclaw"] = "gateway"
    OPENCLAW_URL: str = "http://127.0.0.1:18789"  # loopback on the box; locally via `just tunnel`
    OPENCLAW_TOKEN: str = ""  # gateway.auth.token from ~/.openclaw/openclaw.json on the box; backend/.env only
    OPENCLAW_MODEL: str = "openclaw/procureai"  # an agent target, not a provider model (docs/OPENCLAW.md Q1)
    OPENCLAW_TIMEOUT_S: float = 90
    # Which tasks take the OpenClaw route when LLM_BACKEND=openclaw (comma list; D29). Anything not listed goes
    # straight to the direct gateway without counting as a fallback, so a task whose prose keeps tripping the
    # number guard through OpenClaw can be pinned to the gateway without losing the route for the rest.
    OPENCLAW_TASKS: str = "extract,explain,explain_diff,draft"

    # Guardrail judge (PLAN.md D20, T14): TypeSafe Jev answers typed yes/no questions with probabilities; it never
    # generates text or decides anything. The mock keeps the pipeline identical without credentials or network.
    GUARDRAIL_JUDGE: Literal["mock", "jev"] = "mock"
    TYPESAFE_API_KEY: str = ""
    TYPESAFE_MODEL: str = "jev-1.13.0"  # pinned version, not the moving `jev-latest` alias (D20)
    JUDGE_SUPPORT_THRESHOLD: float = 0.6  # P(document states this value) below this → field unsupported
    JUDGE_LEAK_THRESHOLD: float = 0.7
    JUDGE_INJECTION_THRESHOLD: float = 0.7
    JUDGE_CACHE_MODE: Literal["replay_or_record", "record", "replay_only"] = "replay_or_record"
    JUDGE_TIMEOUT_S: float = 10
    JUDGE_MAX_LIVE_CALLS: int = 40  # per process; beyond it the judge reports "not evaluated" instead of spending

    # Run persistence (PLAN.md D5, T19): "" = in-memory only (tests, local dev); a directory = every run is
    # mirrored to <dir>/<run_id>/{run.json,events.jsonl} and loaded back on startup, so a restart or redeploy
    # keeps the in-progress demo runs. On the box: /home/ubuntu/procureai/data/runs.
    RUN_STORE_DIR: str = ""

    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    MAX_DOCUMENT_CHARS: int = 6000  # gateway body limit headroom (PLAN.md §2)

    @property
    def openclaw_tasks(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.OPENCLAW_TASKS.split(",") if t.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
