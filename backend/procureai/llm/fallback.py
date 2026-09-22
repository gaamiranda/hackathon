"""Primary/fallback pair of LLM clients (D11): OpenClaw first, the direct gateway when it is unavailable.

The fallback fires once, only on LLMUnavailable from the primary (connection refused, timeout, 5xx,
gateway restart window). Anything else — a request too large, an unparseable answer — is the same
on both routes and is left to the caller. The result's `backend` says who actually answered, so the
War Room can show "via OpenClaw" / "via gateway" per agent.finished event.

`tasks` (settings.OPENCLAW_TASKS, D29) limits which task labels take the primary at all: a task not
in the set goes straight to the fallback, silently — it is routing, not a failure, so `fallbacks`
does not count it.
"""

import logging

from procureai.llm.base import LLMClient, LLMResult, LLMUnavailable

log = logging.getLogger(__name__)


class FallbackLLMClient:
    def __init__(self, primary: LLMClient, fallback: LLMClient, *, tasks: frozenset[str] | None = None) -> None:
        self.primary, self.fallback = primary, fallback
        self.tasks = tasks  # None = every task through the primary
        self.fallbacks = 0  # how often the primary was unavailable in this process (for logs / health)

    def routes_to_primary(self, task: str) -> bool:
        return self.tasks is None or task in self.tasks

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        if not self.routes_to_primary(task):
            return self.fallback.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
        try:
            return self.primary.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
        except LLMUnavailable as exc:
            self.fallbacks += 1
            log.warning("llm %s: primary unavailable (%s); falling back to the direct gateway", task, exc)
        return self.fallback.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
