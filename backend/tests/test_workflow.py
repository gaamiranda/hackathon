"""Week 1 pipeline in mock mode: no network, no LLM."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from procureai.agents.factory import build_agents
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockSupplierIntelAgent
from procureai.config.settings import Settings
from procureai.domain.models import (
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    QuoteSource,
    RawDocument,
    WorkflowState as S,
)
from procureai.workflow import Orchestrator, RunStore, WorkflowError

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND.parent / "data" / "fixtures"
sys.path.insert(0, str(BACKEND / "scripts"))
from print_event_log import format_event, run_mismatch_demo  # noqa: E402

DOC_A = RawDocument(doc_id="doc-a", filename="supplier_a_apex.pdf", source=QuoteSource.PDF, text="...")
DOC_B = RawDocument(doc_id="doc-b", filename="supplier_b_borealis.xlsx", source=QuoteSource.XLSX, text="...")
DOC_C = RawDocument(doc_id="doc-c", filename="supplier_c_cobalt.eml.txt", source=QuoteSource.EMAIL, text="...")


@pytest.fixture
def request_() -> ProcurementRequest:
    return ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())


@pytest.fixture
def config() -> ProcurementConfig:
    return ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())


@pytest.fixture
def orch() -> Orchestrator:
    return Orchestrator(build_agents(Settings(MODE="mock")), RunStore())


def types(orch: Orchestrator, run_id: str) -> list[str]:
    return [e.type for e in orch.store.events(run_id)]


def test_happy_path_b_and_c(orch, request_, config):
    run = orch.create_run(request_, config)
    assert run.state == S.CREATED
    run = orch.add_documents(run.run_id, [DOC_B, DOC_C])
    assert run.state == S.EXTRACTED and run.pending_human is None
    run = orch.run_evaluation(run.run_id)

    assert run.state == S.RECOMMENDED
    assert run.recommendation.recommended_supplier_id == "sup_b"
    assert run.recommendation.ranked == ["sup_b", "sup_c"]
    assert run.recommendation.rationale
    assert "Borealis" in run.recommendation.rationale

    events = orch.store.events(run.run_id)
    seqs = [e.seq for e in events]
    assert seqs == list(range(len(events)))
    agents = [e.payload.get("agent") for e in events if e.type in ("agent.started", "agent.finished")]
    assert agents.count("document") == 4 and agents.count("decision") == 2 and agents.count("supplier_intel") == 4
    assert [e.state_after for e in events if e.type == "recommendation.ranked"] == [S.RECOMMENDED]


def test_mismatch_path(orch, request_, config):
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, [DOC_A, DOC_B, DOC_C])
    run = orch.run_evaluation(run.run_id)

    assert run.state == S.CALC_MISMATCH
    assert run.pending_human.quote_ids == ["APX-Q-26091"]
    detail = run.pending_human.details["APX-Q-26091"]
    assert detail["computed_pre_tax_total"] == "22800.00" and detail["stated_total"] == "22040.00"
    assert run.scorecards == []

    with pytest.raises(WorkflowError, match="not allowed"):
        orch.run_evaluation(run.run_id)

    run = orch.confirm_quote_math(run.run_id, "APX-Q-26091", use_computed=True)
    assert run.state == S.RECOMMENDED
    assert run.recommendation.ranked[0] == "sup_b"
    assert {c.supplier_id for c in run.scorecards} == {"sup_a", "sup_b", "sup_c"}
    assert all(c.eligible for c in run.scorecards)

    audit = next(e for e in orch.store.events(run.run_id) if e.type == "quote.math_confirmed")
    assert audit.actor == "human"
    assert audit.payload["original_stated_total"] == "22040.00"
    assert "22040.00" in audit.summary


def test_mismatch_reject_quote(orch, request_, config):
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, [DOC_A, DOC_B])
    orch.run_evaluation(run.run_id)
    run = orch.confirm_quote_math(run.run_id, "APX-Q-26091", use_computed=False)
    assert run.state == S.RECOMMENDED
    assert [q.supplier_id for q in run.quotes] == ["sup_b"]
    assert "quote.rejected" in types(orch, run.run_id)


def test_low_confidence_path(request_, config):
    class ShakyDocumentAgent(MockDocumentAgent):
        def extract(self, doc: RawDocument) -> NormalizedQuote:
            quote = super().extract(doc)
            if doc.doc_id == "doc-c":
                return quote.model_copy(update={"field_confidence": {**quote.field_confidence, "unit_price": 0.5}})
            return quote

    orch = Orchestrator(
        {"document": ShakyDocumentAgent(), "supplier_intel": MockSupplierIntelAgent(), "decision": MockDecisionAgent()},
        RunStore(),
    )
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [DOC_B, DOC_C])
    assert run.state == S.NEEDS_HUMAN_EXTRACTION
    assert run.pending_human.kind == "extraction"
    assert run.pending_human.quote_ids == ["CI-Q-7731"]
    assert run.pending_human.details["fields"] == {"CI-Q-7731": ["unit_price"]}

    with pytest.raises(WorkflowError, match="not allowed"):
        orch.run_evaluation(run.run_id)

    run = orch.correct_quote(run.run_id, "CI-Q-7731", {"unit_price": "13.40"})
    assert run.state == S.RECOMMENDED
    fixed = next(q for q in run.quotes if q.quote_id == "CI-Q-7731")
    assert fixed.field_confidence["unit_price"] == 1.0
    assert run.recommendation.recommended_supplier_id == "sup_b"
    t = types(orch, run.run_id)
    assert t.index("extraction.needs_human") < t.index("quote.corrected") < t.index("recommendation.ready")


def test_blacklisted_supplier_is_ineligible(orch, request_, config):
    class DeltaDocumentAgent(MockDocumentAgent):
        def extract(self, doc: RawDocument) -> NormalizedQuote:
            quote = super().extract(doc)
            if doc.doc_id == "doc-d":
                return quote.model_copy(update={"quote_id": "DLT-1", "supplier_id": "sup_d", "supplier_name": "Delta Trading"})
            return quote

    orch.agents["document"] = DeltaDocumentAgent()
    doc_d = DOC_C.model_copy(update={"doc_id": "doc-d"})
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, [DOC_B, doc_d])
    run = orch.run_evaluation(run.run_id)

    assert run.state == S.RECOMMENDED
    delta = next(c for c in run.scorecards if c.supplier_id == "sup_d")
    assert not delta.eligible and any("blacklisted" in r for r in delta.ineligibility_reasons)
    assert run.recommendation.recommended_supplier_id == "sup_b"
    assert "Delta Trading is ineligible" in run.recommendation.rationale


def test_illegal_transitions_and_unknown_run(orch, request_, config):
    run = orch.create_run(request_, config)
    with pytest.raises(WorkflowError) as exc:
        orch.run_evaluation(run.run_id)
    assert exc.value.code == "illegal_transition"
    with pytest.raises(WorkflowError) as exc:
        orch.run_evaluation("run-nope")
    assert exc.value.code == "run_not_found"
    with pytest.raises(WorkflowError) as exc:
        orch.confirm_quote_math(run.run_id, "x", True)
    assert exc.value.code == "illegal_transition"


def test_subscriber_receives_every_event(orch, request_, config):
    received = []
    orch.events.subscribe(received.append)  # all runs
    run = orch.create_run(request_, config)
    per_run = []
    unsubscribe = orch.events.subscribe(per_run.append, run.run_id)
    orch.add_documents(run.run_id, [DOC_B, DOC_C])
    orch.run_evaluation(run.run_id)

    stored = orch.store.events(run.run_id)
    assert received == stored
    assert per_run == stored[1:]  # subscribed after run.created
    unsubscribe()


def test_unknown_document_fails_safe(orch, request_, config):
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [DOC_B, RawDocument(doc_id="doc-x", filename="mystery.pdf", source="pdf", text="")])
    assert run.state == S.NEEDS_HUMAN_EXTRACTION
    assert "doc-x" in run.pending_human.details["failed_documents"]
    assert "agent.failed" in types(orch, run.run_id)


def test_event_log_script_prints_one_line_per_event(orch, capsys):
    run_id = run_mismatch_demo(orch)
    events = orch.store.events(run_id)
    for e in events:
        print(format_event(e))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(events)
    assert "CALC_MISMATCH" in "\n".join(lines) and lines[-1].endswith("Recommendation stored for sup_b")


def test_no_network_or_llm_imports():
    src = "".join(p.read_text() for p in (BACKEND / "procureai" / "workflow").glob("*.py"))
    src += (BACKEND / "procureai" / "agents" / "mock.py").read_text()
    for forbidden in ("httpx", "requests", "fastapi", "openai", "anthropic", "urllib"):
        assert forbidden not in src
