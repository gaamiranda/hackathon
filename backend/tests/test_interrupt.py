"""Mid-workflow interrupt → replan (PLAN.md §1 step 8, D22, §15 expected outcome). Mock mode, no network."""

import time
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from procureai.domain.models import ProcurementRequest, WorkflowState as S
from procureai.workflow import WorkflowError
from tests.test_negotiation import DOCS, approve_all, config, orch, recommended_run, request_  # noqa: F401

REPLAN_EVENTS = ("requirement.changed", "replan.started", "replan.completed")


def negotiated_run(orch, request_, config) -> str:
    """Demo path through the negotiation loop: B and C settled with negotiated offers, B recommended."""
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_b"
    return run_id


def types_since(orch, run_id: str, seq: int) -> list[str]:
    return [e.type for e in orch.store.events(run_id) if e.seq > seq]


def test_upsized_order_flips_recommendation_to_cobalt(orch, request_, config):
    run_id = negotiated_run(orch, request_, config)
    last_seq = orch.store.events(run_id)[-1].seq
    started = time.perf_counter()
    run = orch.interrupt(run_id, quantity=5000, budget=D("75000"), reason="Customer order upsized")
    assert time.perf_counter() - started < 1.0  # acceptance 4: replan wall-clock in mock mode

    assert run.state == S.RECOMMENDED and run.pending_human is None
    assert run.request.version == 2 and run.request.quantity == 5000 and run.request.budget == D("75000.00")
    assert run.request.created_at == request_.created_at and run.request.required_by == request_.required_by
    assert [r.version for r in run.request_history] == [1] and run.request_history[0].quantity == 2000

    cards = {c.supplier_id: c for c in run.scorecards}
    assert not cards["sup_b"].eligible and any("capacity" in r for r in cards["sup_b"].ineligibility_reasons)
    assert run.recommendation.recommended_supplier_id == "sup_c"
    assert run.recommendation.ranked == ["sup_c", "sup_a", "sup_b"]  # §15: C 62.9, A 58.6, B ineligible
    assert run.recommendation.request_version == 2 and run.recommendation.escalation is None
    assert "capacity" in run.recommendation.change_explanation
    assert "quantity 2,000 → 5,000" in run.recommendation.change_explanation

    impact = run.replan_impact
    assert impact.from_version == 1 and impact.to_version == 2
    assert impact.recommended_before == "sup_b" and impact.recommended_after == "sup_c"
    assert impact.changes["quantity"].before == 2000 and impact.changes["quantity"].after == 5000
    assert impact.changes["budget"].after == D("75000.00") and set(impact.changes) == {"quantity", "budget"}
    b = next(s for s in impact.per_supplier if s.supplier_id == "sup_b")
    assert b.eligible_before and not b.eligible_after and b.score_before > 0 and b.score_after == 0
    assert b.landed_before == D("26763.86") and any("capacity" in r for r in b.reasons)
    assert impact.summary_lines[0] == "Recommended supplier changed from sup_b to sup_c."

    # negotiated offers survive the replan (no re-extraction, no re-negotiation)
    offers = {q.supplier_id: q.negotiated_offer for q in run.quotes}
    assert offers["sup_b"].unit_price == D("12.40") and offers["sup_c"].unit_price == D("12.45")
    assert run.negotiations["sup_b"].status == "accepted" and len(run.documents) == 3

    types = types_since(orch, run_id, last_seq)
    assert [t for t in types if t in REPLAN_EVENTS] == list(REPLAN_EVENTS)
    assert types[:2] == ["requirement.changed", "replan.started"] and types[-1] == "replan.completed"
    for t in ("validation.started", "quotes.validated", "enrichment.started", "scoring.started",
              "quotes.scored", "recommendation.ranked", "recommendation.ready"):
        assert t in types
    assert "calc.mismatch" not in types  # A's confirmed total stays authoritative (G1 already satisfied)
    changed = next(e for e in orch.store.events(run_id) if e.type == "requirement.changed")
    assert changed.actor == "human" and changed.payload["reason"] == "Customer order upsized"
    assert changed.payload["changes"]["quantity"] == {"before": 2000, "after": 5000}
    started_ev = next(e for e in orch.store.events(run_id) if e.type == "replan.started")
    assert started_ev.state_after == S.REPLANNING and started_ev.payload["revisiting"] == ["capacity", "moq", "pricing", "budget", "risk"]
    done = next(e for e in orch.store.events(run_id) if e.type == "replan.completed")
    assert done.payload["impact"]["recommended_after"] == "sup_c" and "Recommendation changed" in done.summary


def test_upsize_without_budget_escalates_no_eligible_supplier(orch, request_, config):
    run_id = negotiated_run(orch, request_, config)
    run = orch.interrupt(run_id, quantity=5000)
    assert run.state == S.RECOMMENDED
    assert all(not c.eligible for c in run.scorecards)
    assert all(any("over budget" in r for r in c.ineligibility_reasons) for c in run.scorecards)
    assert run.recommendation.recommended_supplier_id is None
    assert run.recommendation.escalation.reason == "no_eligible_supplier"
    assert set(run.recommendation.escalation.details["per_supplier"]) == {"sup_a", "sup_b", "sup_c"}
    assert run.replan_impact.recommended_after is None and set(run.replan_impact.changes) == {"quantity"}
    done = next(e for e in orch.store.events(run_id) if e.type == "replan.completed")
    assert "ESCALATION" in done.summary and done.payload["escalation"]["reason"] == "no_eligible_supplier"


def test_interrupt_while_awaiting_approval_discards_draft(orch, request_, config):
    run_id = recommended_run(orch, request_, config)
    run = orch.start_negotiation(run_id)
    assert run.state == S.AWAITING_NEGOTIATION_APPROVAL and run.pending_human.details["supplier_id"] == "sup_b"
    run = orch.interrupt(run_id, quantity=5000, budget=D("75000"))
    assert run.state == S.RECOMMENDED and run.pending_human is None
    assert run.negotiations["sup_b"].status == "closed"  # nothing was ever sent (G5)
    assert next(q for q in run.quotes if q.supplier_id == "sup_b").negotiated_offer is None
    types = [e.type for e in orch.store.events(run_id)]
    assert types.index("requirement.changed") < types.index("negotiation.discarded") < types.index("replan.started")
    assert run.recommendation.recommended_supplier_id == "sup_c"
    # C has no thread, so negotiation can start again after the replan (T11 rule)
    run = orch.start_negotiation(run_id)
    assert run.state == S.AWAITING_NEGOTIATION_APPROVAL and run.pending_human.details["supplier_id"] == "sup_c"


def test_interrupt_in_created_is_illegal(orch, request_, config):
    run = orch.create_run(request_, config)
    with pytest.raises(WorkflowError) as exc:
        orch.interrupt(run.run_id, quantity=5000)
    assert exc.value.code == "illegal_transition"


def test_interrupt_without_a_change_is_rejected(orch, request_, config):
    run_id = recommended_run(orch, request_, config)
    before = len(orch.store.events(run_id))
    for kwargs in ({}, {"quantity": 2000}, {"budget": D("30000.00"), "required_by": request_.required_by}):
        with pytest.raises(WorkflowError) as exc:
            orch.interrupt(run_id, **kwargs)
        assert exc.value.code == "no_change"
    run = orch.store.get(run_id)
    assert run.request.version == 1 and run.state == S.RECOMMENDED and len(orch.store.events(run_id)) == before


def test_tighter_deadline_makes_slow_suppliers_ineligible(orch, request_, config):
    run_id = recommended_run(orch, request_, config)
    new_date = request_.created_at.date() + timedelta(days=5)
    run = orch.interrupt(run_id, required_by=new_date, reason="Launch moved up")
    assert run.state == S.RECOMMENDED and run.request.required_by == new_date
    for c in run.scorecards:  # A 13 d, B 10 d, C 9 d all exceed the 5-day window
        assert not c.eligible and any("lead time" in r for r in c.ineligibility_reasons)
    assert run.recommendation.escalation.reason == "no_eligible_supplier"
    started = next(e for e in orch.store.events(run_id) if e.type == "replan.started")
    assert started.payload["revisiting"] == ["lead_time"]


def test_interrupt_from_extracted_evaluates_the_new_request(orch, request_, config):
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, DOCS)
    assert run.state == S.EXTRACTED
    run = orch.interrupt(run.run_id, quantity=5000, budget=D("75000"))
    # Apex's wrong printed total was never confirmed, so the G1 gate still applies during the replan
    assert run.state == S.CALC_MISMATCH and run.request.version == 2
    run = orch.confirm_quote_math(run.run_id, run.pending_human.quote_ids[0], use_computed=True)
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_c"
    assert run.replan_impact is not None and run.replan_impact.recommended_before is None
    assert run.replan_impact.recommended_after == "sup_c"
    assert [e.type for e in orch.store.events(run.run_id)][-1] == "replan.completed"


def test_request_history_grows_per_interrupt(orch, request_, config):
    run_id = recommended_run(orch, request_, config)
    orch.interrupt(run_id, quantity=3000)
    run = orch.interrupt(run_id, budget=D("75000"))
    assert run.request.version == 3 and [r.version for r in run.request_history] == [1, 2]
    assert isinstance(run.request_history[0], ProcurementRequest)
    assert run.replan_impact.from_version == 2 and run.replan_impact.to_version == 3
