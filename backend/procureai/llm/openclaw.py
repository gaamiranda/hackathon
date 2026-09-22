"""OpenClaw LLM client: OpenAI-compatible POST /v1/chat/completions on the Lightsail gateway (D11 option A).

Same single-shot JSON tasks as the direct gateway client; OpenClaw runs them inside the dedicated
`procureai` agent (no tools, no bootstrap files, fresh session per request — docs/OPENCLAW.md). The
agent's model and num_predict come from openclaw.json, so this client only sends the two messages.
No `user` field is ever sent: that would key a session and give the model memory across calls.

Every failure that the direct gateway can cover (connection refused, timeout, 5xx, a non-JSON body)
is raised as LLMUnavailable so FallbackLLMClient can take over; a 4xx is a configuration problem and
is raised the same way, since retrying it would not help either.
"""

import json
from typing import Any

import httpx

from procureai.config.settings import Settings
from procureai.llm.base import LLMResult, LLMRequestTooLarge, LLMUnavailable
from procureai.llm.common import complete_with_repair

BACKEND = "openclaw"


class OpenClawLLMClient:
    """Sync httpx client for the OpenClaw gateway. One instance per process; safe to share."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.Client(
            base_url=settings.OPENCLAW_URL.rstrip("/"),
            timeout=settings.OPENCLAW_TIMEOUT_S,
        )

    @property
    def model(self) -> str:
        return self.settings.OPENCLAW_MODEL

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
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        encoded = json.dumps(body).encode("utf-8")
        if len(encoded) > self.settings.LLM_MAX_BODY_BYTES:
            raise LLMRequestTooLarge(len(encoded), self.settings.LLM_MAX_BODY_BYTES)
        payload = self._post(encoded)
        return _assistant_text(payload), payload

    def _post(self, encoded: bytes) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.OPENCLAW_TOKEN}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client.post("/v1/chat/completions", content=encoded, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"openclaw transport error: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise LLMUnavailable(
                f"openclaw returned {response.status_code}: {response.text[:200]}", status=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMUnavailable(f"openclaw returned a non-JSON body: {response.text[:200]}") from exc
        if not isinstance(payload, dict) or not payload.get("choices"):
            raise LLMUnavailable(f"openclaw returned no choices: {json.dumps(payload)[:200]}")
        return payload


def _assistant_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):  # OpenAI content-part form; OpenClaw sends a string today
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def probe_openclaw(settings: Settings, *, timeout_s: float = 2.0) -> str:
    """"reachable" | "unreachable": GET {OPENCLAW_URL}/health (no auth, ~1 ms on the box). Never raises."""
    try:
        response = httpx.get(f"{settings.OPENCLAW_URL.rstrip('/')}/health", timeout=timeout_s)
    except httpx.HTTPError:
        return "unreachable"
    return "reachable" if response.status_code == 200 else "unreachable"
