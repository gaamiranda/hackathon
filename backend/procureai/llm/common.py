"""Shared pieces of every live LLM client: the JSON-mode instruction, the tolerant JSON extractor and
the one-repair-call loop (PLAN.md §2: Sonnet wraps JSON in ```json fences whatever the instruction says).

Clients differ only in transport (`chat(system, user, max_tokens) -> (text, raw_payload)`); everything
about turning a text answer into `parsed_json` lives here so gateway.py and openclaw.py cannot drift.
"""

import json
import time
from collections.abc import Callable
from typing import Any

from procureai.llm.base import LLMResult

JSON_INSTRUCTION = "Respond with a single JSON object and nothing else. No prose, no code fences."
REPAIR_SYSTEM = f"You fix malformed output. {JSON_INSTRUCTION}"
REPAIR_USER = "Return only the JSON object contained in the following text:\n\n"

Chat = Callable[[str, str, int], tuple[str, dict[str, Any]]]


def complete_with_repair(chat: Chat, system: str, user: str, *, max_tokens: int, json_mode: bool, backend: str) -> LLMResult:
    """One completion; in json_mode, one repair call when the answer holds no JSON object.

    The caller (not the LLM) decides what an unparseable answer means, so parsed_json is left None
    after the repair attempt instead of raising."""
    if json_mode:
        system = f"{system.rstrip()}\n{JSON_INSTRUCTION}"
    started = time.perf_counter()
    text, payload = chat(system, user, max_tokens)

    parsed = None
    if json_mode:
        parsed = extract_json(text)
        if parsed is None:
            text, payload = chat(REPAIR_SYSTEM, REPAIR_USER + text, max_tokens)
            parsed = extract_json(text)

    return LLMResult(
        text=text,
        parsed_json=parsed,
        raw=payload,
        cached=False,
        latency_ms=int((time.perf_counter() - started) * 1000),
        backend=backend,
    )


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
