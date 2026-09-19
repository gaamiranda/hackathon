"""LLM client protocol and errors (PLAN.md D4, D11).

Every call is a single-shot, stateless JSON task: no conversation history ever leaves the backend.
`task` is a short label (extract / explain / draft / explain_diff) used for logging and cache keys.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

TASKS = ("extract", "explain", "draft", "explain_diff", "probe")


class LLMError(RuntimeError):
    """Base for every gateway failure the caller may have to fall back from (G6)."""


class LLMRequestTooLarge(LLMError):
    """Body exceeded settings.LLM_MAX_BODY_BYTES; raised before anything is sent (PLAN.md risk 2)."""

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"request body is {size} bytes, limit is {limit} (gateway WAF)")
        self.size, self.limit = size, limit


class LLMUnavailable(LLMError):
    """Gateway refused or failed after the retry budget (403 rate limit, 429 quota, 5xx, transport)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMCacheMiss(LLMError):
    """LLM_CACHE_MODE=replay_only and no recorded response for this key (CI / offline tests)."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    parsed_json: dict | None = None
    raw: dict = field(default_factory=dict)
    cached: bool = False
    latency_ms: int = 0


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        """One stateless completion.

        `json_mode` appends a strict JSON instruction and populates `parsed_json`; when the model
        still returns unparseable text the client tries one repair call and then leaves it None,
        so the caller (not the LLM) decides whether to escalate to a human.
        """
        ...
