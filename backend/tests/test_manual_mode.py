"""Fail-safe manual mode (T17, PLAN.md G6): /health llm status, LLM-down extraction → manual form, a whole quote
in one patch → RECOMMENDED on templated text, and the comparison matrix. No network: stub clients only."""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import procureai.api.app as app_module
from procureai.agents.base import CRITICAL_FIELDS
from procureai.agents.decision import LiveDecisionAgent
from procureai.agents.document import LiveDocumentAgent
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockSupplierIntelAgent
from procureai.api.app import HealthProbes
from procureai.config.settings import Settings
from procureai.domain.models import NormalizedQuote, ProcurementConfig, ProcurementRequest, WorkflowState as S
from procureai.llm.base import LLMUnavailable
from procureai.llm.gateway import GatewayLLMClient, probe_gateway
from procureai.llm.openclaw import OpenClawLLMClient
from procureai.llm.status import RouteTracker, llm_status
from procureai.workflow import Orchestrator, RunStore
from procureai.workflow.orchestrator import MANUAL_FIELDS, MANUAL_TEXT_CHARS

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND.parent / "data" / "fixtures"
SYNTHETIC = BACKEND.parent / "data" / "synthetic"
sys.path.insert(0, str(BACKEND / "scripts"))
from extract_live import DOCS, as_document  # noqa: E402

# Identity/audit fields the system owns; everything else is what a person types into the manual form.
SYSTEM_FIELDS = {"doc_id", "source", "field_confidence", "raw_excerpt", "negotiated_offer"}


class DownLLM:
    """Every route refused: what FallbackLLMClient raises once OpenClaw and the gateway both failed."""

    def complete(self, *args, **kwargs):
        raise LLMUnavailable("openclaw transport error: ConnectError: connection refused")


@pytest.fixture
def request_() -> ProcurementRequest:
    return ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())


@pytest.fixture
def config() -> ProcurementConfig:
    return ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())


def manual_orchestrator() -> Orchestrator:
    llm = DownLLM()
    settings = Settings(MODE="live", LLM_GATEWAY_URL="https://gateway.invalid", LLM_GATEWAY_API_KEY="k")
    agents = {"document": LiveDocumentAgent(llm), "supplier_intel": MockSupplierIntelAgent(),
              "decision": LiveDecisionAgent(llm, settings)}
    return Orchestrator(agents, RunStore(), now=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc))


def expected_patch(name: str) -> dict:
    """The ground truth as a person would type it: every fillable field, supplier_id left for the alias table."""
    raw = json.loads((SYNTHETIC / f"{name}.expected.json").read_text())
    return {k: v for k, v in raw.items() if k not in SYSTEM_FIELDS and k != "supplier_id"}


# --- /health llm status ---------------------------------------------------------------------------


def live(**overrides) -> Settings:
    base = dict(MODE="live", LLM_BACKEND="openclaw", LLM_GATEWAY_URL="https://gateway.invalid", LLM_GATEWAY_API_KEY="k",
                OPENCLAW_URL="http://openclaw.invalid:18789", OPENCLAW_TOKEN="t", LLM_CACHE_MODE="replay_only")
    return Settings(**{**base, **overrides})


def test_llm_status_rules():
    t = RouteTracker()
    assert llm_status(Settings(MODE="mock"), t, openclaw=None, gateway=None) == "ok"
    s = live()
    assert llm_status(s, t, openclaw="reachable", gateway="reachable") == "ok"
    assert llm_status(s, t, openclaw="unreachable", gateway="reachable") == "degraded"
    assert llm_status(s, t, openclaw="unreachable", gateway="unreachable") == "down"
    t.record("openclaw", False, "503")
    assert llm_status(s, t, openclaw="reachable", gateway="reachable") == "degraded", "last real call beats the probe"
    t.record("gateway", False, "429")
    assert llm_status(s, t, openclaw="reachable", gateway="reachable") == "down"
    t.record("gateway", True)
    assert llm_status(s, t, openclaw="reachable", gateway="reachable") == "degraded"
    t.record("openclaw", True)
    assert llm_status(s, t, openclaw="reachable", gateway="unreachable") == "ok", "the primary answers; the fallback is not needed"
    # direct gateway route: no fallback, so it is either ok or down
    g = live(LLM_BACKEND="gateway")
    assert llm_status(g, t, openclaw=None, gateway="reachable") == "ok"
    assert llm_status(g, t, openclaw=None, gateway="unreachable") == "down"
    t.record("gateway", False, "refused")
    assert llm_status(g, t, openclaw=None, gateway="reachable") == "down"


def test_health_llm_transitions_with_stubbed_clients(monkeypatch):
    """ok → degraded (OpenClaw refused) → down (gateway refused too) → degraded → ok, from the clients' own bookkeeping."""
    tracker = RouteTracker()
    s = live()
    probes = {"openclaw": "reachable", "gateway": "reachable"}
    monkeypatch.setattr(app_module, "get_settings", lambda: s)
    monkeypatch.setattr(app_module, "probe_openclaw", lambda settings: probes["openclaw"])
    monkeypatch.setattr(app_module, "probe_gateway", lambda settings: probes["gateway"])

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def answers(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "{}"}})

    def openclaw(handler) -> OpenClawLLMClient:
        return OpenClawLLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler), base_url=s.OPENCLAW_URL), tracker=tracker)

    def gateway(handler) -> GatewayLLMClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url=s.LLM_GATEWAY_URL)
        return GatewayLLMClient(s, client=http, sleep=lambda _: None, tracker=tracker)

    with TestClient(app_module.app) as client:
        client.app.state.health_probes = HealthProbes(tracker=tracker)
        body = client.get("/health").json()
        assert body["llm"] == "ok" and body["gateway"] == "reachable" and body["llm_routes"] == {}

        with pytest.raises(LLMUnavailable):
            openclaw(refused).complete("extract", "s", "u")
        body = client.get("/health").json()
        assert body["llm"] == "degraded"
        assert body["llm_routes"]["openclaw"]["ok"] is False and "refused" in body["llm_routes"]["openclaw"]["error"]
        assert datetime.fromisoformat(body["llm_routes"]["openclaw"]["at"]).tzinfo is not None

        with pytest.raises(LLMUnavailable):
            gateway(refused).complete("extract", "s", "u")
        assert client.get("/health").json()["llm"] == "down"

        gateway(answers).complete("extract", "s", "u")
        body = client.get("/health").json()
        assert body["llm"] == "degraded" and body["llm_routes"]["gateway"] == {"ok": True, "at": body["llm_routes"]["gateway"]["at"], "error": None}

        openclaw(answers).complete("extract", "s", "u")
        assert client.get("/health").json()["llm"] == "ok"

        # the probes are cached for 10 s: a change shows only after the cache expires (invalidate stands in for time)
        probes["openclaw"] = probes["gateway"] = "unreachable"
        assert client.get("/health").json()["llm"] == "ok"
        client.app.state.health_probes.invalidate()
        body = client.get("/health").json()
        assert body["llm"] == "down" and body["openclaw"] == "unreachable" and body["gateway"] == "unreachable"


def test_health_stays_ok_in_mock_mode_whatever_the_routes_say(monkeypatch):
    tracker = RouteTracker()
    tracker.record("gateway", False, "refused")
    tracker.record("openclaw", False, "refused")
    monkeypatch.setattr(app_module, "get_settings", lambda: Settings(MODE="mock"))
    with TestClient(app_module.app) as client:
        client.app.state.health_probes = HealthProbes(tracker=tracker)
        body = client.get("/health").json()
    assert body["llm"] == "ok" and body["gateway"] == "unconfigured"


def test_gateway_probe_never_raises(monkeypatch):
    s = live()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", get)
    assert probe_gateway(s) == "unreachable"
    assert calls[0][0] == "https://gateway.invalid/api/tags" and calls[0][1]["timeout"] == 2.0
    assert calls[0][1]["headers"]["Authorization"] == "Bearer k"
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(200, json={"models": []}))
    assert probe_gateway(s) == "reachable"
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(403))
    assert probe_gateway(s) == "unreachable"


# --- LLM down → manual extraction form -------------------------------------------------------------


def test_llm_unavailable_routes_every_document_to_the_manual_form(request_, config):
    orch = manual_orchestrator()
    docs = [as_document(SYNTHETIC / name) for name in DOCS]
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, docs)

    assert run.state is S.NEEDS_HUMAN_EXTRACTION
    pending = run.pending_human
    assert pending is not None and pending.kind == "extraction"
    assert pending.details["reason"] == "llm_unavailable"
    assert pending.message.startswith("AI unavailable: enter 3 quote(s) by hand")
    assert sorted(pending.quote_ids) == sorted(q.quote_id for q in run.quotes)
    assert len(pending.quote_ids) == 3 and not pending.details["failed_documents"]

    manual = pending.details["manual"]
    assert set(manual) == {d.doc_id for d in docs}
    for doc in docs:
        info = manual[doc.doc_id]
        quote = next(q for q in run.quotes if q.doc_id == doc.doc_id)
        assert info["reason"] == "llm_unavailable" and info["filename"] == doc.filename and info["quote_id"] == quote.quote_id
        assert info["text_excerpt"] == doc.text[:MANUAL_TEXT_CHARS] and len(info["text_excerpt"]) <= MANUAL_TEXT_CHARS
        assert "connection refused" in info["detail"]
        # every fillable field, critical first; nothing the system owns
        fields = pending.details["fields"][quote.quote_id]
        assert fields == list(MANUAL_FIELDS) and tuple(fields[: len(CRITICAL_FIELDS)]) == CRITICAL_FIELDS
        assert set(fields).isdisjoint(SYSTEM_FIELDS) and set(fields) <= set(NormalizedQuote.model_fields)
        assert all(quote.field_confidence[f] == 0.0 for f in CRITICAL_FIELDS)

    events = orch.store.events(run.run_id)
    failed = [e for e in events if e.type == "agent.failed"]
    assert len(failed) == 3
    assert all(e.payload["agent"] == "document" and e.payload["reason"] == "llm_unavailable" for e in failed)
    assert all(e.summary == "LLM gateway unavailable; routing the document to human extraction" for e in failed)
    finished = [e for e in events if e.type == "agent.finished" and e.payload["agent"] == "document"]
    assert all(e.payload["fallback"] is True for e in finished)


def test_full_manual_patch_reaches_recommended_with_templated_rationale(request_, config):
    """The whole demo with no LLM: three hand-typed quotes → the same engine numbers as mock mode, a templated
    rationale, and agent.failed (llm_unavailable) on the decision lane."""
    orch = manual_orchestrator()
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [as_document(SYNTHETIC / name) for name in DOCS])
    by_doc = {q.doc_id: q.quote_id for q in run.quotes}

    for name in DOCS:
        quote_id = by_doc[f"doc_{Path(name).stem}"]
        run = orch.correct_quote(run.run_id, quote_id, expected_patch(name))
    # Apex's printed total is wrong: the human-typed stated total still trips the math check (G1), exactly as live
    assert run.state is S.CALC_MISMATCH and run.pending_human.quote_ids == ["APX-Q-26091"]
    run = orch.confirm_quote_math(run.run_id, "APX-Q-26091", use_computed=True)

    assert run.state is S.RECOMMENDED
    assert run.recommendation.recommended_supplier_id == "sup_b"
    assert run.recommendation.ranked == ["sup_b", "sup_c", "sup_a"]
    assert [q.supplier_id for q in sorted(run.quotes, key=lambda q: q.supplier_id)] == ["sup_a", "sup_b", "sup_c"], "supplier_id derived from the typed name"
    assert all(q.field_confidence[f] == 1.0 for q in run.quotes for f in MANUAL_FIELDS if f in q.field_confidence)
    assert all(q.field_confidence[f] == 1.0 for q in run.quotes for f in CRITICAL_FIELDS)

    # identical numbers to the normal (mock-agent) path
    mock = Orchestrator({"document": MockDocumentAgent(), "supplier_intel": MockSupplierIntelAgent(), "decision": MockDecisionAgent()},
                        RunStore(), now=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc))
    ref = mock.create_run(request_, config)
    ref = mock.add_documents(ref.run_id, [as_document(SYNTHETIC / name) for name in DOCS])
    ref = mock.run_evaluation(ref.run_id)
    ref = mock.confirm_quote_math(ref.run_id, "APX-Q-26091", use_computed=True)
    assert [c.model_dump() for c in run.scorecards] == [c.model_dump() for c in ref.scorecards]
    assert run.recommendation.rationale == ref.recommendation.rationale, "templated rationale, no LLM prose"

    events = orch.store.events(run.run_id)
    decision_failed = [e for e in events if e.type == "agent.failed" and e.payload["agent"] == "decision"]
    assert len(decision_failed) == 1 and decision_failed[0].payload["reason"] == "llm_unavailable"
    assert decision_failed[0].summary == "LLM gateway unavailable; using deterministic text"
    corrected = [e for e in events if e.type == "quote.corrected"]
    assert len(corrected) == 3 and all(set(e.payload["patch"]) >= set(CRITICAL_FIELDS) for e in corrected)


def test_partial_manual_patch_keeps_the_quote_on_the_form(request_, config):
    orch = manual_orchestrator()
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [as_document(SYNTHETIC / DOCS[1])])
    quote_id = run.quotes[0].quote_id
    run = orch.correct_quote(run.run_id, quote_id, {"unit_price": "12.80", "currency": "USD"})
    assert run.state is S.NEEDS_HUMAN_EXTRACTION
    assert run.pending_human.details["reason"] == "llm_unavailable"
    assert run.pending_human.details["fields"][quote_id] == list(MANUAL_FIELDS)
    q = run.quotes[0]
    assert q.field_confidence["unit_price"] == 1.0 and q.field_confidence["moq"] == 0.0
    # renaming the quote (the human types the document's reference) keeps the manual details attached
    run = orch.correct_quote(run.run_id, quote_id, {"quote_id": "BOR-2026-0418"})
    assert run.pending_human.quote_ids == ["BOR-2026-0418"]
    assert run.pending_human.details["manual"][run.quotes[0].doc_id]["quote_id"] == "BOR-2026-0418"


def test_low_confidence_gate_carries_no_manual_reason(request_, config):
    class Shaky(MockDocumentAgent):
        def extract(self, doc):
            q = super().extract(doc)
            return q.model_copy(update={"field_confidence": {**q.field_confidence, "unit_price": 0.5}})

    orch = Orchestrator({"document": Shaky(), "supplier_intel": MockSupplierIntelAgent(), "decision": MockDecisionAgent()}, RunStore())
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [as_document(SYNTHETIC / DOCS[1])])
    assert run.state is S.NEEDS_HUMAN_EXTRACTION
    assert "reason" not in run.pending_human.details and run.pending_human.details["manual"] == {}
    assert run.pending_human.details["fields"] == {"BOR-2026-0418": ["unit_price"]}
    assert run.pending_human.message == "Confirm low-confidence critical fields"


# --- comparison matrix ----------------------------------------------------------------------------

ROW_KEYS = {
    "supplier_id", "supplier_name", "quote_id", "unit_price", "currency", "quantity_quoted", "moq", "lead_time_days",
    "shipping_cost", "discount_pct", "payment_terms", "capacity_units", "subtotal", "discount", "pre_tax_total", "tax",
    "landed_cost", "checks", "issues", "negotiated_offer", "negotiated", "eligible", "ineligibility_reasons",
    "total_score", "score_breakdown", "on_time_rate", "defect_rate",
}
REQUEST = {"product": "Product X", "quantity": 2000, "required_by": (date.today() + timedelta(days=14)).isoformat(),
           "budget": "30000.00", "currency": "USD"}


def test_comparison_endpoint_shape_and_409_before_validation():
    with TestClient(app_module.app) as client:
        run_id = client.post("/runs", json={"request": REQUEST}).json()["run_id"]
        r = client.get(f"/runs/{run_id}/comparison")
        assert r.status_code == 409 and r.json()["code"] == "not_validated"

        files = [("files", (n, (SYNTHETIC / n).read_bytes())) for n in DOCS]
        assert client.post(f"/runs/{run_id}/documents", files=files).json()["state"] == "EXTRACTED"
        assert client.get(f"/runs/{run_id}/comparison").status_code == 409, "extracted but not validated"

        assert client.post(f"/runs/{run_id}/evaluate").json()["state"] == "CALC_MISMATCH"
        r = client.get(f"/runs/{run_id}/comparison")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "CALC_MISMATCH" and body["recommended_supplier_id"] is None
        assert body["quantity"] == 2000 and body["budget"] == "30000.00" and body["weights"]["price"] == 0.3
        assert len(body["quotes"]) == 3 and all(set(q) == ROW_KEYS for q in body["quotes"])
        assert all(q["eligible"] is None and q["total_score"] is None and q["score_breakdown"] is None for q in body["quotes"])
        apex = next(q for q in body["quotes"] if q["supplier_id"] == "sup_a")
        assert apex["checks"]["math_ok"] is False and apex["pre_tax_total"] == "22800.00"  # 2000 × 11.20 + 400 shipping
        assert any("22040" in issue for issue in apex["issues"])

        client.post(f"/runs/{run_id}/quotes/APX-Q-26091/confirm-math", json={"use_computed": True})
        body = client.get(f"/runs/{run_id}/comparison").json()
        assert body["state"] == "RECOMMENDED" and body["recommended_supplier_id"] == "sup_b"
        assert [q["supplier_id"] for q in body["quotes"]] == ["sup_b", "sup_c", "sup_a"], "ranked order"
        run = client.get(f"/runs/{run_id}").json()
        for q, card in zip(body["quotes"], run["scorecards"]):
            v = next(v for v in run["validated"] if v["supplier_id"] == q["supplier_id"])
            assert q["landed_cost"] == card["landed_cost"] == v["landed_cost"]
            assert q["total_score"] == card["total_score"] and q["score_breakdown"] == card["score_breakdown"]
            assert q["eligible"] == card["eligible"] and q["subtotal"] == v["subtotal"] and q["tax"] == v["tax"]
            assert q["checks"] == v["checks"] and q["issues"] == v["issues"]
            assert 0 <= q["on_time_rate"] <= 1 and 0 <= q["defect_rate"] <= 1
        borealis = body["quotes"][0]
        assert borealis["landed_cost"] == "27618.42" and borealis["negotiated"] is False and borealis["negotiated_offer"] is None
        assert Decimal(borealis["discount_pct"]) == Decimal("2")
        assert borealis["payment_terms"] == "Net 45" and borealis["capacity_units"] == 4000
