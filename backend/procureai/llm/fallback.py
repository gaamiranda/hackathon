"""Primary/fallback pair of LLM clients (D11): OpenClaw first, the direct gateway when it is unavailable.

The fallback fires once, only on LLMUnavailable from the primary (connection refused, timeout, 5xx,
gateway restart window). Anything else — a request too large, an unparseable answer — is the same
on both routes and is left to the caller. The result's `backend` says who actually answered, so the
War Room can show "via OpenClaw" / "via gateway" per agent.finished event.
"""

import logging

from procureai.llm.base import LLMClient, LLMResult, LLMUnavailable

log = logging.getLogger(__name__)


class FallbackLLMClient:
    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self.primary, self.fallback = primary, fallback
        self.fallbacks = 0  # how often the primary was unavailable in this process (for logs / health)

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        try:
            return self.primary.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
        except LLMUnavailable as exc:
            self.fallbacks += 1
            log.warning("llm %s: primary unavailable (%s); falling back to the direct gateway", task, exc)
        return self.fallback.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
