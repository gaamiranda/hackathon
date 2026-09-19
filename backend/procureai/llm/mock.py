"""Offline LLM client for unit tests and MODE=mock (PLAN.md D3). No network, no tokens."""

import json

from procureai.llm.base import LLMResult


class MockLLMClient:
    """Returns the text / JSON given at construction, optionally overridden per task label."""

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
        return LLMResult(text=text, parsed_json=parsed if json_mode else None, raw={}, cached=False, latency_ms=0)
