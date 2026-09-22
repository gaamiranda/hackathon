"""Organiser LLM gateway client: Ollama-compatible POST /api/chat (PLAN.md D4, docs/INFRA.md).

Hard constraints the gateway imposes, all handled here and nowhere else:
- request body must stay under the WAF limit (settings.LLM_MAX_BODY_BYTES);
- output is capped at 256 tokens unless `options.num_predict` is set, and the cap is reported as
  done_reason="stop", so every call sets num_predict explicitly;
- 403 is rate limiting and 429 is quota, so both are retried slowly and then surfaced as LLMUnavailable.
"""

import json
import time
from typing import Any

import httpx

from procureai.config.settings import Settings
from procureai.llm.base import LLMResult, LLMRequestTooLarge, LLMUnavailable
from procureai.llm.common import JSON_INSTRUCTION, complete_with_repair, extract_json  # noqa: F401  (re-exported)
from procureai.llm.status import TRACKER, RouteTracker

BACKEND = "gateway"
RETRY_STATUSES = frozenset({403, 429})
RETRY_DELAYS_S = (2.0, 4.0)  # 3 attempts total


class GatewayLLMClient:
    """Sync httpx client for the gateway. One instance per process; safe to share."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        model: str | None = None,
        tracker: RouteTracker = TRACKER,
    ) -> None:
        self.settings = settings
        self.sleep = sleep
        self.tracker = tracker  # last-attempt bookkeeping for /health (T17); never makes a call of its own
        self._model = model  # None → settings.LLM_MODEL; set for the LLM_MODEL_FAST client (D18)
        self._client = client or httpx.Client(
            base_url=settings.LLM_GATEWAY_URL.rstrip("/"),
            timeout=settings.LLM_TIMEOUT_S,
            follow_redirects=True,
        )

    @property
    def model(self) -> str:
        return self._model or self.settings.LLM_MODEL

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        return complete_with_repair(self._chat, system, user, max_tokens=max_tokens, json_mode=json_mode, backend=BACKEND)

    def _chat(self, system: str, user: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0},
        }
        encoded = json.dumps(body).encode("utf-8")
        if len(encoded) > self.settings.LLM_MAX_BODY_BYTES:
            raise LLMRequestTooLarge(len(encoded), self.settings.LLM_MAX_BODY_BYTES)
        payload = self._post(encoded)
        return _assistant_text(payload), payload

    def _post(self, encoded: bytes) -> dict[str, Any]:
        try:
            payload = self._post_with_retries(encoded)
        except LLMUnavailable as exc:
            self.tracker.record(BACKEND, False, str(exc))
            raise
        self.tracker.record(BACKEND, True)
        return payload

    def _post_with_retries(self, encoded: bytes) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.LLM_GATEWAY_API_KEY}",
            "Content-Type": "application/json",
        }
        last = "no attempt made"
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            try:
                response = self._client.post("/api/chat", content=encoded, headers=headers)
            except httpx.HTTPError as exc:
                last, status = f"transport error: {type(exc).__name__}: {exc}", None
            else:
                status = response.status_code
                if status == 200:
                    return response.json()
                last = f"gateway returned {status}: {response.text[:200]}"
                if status not in RETRY_STATUSES and status < 500:
                    raise LLMUnavailable(last, status=status)
            if attempt < len(RETRY_DELAYS_S):
                self.sleep(RETRY_DELAYS_S[attempt])
        raise LLMUnavailable(f"{last} (after {len(RETRY_DELAYS_S) + 1} attempts)", status=status)


def _assistant_text(payload: dict[str, Any]) -> str:
    return (payload.get("message") or {}).get("content", "") or ""


def probe_gateway(settings: Settings, *, timeout_s: float = 2.0) -> str:
    """"reachable" | "unreachable": GET {LLM_GATEWAY_URL}/api/tags (~25 ms, free — docs/INFRA.md). Never raises."""
    try:
        response = httpx.get(
            f"{settings.LLM_GATEWAY_URL.rstrip('/')}/api/tags",
            headers={"Authorization": f"Bearer {settings.LLM_GATEWAY_API_KEY}"},
            timeout=timeout_s,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return "unreachable"
    return "reachable" if response.status_code == 200 else "unreachable"
