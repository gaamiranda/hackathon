"""Offline LLM client for unit tests and MODE=mock (PLAN.md D3). No network, no tokens."""

import json

from procureai.llm.base import LLMResult
from procureai.llm.common import extract_json


class MockLLMClient:
    """Returns the text / JSON given at construction, optionally overridden per task label.

    When only `text` is given, json_mode parses it the way the gateway client would, so a test can
    hand an agent realistic model output (code fences, prose, garbage) and exercise the real path."""

    def __init__(
        self,
        text: str = "",
        parsed_json: dict | None = None,
        *,
        by_task: dict[str, tuple[str, dict | None]] | None = None,
    ) -> None:
        self.text = text or (json.dumps(parsed_json) if parsed_json is not None else "")
        self.parsed_json = parsed_json
        self.by_task = by_task or {}
        self.calls: list[dict] = []

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        self.calls.append(
            {"task": task, "system": system, "user": user, "max_tokens": max_tokens, "json_mode": json_mode}
        )
        text, parsed = self.by_task.get(task, (self.text, self.parsed_json))
        if json_mode and parsed is None:
            parsed = extract_json(text)
        return LLMResult(text=text, parsed_json=parsed if json_mode else None, raw={}, cached=False, latency_ms=0, backend="mock")
