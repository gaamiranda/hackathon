"""Record/replay cache for live Jev calls (same pattern as llm/cache.py; PLAN.md D20 budget: ≤ 40 live calls).

Cache files are committed, so tests and demos replay real verdicts without touching the API.
Key = sha256(model, method, state, questions); files live in data/judge_cache/<method>/<key>.json.
The API key is never part of the key or of what is written to disk.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

CacheMode = Literal["replay_or_record", "record", "replay_only"]

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
CACHE_DIR = DATA_DIR / "judge_cache"


class JudgeUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class JudgeResponse(BaseModel):
    """What one system_one call returns, reduced to what the judge needs: P(yes) per question id."""

    model: str
    answers: dict[str, float]
    usage: JudgeUsage = JudgeUsage()
    cached: bool = False


# (state, questions) → JudgeResponse. The live implementation wraps the TypeSafe SDK; tests can pass a stub.
SystemOneCaller = Callable[[Any, dict[str, dict[str, Any]]], JudgeResponse]


class JudgeCacheMiss(RuntimeError):
    pass


class JudgeBudgetExceeded(RuntimeError):
    pass


def cache_key(model: str, method: str, state: Any, questions: dict[str, dict[str, Any]]) -> str:
    material = "\x00".join([
        model, method,
        json.dumps(state, sort_keys=True, ensure_ascii=False),
        json.dumps(questions, sort_keys=True, ensure_ascii=False),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class JudgeReplayCache:
    def __init__(
        self,
        inner: SystemOneCaller,
        *,
        model: str,
        mode: CacheMode = "replay_or_record",
        cache_dir: Path = CACHE_DIR,
        max_live_calls: int = 40,
    ) -> None:
        self.inner, self.model, self.mode, self.cache_dir = inner, model, mode, cache_dir
        self.max_live_calls = max_live_calls
        self.live_calls = 0
        self.live_input_tokens = 0
        self.replayed = 0

    def path_for(self, method: str, key: str) -> Path:
        return self.cache_dir / method / f"{key}.json"

    def ask(self, method: str, state: Any, questions: dict[str, dict[str, Any]]) -> JudgeResponse:
        key = cache_key(self.model, method, state, questions)
        path = self.path_for(method, key)

        if self.mode != "record" and path.exists():
            self.replayed += 1
            return _load(path)
        if self.mode == "replay_only":
            raise JudgeCacheMiss(f"no recorded verdict for method={method} key={key} ({path})")
        if self.live_calls >= self.max_live_calls:
            raise JudgeBudgetExceeded(f"live Jev call budget of {self.max_live_calls} reached (method={method})")

        response = self.inner(state, questions)
        self.live_calls += 1
        self.live_input_tokens += response.usage.input_tokens
        _store(path, self.model, method, state, questions, response)
        return response


def _load(path: Path) -> JudgeResponse:
    entry = json.loads(path.read_text())
    return JudgeResponse.model_validate({**entry["response"], "cached": True})


def _store(path: Path, model: str, method: str, state: Any, questions: dict[str, Any], response: JudgeResponse) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "request": {"model": model, "method": method, "state": state, "questions": questions},
        "response": response.model_dump(exclude={"cached"}),
    }
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
