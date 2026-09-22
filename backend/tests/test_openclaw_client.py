"""OpenClaw client, fallback pair and health degradation (T10b, D11). No network: httpx.MockTransport only."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from procureai.config.settings import Settings
from procureai.llm.base import LLMRequestTooLarge, LLMResult, LLMUnavailable
from procureai.llm.cache import ReplayCache, cache_key
from procureai.llm.common import JSON_INSTRUCTION
from procureai.llm.factory import build_llm_client
from procureai.llm.fallback import FallbackLLMClient
from procureai.llm.gateway import GatewayLLMClient
from procureai.llm.mock import MockLLMClient
from procureai.llm.openclaw import OpenClawLLMClient, probe_openclaw


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl_test",
            "object": "chat.completion",
            "model": "openclaw/procureai",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3600, "completion_tokens": 40},
        },
    )


def settings(**overrides) -> Settings:
    base = dict(
        MODE="live",
        LLM_BACKEND="openclaw",
        LLM_GATEWAY_URL="https://gateway.invalid",
        LLM_GATEWAY_API_KEY="gw-key",
        LLM_MODEL="test-model",
        OPENCLAW_URL="http://openclaw.invalid:18789",
        OPENCLAW_TOKEN="oc-token",
        LLM_CACHE_MODE="replay_only",
    )
    return Settings(**{**base, **overrides})


def openclaw_client(handler, **overrides) -> OpenClawLLMClient:
    s = settings(**overrides)
    return OpenClawLLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler), base_url=s.OPENCLAW_URL))


def gateway_client(handler, **overrides) -> GatewayLLMClient:
    s = settings(**overrides)
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=s.LLM_GATEWAY_URL)
    return GatewayLLMClient(s, client=http, sleep=lambda _: None, model=s.LLM_MODEL)


# --- request body and parsing ----------------------------------------------------------------


def test_openclaw_body_shape_auth_and_parsing():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return completion('```json\n{"supplier_name": "Apex", "unit_price": 11.2}\n```')

    result = openclaw_client(handler).complete("extract", "You extract quotes.", "<document>…</document>", max_tokens=600, json_mode=True)

    request = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer oc-token"
    body = json.loads(request.content)
    assert body["model"] == "openclaw/procureai"
    assert body["stream"] is False and body["temperature"] == 0 and body["max_tokens"] == 600
    assert "user" not in body, "a `user` field would key a session and give the agent memory across calls"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"].endswith(JSON_INSTRUCTION)
    assert body["messages"][1]["content"] == "<document>…</document>"

    assert result.backend == "openclaw" and result.cached is False
    assert result.parsed_json == {"supplier_name": "Apex", "unit_price": 11.2}
    assert result.raw["usage"]["prompt_tokens"] == 3600


def test_openclaw_repair_call_when_first_answer_has_no_json():
    answers = iter(["Sure! Here is what I found.", '{"ok": true}'])
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return completion(next(answers))

    result = openclaw_client(handler).complete("explain", "sys", "usr", json_mode=True)
    assert result.parsed_json == {"ok": True}
    assert len(seen) == 2 and seen[1]["messages"][1]["content"].endswith("Sure! Here is what I found.")


def test_openclaw_body_over_the_limit_is_never_sent():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return completion("x")

    with pytest.raises(LLMRequestTooLarge):
        openclaw_client(handler, LLM_MAX_BODY_BYTES=500).complete("extract", "s", "u" * 600)
    assert calls == []


@pytest.mark.parametrize(
    "failure",
    ["connect", "timeout", 500, 502, 401, "not-json", "no-choices"],
)
def test_openclaw_failures_surface_as_unavailable(failure):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "not-json":
            return httpx.Response(200, text="<html>bad gateway</html>")
        if failure == "no-choices":
            return httpx.Response(200, json={"error": {"message": "agent not found"}})
        return httpx.Response(failure, json={"error": {"message": "nope"}})

    with pytest.raises(LLMUnavailable):
        openclaw_client(handler).complete("explain", "s", "u")


# --- fallback ------------------------------------------------------------------------------------


def test_fallback_answers_from_the_gateway_when_openclaw_is_down():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway_calls: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        gateway_calls.append(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"rationale": "B wins"}'}, "done": True})

    client = FallbackLLMClient(primary=openclaw_client(down), fallback=gateway_client(gateway))
    result = client.complete("explain", "sys", "usr", max_tokens=300, json_mode=True)

    assert result.backend == "gateway" and result.parsed_json == {"rationale": "B wins"}
    assert len(gateway_calls) == 1 and gateway_calls[0].url.path == "/api/chat"
    assert client.fallbacks == 1


def test_fallback_is_not_used_when_openclaw_answers():
    def gateway(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must never be called
        raise AssertionError("gateway must not be called when OpenClaw answers")

    client = FallbackLLMClient(primary=openclaw_client(lambda r: completion("fine")), fallback=gateway_client(gateway))
    result = client.complete("explain", "s", "u")
    assert result.backend == "openclaw" and result.text == "fine" and client.fallbacks == 0


def test_fallback_does_not_swallow_other_errors():
    def gateway(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("not a fallback case")

    client = FallbackLLMClient(primary=openclaw_client(lambda r: completion("x"), LLM_MAX_BODY_BYTES=100), fallback=gateway_client(gateway))
    with pytest.raises(LLMRequestTooLarge):
        client.complete("extract", "s", "u" * 200)


# --- per-task routing (D29) -----------------------------------------------------------------------


def test_tasks_outside_openclaw_tasks_go_straight_to_the_gateway():
    """A task pinned to the gateway never touches OpenClaw and is not counted as a fallback."""
    openclaw_calls: list[httpx.Request] = []

    def openclaw(request: httpx.Request) -> httpx.Response:
        openclaw_calls.append(request)
        return completion("from openclaw")

    def gateway(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "from gateway"}, "done": True})

    client = FallbackLLMClient(primary=openclaw_client(openclaw), fallback=gateway_client(gateway),
                               tasks=frozenset({"extract", "explain_diff"}))
    assert client.complete("explain", "s", "u").backend == "gateway"
    assert client.complete("extract", "s", "u").backend == "openclaw"
    assert len(openclaw_calls) == 1 and client.fallbacks == 0


def test_openclaw_tasks_setting_parses_a_comma_list():
    assert settings().openclaw_tasks == {"extract", "explain", "explain_diff", "draft"}
    assert settings(OPENCLAW_TASKS=" extract , draft,,").openclaw_tasks == {"extract", "draft"}
    assert settings(OPENCLAW_TASKS="").openclaw_tasks == frozenset()
    client = build_llm_client(settings(OPENCLAW_TASKS="extract"))
    assert client.inner.tasks == {"extract"} and not client.inner.routes_to_primary("explain")


# --- cache key independence ----------------------------------------------------------------------


def test_cache_key_ignores_the_backend(tmp_path):
    """Same task text → same entry whether OpenClaw or the gateway answered, so committed entries keep replaying."""
    args = ("extract", "sys", "usr", 600, True)
    assert cache_key("test-model", *args) == cache_key("test-model", *args)

    via_openclaw = ReplayCache(openclaw_client(lambda r: completion('{"a": 1}')), model="test-model", mode="record", cache_dir=tmp_path)
    first = via_openclaw.complete("extract", "sys", "usr", max_tokens=600, json_mode=True)
    assert first.backend == "openclaw"

    gateway = gateway_client(lambda r: httpx.Response(200, json={"message": {"content": '{"a": 2}'}}))
    via_gateway = ReplayCache(gateway, model="test-model", mode="replay_or_record", cache_dir=tmp_path)
    replayed = via_gateway.complete("extract", "sys", "usr", max_tokens=600, json_mode=True)
    assert replayed.cached is True and replayed.backend == "replay"
    assert replayed.parsed_json == {"a": 1}, "the OpenClaw-recorded entry must replay for the gateway route"
    stored = json.loads(next(tmp_path.rglob("*.json")).read_text())
    assert stored["result"]["backend"] == "openclaw" and "backend" not in stored["request"]


def test_replay_of_legacy_entries_without_backend_field(tmp_path):
    cache = ReplayCache(MockLLMClient("unused"), model="m", mode="record", cache_dir=tmp_path)
    cache.complete("probe", "s", "u")
    path = next(tmp_path.rglob("*.json"))
    entry = json.loads(path.read_text())
    del entry["result"]["backend"]  # entries recorded before T10b
    path.write_text(json.dumps(entry))
    replayed = ReplayCache(MockLLMClient("unused"), model="m", mode="replay_only", cache_dir=tmp_path).complete("probe", "s", "u")
    assert replayed.backend == "replay" and replayed.cached


def test_result_backend_defaults():
    assert LLMResult(text="x").backend == "gateway"
    assert MockLLMClient("x").complete("probe", "s", "u").backend == "mock"


# --- factory -------------------------------------------------------------------------------------


def test_factory_openclaw_backend_wires_fallback_inside_the_cache():
    client = build_llm_client(settings())
    assert isinstance(client, ReplayCache) and client.model == "test-model"
    assert isinstance(client.inner, FallbackLLMClient)
    assert isinstance(client.inner.primary, OpenClawLLMClient)
    assert isinstance(client.inner.fallback, GatewayLLMClient)


def test_factory_gateway_backend_is_unchanged():
    client = build_llm_client(settings(LLM_BACKEND="gateway"))
    assert isinstance(client, ReplayCache) and isinstance(client.inner, GatewayLLMClient)


# --- health --------------------------------------------------------------------------------------


def test_probe_reports_unreachable_without_raising(monkeypatch):
    def boom(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    assert probe_openclaw(settings()) == "unreachable"

    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(200, json={"ok": True}))
    assert probe_openclaw(settings()) == "reachable"
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(503))
    assert probe_openclaw(settings()) == "unreachable"


def test_health_degrades_gracefully_when_openclaw_is_unreachable(monkeypatch):
    import procureai.api.app as app_module

    monkeypatch.setattr(app_module, "get_settings", lambda: settings(MODE="mock"))
    monkeypatch.setattr(app_module, "probe_openclaw", lambda s: "unreachable")
    with TestClient(app_module.app) as client:
        body = client.get("/health").json()
    assert body["llm_backend"] == "openclaw" and body["openclaw"] == "unreachable"


def test_health_skips_the_probe_when_openclaw_is_not_the_route(monkeypatch):
    import procureai.api.app as app_module

    monkeypatch.setattr(app_module, "get_settings", lambda: settings(MODE="mock", LLM_BACKEND="gateway"))
    monkeypatch.setattr(app_module, "probe_openclaw", lambda s: pytest.fail("must not probe"))
    with TestClient(app_module.app) as client:
        body = client.get("/health").json()
    assert body["llm_backend"] == "gateway" and body["openclaw"] == "configured"
