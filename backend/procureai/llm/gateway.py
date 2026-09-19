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

JSON_INSTRUCTION = "Respond with a single JSON object and nothing else. No prose, no code fences."
REPAIR_SYSTEM = f"You fix malformed output. {JSON_INSTRUCTION}"
REPAIR_USER = "Return only the JSON object contained in the following text:\n\n"

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
    ) -> None:
        self.settings = settings
        self.sleep = sleep
        self._client = client or httpx.Client(
            base_url=settings.LLM_GATEWAY_URL.rstrip("/"),
            timeout=settings.LLM_TIMEOUT_S,
            follow_redirects=True,
        )

    @property
    def model(self) -> str:
        return self.settings.LLM_MODEL

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        if json_mode:
            system = f"{system.rstrip()}\n{JSON_INSTRUCTION}"
        started = time.perf_counter()
        payload = self._chat(system, user, max_tokens)
        text = _assistant_text(payload)

        parsed = None
        if json_mode:
            parsed = extract_json(text)
            if parsed is None:
                payload = self._chat(REPAIR_SYSTEM, REPAIR_USER + text, max_tokens)
                text = _assistant_text(payload)
                parsed = extract_json(text)

        return LLMResult(
            text=text,
            parsed_json=parsed,
            raw=payload,
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _chat(self, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0},
        }
        encoded = json.dumps(body).encode("utf-8")
        if len(encoded) > self.settings.LLM_MAX_BODY_BYTES:
            raise LLMRequestTooLarge(len(encoded), self.settings.LLM_MAX_BODY_BYTES)
        return self._post(encoded)

    def _post(self, encoded: bytes) -> dict[str, Any]:
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


def extract_json(text: str) -> dict | None:
    """Tolerant extractor: whole string first, then the first balanced {...} block.

    Handles the usual drift (```json fences, a sentence before the object) without regex guesswork.
    """
    candidates = [text.strip()]
    block = _first_object(text)
    if block is not None:
        candidates.append(block)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _first_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
