"""Full in-process negotiated demo run in mock mode (PLAN.md §2 state machine, D17).

Mismatch demo (A's wrong total confirmed) → RECOMMENDED → negotiation with the top-2 eligible
suppliers, every draft auto-approved unedited → RE_SCORING → RECOMMENDED. Prints the event log
(one line per event, same format as print_event_log.py), then landed costs and scores before/after.

Usage: cd backend && uv run python scripts/demo_negotiation.py
"""

from print_event_log import format_event, run_mismatch_demo
from procureai.agents.factory import build_agents
from procureai.config.settings import Settings
from procureai.domain.models import Run, Scorecard, WorkflowState
from procureai.workflow import Orchestrator, RunStore


def run_negotiated_demo(orchestrator: Orchestrator) -> tuple[str, list[Scorecard]]:
    """Returns (run_id, scorecards before negotiation)."""
    run_id = run_mismatch_demo(orchestrator)
    before = list(orchestrator.store.get(run_id).scorecards)
    run = orchestrator.start_negotiation(run_id)
    while run.state == WorkflowState.AWAITING_NEGOTIATION_APPROVAL:
        run = orchestrator.approve_negotiation(run_id, run.pending_human.details["supplier_id"])
    return run_id, before


def format_comparison(run: Run, before: list[Scorecard]) -> str:
    old = {c.supplier_id: c for c in before}
    lines = [f"{'supplier':<10} {'landed before':>14} {'landed after':>14} {'score before':>13} {'score after':>12}  negotiated offer"]
    for c in run.scorecards:
        quote = next(q for q in run.quotes if q.supplier_id == c.supplier_id)
        offer = (f"{quote.negotiated_offer.unit_price}/unit, {quote.negotiated_offer.lead_time_days} d "
                 f"(was {quote.unit_price}/unit, {quote.lead_time_days} d)") if quote.negotiated_offer else "-"
        b = old.get(c.supplier_id)
        lines.append(f"{c.supplier_id:<10} {b.landed_cost if b else 0:>14,.2f} {c.landed_cost:>14,.2f} "
                     f"{b.total_score if b else 0:>13.2f} {c.total_score:>12.2f}  {offer}")
    return "\n".join(lines)


if __name__ == "__main__":
    orch = Orchestrator(build_agents(Settings(MODE="mock")), RunStore())
    run_id, before = run_negotiated_demo(orch)
    for event in orch.store.events(run_id):
        print(format_event(event))
    run = orch.store.get(run_id)
    print(f"\nfinal state: {run.state}; recommended: {run.recommendation.recommended_supplier_id}\n")
    print(format_comparison(run, before))
    print(f"\nchange explanation: {run.recommendation.change_explanation}")
