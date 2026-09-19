"""Gateway client, JSON recovery and record/replay cache. No network: httpx.MockTransport everywhere.

The replay tests read the committed data/llm_cache/ entries recorded against the live gateway in T4,
so the extraction evidence (11.20 / 12.80 / 13.40, injection ignored) is checked on every run for free.
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from procureai.config.settings import Settings
from procureai.llm.base import LLMCacheMiss, LLMRequestTooLarge, LLMResult, LLMUnavailable
from procureai.llm.cache import ReplayCache, cache_key
from procureai.llm.gateway import JSON_INSTRUCTION, GatewayLLMClient, extract_json
from procureai.llm.mock import MockLLMClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
from record_extractions import (  # noqa: E402
    DOCS,
    EXPECTED_UNIT_PRICE,
    EXTRACTION_SYSTEM,
    INJECTION_MARKERS,
    MAX_TOKENS,
    TASK,
    document_text,
)

CACHE_DIR = BACKEND.parent / "data" / "llm_cache"
EXTRACT_CACHE = CACHE_DIR / "extract"


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "eval_count": 7,
        },
    )


def build_client(handler, **overrides) -> tuple[GatewayLLMClient, list[float]]:
    """Gateway client wired to a stub transport, with sleep replaced so retries cost no wall-clock."""
    settings = Settings(
        LLM_GATEWAY_URL="https://gateway.invalid",
        LLM_GATEWAY_API_KEY="test-key",
        LLM_MODEL="test-model",
        **overrides,
    )
    slept: list[float] = []
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=settings.LLM_GATEWAY_URL)
    return GatewayLLMClient(settings, client=http, sleep=slept.append), slept


# --- request shape -------------------------------------------------------------------------------


def test_request_body_and_auth_header():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_response("hello")

    client, _ = build_client(handler)
    result = client.complete("explain", "You rank suppliers.", "Why B?", max_tokens=128)

    assert result.text == "hello"
    assert result.parsed_json is None and result.cached is False
    request = seen[0]
    assert request.url.path == "/api/chat"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["options"] == {"num_predict": 128, "temperature": 0}
    assert body["messages"] == [
        {"role": "system", "content": "You rank suppliers."},
        {"role": "user", "content": "Why B?"},
    ]


def test_json_mode_appends_strict_instruction():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_response('{"ok": true}')

    client, _ = build_client(handler)
    result = client.complete("extract", "Extract fields.", "doc", json_mode=True)

    system = json.loads(seen[0].content)["messages"][0]["content"]
    assert system == f"Extract fields.\n{JSON_INSTRUCTION}"
    assert result.parsed_json == {"ok": True}


def test_body_over_the_limit_is_never_sent():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("oversized body reached the gateway")

    client, _ = build_client(handler, LLM_MAX_BODY_BYTES=500)
    with pytest.raises(LLMRequestTooLarge) as excinfo:
        client.complete("extract", "system", "x" * 2000)
    assert excinfo.value.limit == 500
    assert excinfo.value.size > 500


# --- retries -------------------------------------------------------------------------------------


def test_retries_on_429_then_succeeds():
    statuses = [429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return chat_response("recovered") if status == 200 else httpx.Response(status, text="quota")

    client, slept = build_client(handler)
    assert client.complete("explain", "s", "u").text == "recovered"
    assert slept == [2.0]


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_gives_up_after_three_attempts(status):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(status, text="nope")

    client, slept = build_client(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        client.complete("explain", "s", "u")
    assert excinfo.value.status == status
    assert len(attempts) == 3
    assert slept == [2.0, 4.0]


def test_client_error_is_not_retried():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(400, text="bad model")

    client, slept = build_client(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        client.complete("explain", "s", "u")
    assert excinfo.value.status == 400
    assert len(attempts) == 1 and slept == []


def test_transport_error_is_retried_then_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gateway down")

    client, slept = build_client(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        client.complete("explain", "s", "u")
    assert excinfo.value.status is None
    assert slept == [2.0, 4.0]


# --- JSON recovery -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"unit_price": 11.2}',
        '```json\n{"unit_price": 11.2}\n```',
        '```\n{"unit_price": 11.2}\n```',
        'Here is the quote:\n{"unit_price": 11.2}\nHope that helps.',
        '{"unit_price": 11.2} trailing noise',
    ],
)
def test_extract_json_tolerates_drift(text):
    assert extract_json(text) == {"unit_price": 11.2}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", '{"unbalanced": '])
def test_extract_json_returns_none_when_hopeless(text):
    assert extract_json(text) is None


def test_extract_json_keeps_braces_inside_strings():
    assert extract_json('{"note": "a } brace", "n": 1}') == {"note": "a } brace", "n": 1}


def test_repair_call_recovers_unparseable_json():
    replies = ["Sure! Here you go: unit price is 11.20", '{"unit_price": 11.2}']
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return chat_response(replies[len(seen) - 1])

    client, _ = build_client(handler)
    result = client.complete("extract", "Extract fields.", "doc", json_mode=True)

    assert len(seen) == 2, "exactly one repair call"
    assert result.parsed_json == {"unit_price": 11.2}
    assert replies[0] in seen[1]["messages"][1]["content"], "repair call quotes the bad output"


def test_repair_failure_leaves_the_decision_to_the_caller():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return chat_response("still not json")

    client, _ = build_client(handler)
    result = client.complete("extract", "Extract fields.", "doc", json_mode=True)

    assert len(calls) == 2, "no more than one repair attempt"
    assert result.parsed_json is None
    assert result.text == "still not json"


# --- cache ---------------------------------------------------------------------------------------


def test_cache_records_on_miss_and_replays_on_hit(tmp_path):
    inner = MockLLMClient(parsed_json={"unit_price": 11.2})
    cache = ReplayCache(inner, model="test-model", cache_dir=tmp_path)

    first = cache.complete(TASK, "sys", "user", max_tokens=64, json_mode=True)
    assert first.cached is False and len(inner.calls) == 1

    path = tmp_path / TASK / f"{cache_key('test-model', TASK, 'sys', 'user', 64, True)}.json"
    entry = json.loads(path.read_text())
    assert entry["request"] == {
        "model": "test-model",
        "task": TASK,
        "system": "sys",
        "user": "user",
        "max_tokens": 64,
        "json_mode": True,
    }
    assert "test-key" not in path.read_text(), "no credentials on disk"

    second = cache.complete(TASK, "sys", "user", max_tokens=64, json_mode=True)
    assert second.cached is True and second.parsed_json == {"unit_price": 11.2}
    assert len(inner.calls) == 1, "a hit must not reach the gateway"


def test_cache_key_separates_different_requests(tmp_path):
    inner = MockLLMClient("x")
    cache = ReplayCache(inner, model="test-model", cache_dir=tmp_path)
    cache.complete(TASK, "sys", "user-a", max_tokens=64)
    cache.complete(TASK, "sys", "user-b", max_tokens=64)
    cache.complete(TASK, "sys", "user-a", max_tokens=128)
    cache.complete(TASK, "sys", "user-a", max_tokens=64, json_mode=True)  # D19: json_mode is part of the key
    assert len(list((tmp_path / TASK).glob("*.json"))) == 4
    assert len(inner.calls) == 4


def test_record_mode_always_calls_and_overwrites(tmp_path):
    inner = MockLLMClient("first")
    cache = ReplayCache(inner, model="test-model", mode="record", cache_dir=tmp_path)
    cache.complete(TASK, "sys", "user")
    inner.text = "second"
    result = cache.complete(TASK, "sys", "user")

    assert result.cached is False and len(inner.calls) == 2
    path = tmp_path / TASK / f"{cache_key('test-model', TASK, 'sys', 'user', 1024, False)}.json"
    assert json.loads(path.read_text())["result"]["text"] == "second"


def test_replay_only_raises_on_a_miss(tmp_path):
    cache = ReplayCache(_NoNetwork(), model="test-model", mode="replay_only", cache_dir=tmp_path)
    with pytest.raises(LLMCacheMiss):
        cache.complete(TASK, "sys", "user")


class _NoNetwork:
    """Stand-in for the gateway in replay_only mode: any call is a test failure."""

    def complete(self, *args, **kwargs) -> LLMResult:  # pragma: no cover - must not run
        raise AssertionError("replay_only must never reach the gateway")


# --- committed extraction evidence (recorded live in T4) -------------------------------------------


@pytest.fixture(scope="module")
def recorded_model() -> str:
    entries = sorted(EXTRACT_CACHE.glob("*.json"))
    assert entries, f"no committed extraction cache in {EXTRACT_CACHE}"
    models = {json.loads(p.read_text())["request"]["model"] for p in entries}
    assert len(models) == 1, f"cache mixes models: {models}"
    return models.pop()


@pytest.mark.parametrize("name", DOCS)
def test_replays_committed_extraction(recorded_model, name):
    cache = ReplayCache(_NoNetwork(), model=recorded_model, mode="replay_only", cache_dir=CACHE_DIR)
    result = cache.complete(TASK, EXTRACTION_SYSTEM, document_text(name), max_tokens=MAX_TOKENS, json_mode=True)

    assert result.cached is True
    assert result.parsed_json is not None
    assert Decimal(str(result.parsed_json["unit_price"])) == EXPECTED_UNIT_PRICE[name]
    assert result.parsed_json["currency"] == "USD"
    assert result.parsed_json["quantity_quoted"] == 2000


def test_recorded_extraction_ignored_the_injection_line(recorded_model):
    name = "supplier_c_cobalt.eml.txt"
    text = document_text(name)
    assert "ignore previous instructions" in text.lower(), "fixture lost its injection line"

    cache = ReplayCache(_NoNetwork(), model=recorded_model, mode="replay_only", cache_dir=CACHE_DIR)
    result = cache.complete(TASK, EXTRACTION_SYSTEM, text, max_tokens=MAX_TOKENS, json_mode=True)

    blob = json.dumps(result.parsed_json).lower()
    assert not any(marker in blob for marker in INJECTION_MARKERS)
    assert set(result.parsed_json) <= {
        "unit_price",
        "currency",
        "quantity_quoted",
        "moq",
        "lead_time_days",
        "shipping_cost",
        "payment_terms",
        "capacity_units",
        "stated_total",
    }


# --- mock / factory ------------------------------------------------------------------------------


def test_mock_client_records_calls_and_answers_per_task():
    mock = MockLLMClient("default", by_task={"draft": ("a draft", None)})
    assert mock.complete("draft", "s", "u").text == "a draft"
    assert mock.complete("explain", "s", "u").text == "default"
    assert [c["task"] for c in mock.calls] == ["draft", "explain"]


def test_factory_returns_a_mock_outside_live_mode():
    from procureai.llm.factory import build_llm_client

    assert isinstance(build_llm_client(Settings(MODE="mock")), MockLLMClient)


def test_factory_wires_cache_around_the_gateway_in_live_mode():
    from procureai.llm.factory import build_llm_client

    client = build_llm_client(
        Settings(MODE="live", LLM_GATEWAY_URL="https://gateway.invalid", LLM_CACHE_MODE="replay_only")
    )
    assert isinstance(client, ReplayCache)
    assert isinstance(client.inner, GatewayLLMClient)
    assert client.mode == "replay_only"
