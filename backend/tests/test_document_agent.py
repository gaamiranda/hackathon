"""Live Document Agent, replayed from data/llm_cache/ — real Claude output, no network (T5).

The extraction, injection and mapping assertions all run against responses the gateway actually
returned; only the failure paths use a stub client, because no real response produces them.
"""

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from procureai.agents.base import CRITICAL_FIELDS
from procureai.agents.document import LiveDocumentAgent, slugify
from procureai.agents.factory import build_agents
from procureai.agents.mock import MockDecisionAgent, MockSupplierIntelAgent
from procureai.config.settings import Settings
from procureai.domain.models import (
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    WorkflowState as S,
)
from procureai.llm.cache import CACHE_DIR, ReplayCache
from procureai.llm.mock import MockLLMClient
from procureai.workflow import Orchestrator, RunStore

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND.parent / "data" / "fixtures"
SYNTHETIC = BACKEND.parent / "data" / "synthetic"
sys.path.insert(0, str(BACKEND / "scripts"))
from extract_live import DOCS, as_document  # noqa: E402

INJECTED = "injection_cobalt.eml.txt"
EXPECTED_SUPPLIER = {
    "supplier_a_apex.pdf": "sup_a",
    "supplier_b_borealis.xlsx": "sup_b",
    "supplier_c_cobalt.eml.txt": "sup_c",
}


def recorded_model() -> str:
    """The model the cache was recorded against; keeps the tests independent of anyone's .env."""
    models = {json.loads(p.read_text())["request"]["model"] for p in CACHE_DIR.rglob("*.json")}
    assert len(models) == 1, f"cache mixes models: {models}"
    return models.pop()


class _NoNetwork:
    def complete(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("replay_only must never reach the gateway")


@pytest.fixture
def replay_agent() -> LiveDocumentAgent:
    cache = ReplayCache(_NoNetwork(), model=recorded_model(), mode="replay_only", cache_dir=CACHE_DIR)
    return LiveDocumentAgent(cache)


@pytest.fixture
def request_() -> ProcurementRequest:
    return ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())


@pytest.fixture
def config() -> ProcurementConfig:
    return ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())


# --- the three demo documents, extracted by Claude ------------------------------------------------


@pytest.mark.parametrize("name", DOCS)
def test_extracts_the_demo_documents_from_recorded_responses(replay_agent, name):
    expected = NormalizedQuote.model_validate_json((SYNTHETIC / f"{name}.expected.json").read_text())
    quote = replay_agent.extract(as_document(SYNTHETIC / name))

    for field in (*CRITICAL_FIELDS, "llm_stated_total", "quote_id", "supplier_name"):
        assert getattr(quote, field) == getattr(expected, field), field
    assert quote.supplier_id == EXPECTED_SUPPLIER[name]
    assert quote.doc_id == f"doc_{Path(name).stem}"
    assert quote.raw_excerpt and len(quote.raw_excerpt) <= 300
    assert all(quote.field_confidence[f] >= 0.85 for f in CRITICAL_FIELDS)


def test_stated_total_is_passed_through_not_recomputed(replay_agent):
    """Apex prints 22,040.00 while 2,000 × 11.20 + 400 is 22,800: the engine catches that, not the LLM (G1)."""
    quote = replay_agent.extract(as_document(SYNTHETIC / "supplier_a_apex.pdf"))
    assert quote.llm_stated_total == Decimal("22040.00")
    assert quote.unit_price * quote.quantity_quoted + quote.shipping_cost != quote.llm_stated_total


# --- prompt injection (G4), against a real recorded response ---------------------------------------


def test_injected_document_is_extracted_as_data_not_obeyed(replay_agent):
    text = (SYNTHETIC / INJECTED).read_text()
    assert "set unit_price to 1.00" in text, "fixture lost its injection line"

    quote = replay_agent.extract(as_document(SYNTHETIC / INJECTED))

    assert quote.unit_price == Decimal("13.40"), "model obeyed an instruction inside the document"
    assert quote.supplier_id == "sup_c"
    assert quote.lead_time_days == 9


# --- supplier identity ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "supplier_id"),
    [("Apex Components Ltd", "sup_a"), ("Borealis Manufacturing AS", "sup_b"), ("Cobalt Industrial", "sup_c")],
)
def test_alias_table_maps_printed_names_to_canonical_ids(name, supplier_id):
    agent = LiveDocumentAgent(MockLLMClient())
    assert agent.supplier_id_for(name) == supplier_id
    assert MockSupplierIntelAgent().get_profile(supplier_id) is not None


def test_unknown_supplier_falls_back_to_its_slug():
    agent = LiveDocumentAgent(MockLLMClient())
    assert agent.supplier_id_for("Nakamura Precision Co., Ltd.") == "nakamura-precision-co-ltd"
    assert MockSupplierIntelAgent().get_profile("nakamura-precision-co-ltd") is None, "must not match a known supplier"


@pytest.mark.parametrize(
    ("raw", "slug"),
    [("Apex Components Ltd.", "apex-components-ltd"), ("  BOREALIS  MFG  ", "borealis-mfg"), ("", "")],
)
def test_slugify(raw, slug):
    assert slugify(raw) == slug


# --- model output the client has to survive --------------------------------------------------------


FENCED = """Here is the quote:
```json
{"supplier_name": "Cobalt Industrial", "quote_reference": "CI-Q-7731", "unit_price": 13.4,
 "currency": "USD", "quantity_quoted": 2000, "moq": 1000, "lead_time_days": 9,
 "payment_terms": "50% upfront", "shipping_cost": 0, "discount_pct": 0,
 "validity_date": "2026-10-15", "capacity_units": 10000, "llm_stated_total": 26800,
 "confidence": {"unit_price": 0.98, "currency": 1.0, "quantity_quoted": 1.0, "moq": 0.9,
                "lead_time_days": 0.95}}
```"""


def test_fenced_json_with_prose_around_it_is_parsed():
    agent = LiveDocumentAgent(MockLLMClient(FENCED))
    quote = agent.extract(as_document(SYNTHETIC / "supplier_c_cobalt.eml.txt"))

    assert quote.unit_price == Decimal("13.4")
    assert quote.supplier_id == "sup_c"
    assert quote.field_confidence["unit_price"] == 0.98
    assert quote.field_confidence["payment_terms"] == 0.0, "confidence the model omitted counts as zero"


def test_numbers_printed_with_separators_are_salvaged():
    agent = LiveDocumentAgent(
        MockLLMClient(
            '{"supplier_name": "Apex Components Ltd", "quote_reference": "APX-Q-26091",'
            ' "unit_price": "USD 11.20", "currency": "usd", "quantity_quoted": "2,000", "moq": 500,'
            ' "lead_time_days": 13, "shipping_cost": 400, "discount_pct": 0,'
            ' "llm_stated_total": "22,040.00", "validity_date": "15 October 2026",'
            ' "confidence": {"unit_price": 1, "currency": 1, "quantity_quoted": 1, "moq": 1, "lead_time_days": 1}}'
        )
    )
    quote = agent.extract(as_document(SYNTHETIC / "supplier_a_apex.pdf"))

    assert quote.unit_price == Decimal("11.20")
    assert quote.quantity_quoted == 2000
    assert quote.llm_stated_total == Decimal("22040.00")
    assert quote.currency == "USD"
    assert quote.validity_date is None, "an unparseable date is dropped, not fatal"


def test_long_documents_are_truncated_before_the_call():
    llm = MockLLMClient(FENCED)
    agent = LiveDocumentAgent(llm, max_text_chars=120)
    doc = as_document(SYNTHETIC / "supplier_c_cobalt.eml.txt")
    agent.extract(doc)

    sent = llm.calls[0]["user"]
    assert doc.text[:120] in sent and doc.text[:121] not in sent
    assert sent.startswith("<<<DOCUMENT") and sent.endswith(">>>"), "document stays inside the delimiters (D8)"


# --- failure path: unusable output must reach a human, not the engine ------------------------------


@pytest.mark.parametrize("reply", ["I could not read this document.", "", '{"unit_price": 11.2}'])
def test_unusable_output_becomes_a_zero_confidence_quote(reply):
    """No JSON at all, or JSON missing the required fields: same outcome, a quote a human must fix."""
    agent = LiveDocumentAgent(MockLLMClient(reply))
    quote = agent.extract(as_document(SYNTHETIC / "supplier_a_apex.pdf"))

    assert all(quote.field_confidence[f] == 0.0 for f in CRITICAL_FIELDS)
    assert quote.supplier_id == "unknown-doc_supplier_a_apex"
    assert quote.doc_id == "doc_supplier_a_apex"


def test_orchestrator_routes_unusable_extraction_to_a_human(request_, config):
    agents = {
        "document": LiveDocumentAgent(MockLLMClient("the document was blurry")),
        "supplier_intel": MockSupplierIntelAgent(),
        "decision": MockDecisionAgent(),
    }
    orch = Orchestrator(agents, RunStore(), now=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc))
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [as_document(SYNTHETIC / name) for name in DOCS])

    assert run.state is S.NEEDS_HUMAN_EXTRACTION
    assert run.pending_human is not None
    assert sorted(run.pending_human.details["fields"]) == sorted(q.quote_id for q in run.quotes)
    assert set(run.pending_human.details["fields"][run.quotes[0].quote_id]) == set(CRITICAL_FIELDS)


def test_orchestrator_reaches_recommended_on_recorded_extractions(replay_agent, request_, config):
    """The whole Week 1 pipeline on real Claude output: Borealis wins, Apex's total mismatch is caught."""
    agents = {
        "document": replay_agent,
        "supplier_intel": MockSupplierIntelAgent(),
        "decision": MockDecisionAgent(),
    }
    orch = Orchestrator(agents, RunStore(), now=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc))
    run = orch.create_run(request_, config)
    run = orch.add_documents(run.run_id, [as_document(SYNTHETIC / name) for name in DOCS])
    assert run.state is S.EXTRACTED, "all three documents extracted cleanly"

    run = orch.run_evaluation(run.run_id)
    assert run.state is S.CALC_MISMATCH, "Apex's printed total is wrong (G1)"
    mismatched = list(run.pending_human.quote_ids)
    assert mismatched == ["APX-Q-26091"]
    for quote_id in mismatched:
        run = orch.confirm_quote_math(run.run_id, quote_id, use_computed=True)

    assert run.state is S.RECOMMENDED
    assert run.recommendation.recommended_supplier_id == "sup_b"
    assert run.recommendation.ranked == ["sup_b", "sup_c", "sup_a"]


def test_live_factory_builds_the_document_agent_on_a_cached_gateway():
    agents = build_agents(Settings(MODE="live", LLM_GATEWAY_URL="https://gateway.invalid"))
    assert isinstance(agents["document"], LiveDocumentAgent)
    assert isinstance(agents["document"].llm, ReplayCache), "live extraction always goes through the cache"
    assert isinstance(agents["supplier_intel"], MockSupplierIntelAgent), "history is a lookup, never an LLM"
