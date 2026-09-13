"""Run the mismatch demo path in mock mode and print the event log, one line per event.

Usage: cd backend && uv run python scripts/print_event_log.py
"""

from pathlib import Path

from procureai.agents.factory import build_agents
from procureai.config.settings import Settings
from procureai.domain.models import ProcurementConfig, ProcurementRequest, QuoteSource, RawDocument, WorkflowEvent
from procureai.workflow import Orchestrator, RunStore

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
DEMO_DOCS = [
    ("supplier_a_apex.pdf", QuoteSource.PDF),
    ("supplier_b_borealis.xlsx", QuoteSource.XLSX),
    ("supplier_c_cobalt.eml.txt", QuoteSource.EMAIL),
]


def format_event(e: WorkflowEvent) -> str:
    return f"{e.seq:>3} {e.actor:<8} {e.type:<24} {e.state_before or '-':>22} → {e.state_after or '-':<22} {e.summary}"


def run_mismatch_demo(orchestrator: Orchestrator) -> str:
    request = ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())
    config = ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())
    run = orchestrator.create_run(request, config)
    docs = [RawDocument(doc_id=f"doc-{i}", filename=name, source=src, text="(parsed text)") for i, (name, src) in enumerate(DEMO_DOCS)]
    orchestrator.add_documents(run.run_id, docs)
    run = orchestrator.run_evaluation(run.run_id)
    for quote_id in list(run.pending_human.quote_ids if run.pending_human else []):
        orchestrator.confirm_quote_math(run.run_id, quote_id, use_computed=True)
    return run.run_id


if __name__ == "__main__":
    orch = Orchestrator(build_agents(Settings(MODE="mock")), RunStore())
    run_id = run_mismatch_demo(orch)
    for event in orch.store.events(run_id):
        print(format_event(event))
    print(f"\nfinal state: {orch.store.get(run_id).state}")
