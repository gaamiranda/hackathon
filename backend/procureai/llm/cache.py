"""Record/replay cache for live LLM calls (PLAN.md risk 2b: USD 100 shared credit).

Cache files are committed, so tests and demos replay real gateway responses without spending tokens.
Key = sha256(model, task, system, user, max_tokens); files live in data/llm_cache/<task>/<key>.json.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from procureai.llm.base import LLMCacheMiss, LLMClient, LLMResult

CacheMode = Literal["replay_or_record", "record", "replay_only"]

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
CACHE_DIR = DATA_DIR / "llm_cache"


def cache_key(model: str, task: str, system: str, user: str, max_tokens: int) -> str:
    material = "\x00".join([model, task, system, user, str(max_tokens)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ReplayCache:
    """Wraps any LLMClient. The API key is never part of the key or of what is written to disk."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        model: str,
        mode: CacheMode = "replay_or_record",
        cache_dir: Path = CACHE_DIR,
    ) -> None:
        self.inner, self.model, self.mode, self.cache_dir = inner, model, mode, cache_dir

    def path_for(self, task: str, key: str) -> Path:
        return self.cache_dir / task / f"{key}.json"

    def complete(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResult:
        key = cache_key(self.model, task, system, user, max_tokens)
        path = self.path_for(task, key)

        if self.mode != "record" and path.exists():
            return _load(path)
        if self.mode == "replay_only":
            raise LLMCacheMiss(f"no recorded response for task={task} key={key} ({path})")

        result = self.inner.complete(task, system, user, max_tokens=max_tokens, json_mode=json_mode)
        _store(path, self.model, task, system, user, max_tokens, json_mode, result)
        return result


def _load(path: Path) -> LLMResult:
    entry = json.loads(path.read_text())
    result = entry["result"]
    return LLMResult(
        text=result["text"],
        parsed_json=result.get("parsed_json"),
        raw=result.get("raw", {}),
        cached=True,
        latency_ms=result.get("latency_ms", 0),
    )


def _store(
    path: Path,
    model: str,
    task: str,
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool,
    result: LLMResult,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "request": {
            "model": model,
            "task": task,
            "system": system,
            "user": user,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
        },
        "result": {
            "text": result.text,
            "parsed_json": result.parsed_json,
            "raw": result.raw,
            "latency_ms": result.latency_ms,
        },
    }
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
