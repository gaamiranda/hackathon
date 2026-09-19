"""PO gate (PLAN.md §1 step 9, §17 G5): RECOMMENDED → AWAITING_PO_APPROVAL → PO_GENERATED. Mock mode, no network.

The PurchaseOrder is built only inside the orchestrator's request_po / approve_po path; nothing else in the
codebase constructs one (see test_purchase_order_is_constructed_only_by_the_orchestrator)."""

import re
import subprocess
from decimal import Decimal as D
from pathlib import Path

import pytest

from procureai.domain.models import PurchaseOrder, WorkflowState as S
from procureai.engine import EvaluationResult
from procureai.po import render_po_pdf
from procureai.workflow import WorkflowError
from tests.test_interrupt import negotiated_run
from tests.test_negotiation import config, orch, recommended_run, request_  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
PO_NUMBER = re.compile(r"^PO-\d{8}-.{6}$")
# §15 after the interrupt: Cobalt 5,000 × 12.45 (negotiated), free shipping, no discount, 9 % tax.
COBALT_V2 = {"subtotal": D("62250.00"), "discount": D("0.00"), "shipping": D("0.00"), "tax": D("5602.50"), "total": D("67852.50")}


def replanned_run(orch, request_, config) -> str:
    """Demo path through the interrupt: 5,000 units / 75,000 budget, Cobalt recommended."""
    run_id = negotiated_run(orch, request_, config)
    run = orch.interrupt(run_id, quantity=5000, budget=D("75000"), reason="Customer order upsized")
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_c"
    return run_id


def types(orch, run_id: str) -> list[str]:
    return [e.type for e in orch.store.events(run_id)]


def test_request_po_builds_preview_for_cobalt_at_v2(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    run = orch.request_po(run_id)
    assert run.state == S.AWAITING_PO_APPROVAL and run.purchase_order is None
    po = run.po_preview
    assert isinstance(po, PurchaseOrder)
    assert po.po_number is None and po.approved_by is None and po.approved_at is None
    assert po.run_id == run_id and po.request_version == 2 and po.currency == "USD"
    assert po.supplier.supplier_id == "sup_c" and po.supplier.name == "Cobalt Industrial"
    assert len(po.line_items) == 1
    line = po.line_items[0]
    assert line.description == "Product X" and line.quantity == 5000
    assert line.unit_price == D("12.45") and line.line_total == D("62250.00")
    assert po.negotiated is True and po.lead_time_days == 9
    assert {k: getattr(po.totals, k) for k in COBALT_V2} == COBALT_V2
    card = next(c for c in run.scorecards if c.supplier_id == "sup_c")
    validated = next(v for v in run.validated if v.supplier_id == "sup_c")
    assert po.totals.total == card.landed_cost == validated.landed_cost
    assert po.payment_terms == validated.payment_terms

    pending = run.pending_human
    assert pending.kind == "po_approval" and pending.quote_ids == [validated.quote_id]
    assert pending.details == {
        "supplier_id": "sup_c", "supplier_name": "Cobalt Industrial",
        "totals": {k: str(v) for k, v in COBALT_V2.items()},
        "unit_price": "12.45", "lead_time_days": 9, "negotiated": True, "request_version": 2,
    }
    ev = orch.store.events(run_id)[-1]
    assert ev.type == "po.requested" and ev.actor == "engine"
    assert ev.state_before == S.RECOMMENDED and ev.state_after == S.AWAITING_PO_APPROVAL
    assert ev.payload["po_preview"]["po_number"] is None


def test_approve_po_generates_numbered_po_and_is_terminal(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    orch.request_po(run_id)
    run = orch.approve_po(run_id, "demo-user")
    assert run.state == S.PO_GENERATED and run.pending_human is None and run.po_preview is None
    po = run.purchase_order
    assert PO_NUMBER.match(po.po_number), po.po_number
    assert po.po_number == f"PO-{po.approved_at:%Y%m%d}-{run_id[:6]}"
    assert po.approved_by == "demo-user" and po.approved_at is not None
    assert po.supplier.supplier_id == "sup_c" and po.line_items[0].unit_price == D("12.45")
    assert po.totals.total == D("67852.50") and po.request_version == 2
    ev = orch.store.events(run_id)[-1]
    assert ev.type == "po.generated" and ev.actor == "human"
    assert ev.state_before == S.AWAITING_PO_APPROVAL and ev.state_after == S.PO_GENERATED
    assert ev.payload["purchase_order"] == po.model_dump(mode="json")
    assert ev.payload["approved_by"] == "demo-user" and po.po_number in ev.summary

    # PO_GENERATED is terminal
    for call in (lambda: orch.interrupt(run_id, quantity=6000), lambda: orch.start_negotiation(run_id),
                 lambda: orch.request_po(run_id), lambda: orch.approve_po(run_id, "x"), lambda: orch.reject_po(run_id),
                 lambda: orch.run_evaluation(run_id)):
        with pytest.raises(WorkflowError) as exc:
            call()
        assert exc.value.code == "illegal_transition"
    assert orch.store.get(run_id).state == S.PO_GENERATED and types(orch, run_id)[-1] == "po.generated"

    profile = orch.agents["supplier_intel"].get_profile("sup_c")
    pdf = render_po_pdf(po, run.request, profile)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_approve_po_needs_an_approver(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    orch.request_po(run_id)
    with pytest.raises(WorkflowError) as exc:
        orch.approve_po(run_id, "   ")
    assert exc.value.code == "approver_required"
    assert orch.store.get(run_id).state == S.AWAITING_PO_APPROVAL


def test_approve_po_directly_from_recommended_is_illegal(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    with pytest.raises(WorkflowError) as exc:
        orch.approve_po(run_id, "demo-user")
    assert exc.value.code == "illegal_transition"
    run = orch.store.get(run_id)
    assert run.state == S.RECOMMENDED and run.purchase_order is None and "po.generated" not in types(orch, run_id)


def test_request_po_without_eligible_recommendation_is_rejected(orch, request_, config):
    # No budget increase: everything over budget → escalation, no recommended supplier
    run_id = negotiated_run(orch, request_, config)
    run = orch.interrupt(run_id, quantity=5000)
    assert run.recommendation.recommended_supplier_id is None
    with pytest.raises(WorkflowError) as exc:
        orch.request_po(run_id)
    assert exc.value.code == "no_recommendation"
    assert orch.store.get(run_id).state == S.RECOMMENDED and orch.store.get(run_id).po_preview is None

    # A recommended supplier whose scorecard is (somehow) ineligible must be refused too
    run_id = replanned_run(orch, request_, config)
    run = orch.store.get(run_id)
    card = next(c for c in run.scorecards if c.supplier_id == "sup_c")
    card.eligible, card.ineligibility_reasons = False, ["blacklisted"]
    with pytest.raises(WorkflowError) as exc:
        orch.request_po(run_id)
    assert exc.value.code == "supplier_ineligible" and "blacklisted" in exc.value.message


def test_request_po_requires_the_approval_gate_in_config(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    orch.store.get(run_id).config.approvals.po_generation = False
    with pytest.raises(WorkflowError) as exc:
        orch.request_po(run_id)
    assert exc.value.code == "approval_required"


def test_reject_po_returns_to_recommended_and_keeps_options_open(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    orch.request_po(run_id)
    run = orch.reject_po(run_id, "Want to try Apex first")
    assert run.state == S.RECOMMENDED and run.po_preview is None and run.purchase_order is None
    assert run.pending_human is None
    ev = orch.store.events(run_id)[-1]
    assert ev.type == "po.rejected" and ev.actor == "human" and ev.payload["reason"] == "Want to try Apex first"
    assert ev.state_before == S.AWAITING_PO_APPROVAL and ev.state_after == S.RECOMMENDED
    # negotiation (Apex has no thread yet) and a further interrupt are still available
    run = orch.start_negotiation(run_id)
    assert run.state == S.AWAITING_NEGOTIATION_APPROVAL and run.pending_human.details["supplier_id"] == "sup_a"
    run = orch.interrupt(run_id, quantity=4500)
    assert run.state == S.RECOMMENDED and run.request.version == 3


def test_interrupt_from_awaiting_po_approval_discards_preview_then_replans(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    orch.request_po(run_id)
    seen = len(orch.store.events(run_id))
    run = orch.interrupt(run_id, quantity=4000, reason="Trimmed")
    assert run.state == S.RECOMMENDED and run.po_preview is None and run.purchase_order is None
    assert run.request.version == 3 and run.pending_human is None
    t = types(orch, run_id)[seen:]
    assert t.index("requirement.changed") < t.index("po.discarded") < t.index("replan.started") < t.index("replan.completed")
    discarded = next(e for e in orch.store.events(run_id) if e.type == "po.discarded")
    assert discarded.payload["supplier_id"] == "sup_c" and discarded.payload["po_preview"]["request_version"] == 2
    assert "po.generated" not in t
    # and the gate can be reached again at the new version
    run = orch.request_po(run_id)
    assert run.state == S.AWAITING_PO_APPROVAL and run.po_preview.request_version == 3
    assert run.po_preview.line_items[0].quantity == 4000


def test_approve_po_refuses_when_engine_totals_moved(orch, request_, config, monkeypatch):
    run_id = replanned_run(orch, request_, config)
    orch.request_po(run_id)
    real = orch.engine

    def drifted(*args, **kwargs) -> EvaluationResult:
        result = real(*args, **kwargs)
        bumped = [v.model_copy(update={"landed_cost": v.landed_cost + D("0.01")}) for v in result.validated]
        return result.model_copy(update={"validated": bumped})

    monkeypatch.setattr(orch, "engine", drifted)
    with pytest.raises(WorkflowError) as exc:
        orch.approve_po(run_id, "demo-user")
    assert exc.value.code == "totals_changed"
    run = orch.store.get(run_id)
    assert run.state == S.AWAITING_PO_APPROVAL and run.purchase_order is None and "po.generated" not in types(orch, run_id)
    # with the real engine back, the same preview approves cleanly
    monkeypatch.setattr(orch, "engine", real)
    assert orch.approve_po(run_id, "demo-user").state == S.PO_GENERATED


def test_request_po_before_recommended_is_illegal(orch, request_, config):
    run = orch.create_run(request_, config)
    for call in (lambda: orch.request_po(run.run_id), lambda: orch.approve_po(run.run_id, "x"), lambda: orch.reject_po(run.run_id)):
        with pytest.raises(WorkflowError) as exc:
            call()
        assert exc.value.code == "illegal_transition"


def test_render_refuses_an_unapproved_preview(orch, request_, config):
    run_id = replanned_run(orch, request_, config)
    run = orch.request_po(run_id)
    with pytest.raises(ValueError):
        render_po_pdf(run.po_preview, run.request, None)


def test_purchase_order_is_constructed_only_by_the_orchestrator():
    """G5 / acceptance 4: `PurchaseOrder(` appears in the domain definition and the orchestrator's _build_po only."""
    out = subprocess.run(["git", "grep", "-n", r"PurchaseOrder(", "--", "*.py", "*.ts", "*.tsx"],
                         cwd=BACKEND.parent, capture_output=True, text=True).stdout
    hits = [line for line in out.splitlines() if not line.startswith("backend/tests/")]
    files = sorted({h.split(":")[0] for h in hits})
    assert files == ["backend/procureai/domain/models.py", "backend/procureai/workflow/orchestrator.py"], hits
    assert sum("orchestrator.py" in h for h in hits) == 1
