"""Workflow state machine (PLAN.md §2, §17 gates G1/G2/G3/G5/G6).

CREATED → EXTRACTING → [NEEDS_HUMAN_EXTRACTION] → EXTRACTED → VALIDATING → [CALC_MISMATCH]
        → ENRICHING → SCORING → RECOMMENDED
        → (per supplier, top negotiate_top_n, sequentially; ≤ max_rounds buyer turns each)
          NEGOTIATION_DRAFTED → AWAITING_NEGOTIATION_APPROVAL → NEGOTIATING → COUNTER_RECEIVED
        → RE_SCORING → RECOMMENDED
RECOMMENDED → (request_po) AWAITING_PO_APPROVAL → (approve_po, human) PO_GENERATED  [terminal]
                                              → (reject_po, human) RECOMMENDED
RECOMMENDED | EXTRACTED | AWAITING_NEGOTIATION_APPROVAL | AWAITING_PO_APPROVAL
        → (interrupt, D22) REPLANNING → VALIDATING → … → RECOMMENDED

The orchestrator owns state and calls agents/engine; it never does arithmetic. Negotiation
guardrails live here, not in the agent: the round limit (G2) and the outbound leakage filter (G3).
The PurchaseOrder is constructed only in request_po (preview) and approve_po (final); no agent
tool reaches either (G5).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from procureai.agents.base import CRITICAL_FIELDS, AgentSet
from procureai.domain.models import (
    Escalation,
    EventActor,
    FieldChange,
    NegotiationOffer,
    NegotiationRole,
    NegotiationStatus,
    NegotiationThread,
    NegotiationTurn,
    NormalizedQuote,
    PendingHuman,
    PendingHumanKind,
    ProcurementConfig,
    ProcurementRequest,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderTotals,
    RawDocument,
    Recommendation,
    ReplanImpact,
    Run,
    Scorecard,
    SupplierImpact,
    SupplierProfile,
    SupplierRef,
    ValidatedQuote,
    WorkflowState,
)
from procureai.engine import (
    EvaluationResult,
    can_open_turn,
    check_negotiation_bounds,
    check_outbound_message,
    effective_lead_time_days,
    effective_unit_price,
    evaluate,
    explain_diff,
    recommended_id,
)
from procureai.engine.costing import pre_tax_total
from procureai.engine.policy import buyer_turns
from procureai.sim import ScriptedSupplier, SupplierSim
from procureai.workflow.errors import WorkflowError
from procureai.workflow.events import EventBus
from procureai.workflow.store import RunStore

S = WorkflowState
EngineFn = Callable[..., EvaluationResult]


def _json(model_or_list: Any) -> Any:
    if isinstance(model_or_list, list):
        return [m.model_dump(mode="json") for m in model_or_list]
    return model_or_list.model_dump(mode="json")


# Which engine checks an interrupt revisits, per changed request field (replan.started payload).
REVISITED_BY_FIELD: dict[str, list[str]] = {
    "quantity": ["capacity", "moq", "pricing", "budget", "risk"],
    "budget": ["budget"],
    "required_by": ["lead_time"],
}
REVISIT_ORDER = ["capacity", "moq", "pricing", "lead_time", "budget", "risk"]


@dataclass
class _Replan:
    """Pre-interrupt snapshot kept until the replan reaches RECOMMENDED (survives a CALC_MISMATCH stop)."""

    from_version: int
    to_version: int
    changes: dict[str, FieldChange]
    scorecards: list[Scorecard]
    recommended: str | None
    math_resolved: bool


class Orchestrator:
    def __init__(
        self,
        agents: AgentSet,
        store: RunStore,
        events: EventBus | None = None,
        engine: EngineFn = evaluate,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        supplier: SupplierSim | None = None,
    ) -> None:
        self.agents = agents
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.events = events or EventBus(store, now=self.now)
        self.engine = engine
        self.new_id = id_factory or (lambda: f"run-{uuid4().hex[:8]}")
        self.supplier = supplier or ScriptedSupplier()
        self._replans: dict[str, _Replan] = {}

    # ------------------------------------------------------------------ public API

    def create_run(self, request: ProcurementRequest, config: ProcurementConfig) -> Run:
        with self.store.lock:
            ts = self.now()
            run = Run(run_id=self.new_id(), request=request, config=config, created_at=ts, updated_at=ts)
            self.store.put(run)
            self._emit(run, EventActor.ENGINE, "run.created", f"Run created for {request.quantity:,} × {request.product}",
                       {"request": _json(request)}, state_after=S.CREATED)
            return run

    def add_documents(self, run_id: str, docs: list[RawDocument]) -> Run:
        """Extract every document. Ends in EXTRACTED (clean, ready for run_evaluation)
        or NEEDS_HUMAN_EXTRACTION (low-confidence / missing critical fields, G6)."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.CREATED, S.EXTRACTED}, "add_documents")
            if not docs:
                raise WorkflowError("no_documents", "add_documents needs at least one document")
            run.documents.extend(docs)
            self._transition(run, S.EXTRACTING, EventActor.ENGINE, "documents.added",
                             f"{len(docs)} document(s) queued for extraction",
                             {"documents": [{"doc_id": d.doc_id, "filename": d.filename, "source": d.source} for d in docs]})
            for doc in docs:
                self._extract(run, doc)
            self._finish_extraction(run)
            return self._save(run)

    def replace_document(self, run_id: str, doc_id: str, doc: RawDocument) -> Run:
        """Re-upload one document (failed parse or bad extraction): drops the old document
        and the quote extracted from it, extracts the new one, re-checks (D15)."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.EXTRACTED, S.NEEDS_HUMAN_EXTRACTION}, "replace_document")
            if not any(d.doc_id == doc_id for d in run.documents):
                raise WorkflowError("document_not_found", f"unknown doc_id {doc_id!r} in run {run.run_id}")
            old_quote_id = self._quote_id_for_document(run, doc_id)
            run.documents = [d for d in run.documents if d.doc_id != doc_id]
            run.quotes = [q for q in run.quotes if q.quote_id != old_quote_id]
            if run.pending_human:
                run.pending_human.details.get("failed_documents", {}).pop(doc_id, None)
                run.pending_human.details.get("fields", {}).pop(old_quote_id, None)
                if old_quote_id in run.pending_human.quote_ids:
                    run.pending_human.quote_ids.remove(old_quote_id)
            run.documents.append(doc)
            self._emit(run, EventActor.HUMAN, "document.replaced", f"Document {doc_id} replaced by {doc.doc_id} ({doc.filename})",
                       {"old_doc_id": doc_id, "old_quote_id": old_quote_id, "doc_id": doc.doc_id, "filename": doc.filename})
            self._extract(run, doc)
            self._finish_extraction(run)
            return self._save(run)

    def correct_quote(self, run_id: str, quote_id: str, patch: dict[str, Any]) -> Run:
        """Human fills/overrides fields; their confidence becomes 1.0. Resumes when nothing is pending."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.NEEDS_HUMAN_EXTRACTION}, "correct_quote")
            idx = self._quote_index(run, quote_id)
            old = run.quotes[idx]
            unknown = set(patch) - set(NormalizedQuote.model_fields)
            if unknown:
                raise WorkflowError("bad_patch", f"unknown NormalizedQuote fields: {sorted(unknown)}")
            confidences = {**old.field_confidence, **{k: 1.0 for k in patch}}
            # re-validate: patches arrive as raw JSON values from the human form
            run.quotes[idx] = NormalizedQuote.model_validate({**old.model_dump(), **patch, "field_confidence": confidences})
            self._emit(run, EventActor.HUMAN, "quote.corrected", f"Human corrected {sorted(patch)} on {quote_id}",
                       {"quote_id": quote_id, "patch": patch, "quote": _json(run.quotes[idx])})
            if self._check_extraction(run):
                self._transition(run, S.EXTRACTED, EventActor.HUMAN, "extraction.resumed",
                                 "All critical fields confirmed; resuming")
                self._evaluate(run)
            return self._save(run)

    def run_evaluation(self, run_id: str) -> Run:
        """VALIDATING → (CALC_MISMATCH stop) → ENRICHING → SCORING → RECOMMENDED."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.EXTRACTED}, "run_evaluation")
            if not run.quotes:
                raise WorkflowError("no_quotes", "run_evaluation needs extracted quotes; call add_documents first")
            if run.pending_human:
                raise WorkflowError("pending_human", f"human input pending: {run.pending_human.message}")
            self._evaluate(run)
            return self._save(run)

    def confirm_quote_math(self, run_id: str, quote_id: str, use_computed: bool) -> Run:
        """Human resolves a calculation mismatch (G1). use_computed=True replaces the
        document's stated total with the engine's pre-tax total (original kept in the
        audit event); False withdraws the quote from the run. Resumes when nothing is pending."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.CALC_MISMATCH}, "confirm_quote_math")
            pending = run.pending_human
            if pending is None or pending.kind != PendingHumanKind.CALC_MISMATCH or quote_id not in pending.quote_ids:
                raise WorkflowError("not_pending", f"quote {quote_id!r} has no pending calculation mismatch")
            idx = self._quote_index(run, quote_id)
            quote = run.quotes[idx]
            # The printed total refers to the supplier's quoted quantity, not the (possibly replanned) request
            # quantity; storing this figure keeps math_ok true at any later quantity.
            computed = self._quoted_total(quote)
            if use_computed:
                run.quotes[idx] = quote.model_copy(update={"llm_stated_total": computed})
                self._emit(run, EventActor.HUMAN, "quote.math_confirmed",
                           f"Human accepted computed total {computed} for {quote_id} (document said {quote.llm_stated_total})",
                           {"quote_id": quote_id, "original_stated_total": str(quote.llm_stated_total),
                            "computed_pre_tax_total": str(computed)})
            else:
                run.quotes.pop(idx)
                run.validated = [v for v in run.validated if v.quote_id != quote_id]
                self._emit(run, EventActor.HUMAN, "quote.rejected",
                           f"Human withdrew {quote_id} over unresolved total mismatch",
                           {"quote_id": quote_id, "original_stated_total": str(quote.llm_stated_total),
                            "computed_pre_tax_total": str(computed)})
            pending.quote_ids.remove(quote_id)
            pending.details.pop(quote_id, None)
            if not pending.quote_ids:
                run.pending_human = None
                self._transition(run, S.EXTRACTED, EventActor.HUMAN, "mismatch.resolved",
                                 "All calculation mismatches resolved; resuming")
                self._evaluate(run)
            return self._save(run)

    def start_negotiation(self, run_id: str) -> Run:
        """RECOMMENDED → draft for the best eligible supplier → AWAITING_NEGOTIATION_APPROVAL.

        Targets are the top `negotiate_top_n` eligible suppliers by score, handled one at a
        time (D17). Each draft passes the outbound policy filter before the human gate (G3, G5)."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.RECOMMENDED}, "start_negotiation")
            targets = self._negotiation_targets(run)
            if not targets:
                raise WorkflowError("nothing_to_negotiate", "no eligible supplier without a settled negotiation thread")
            b = run.config.negotiation
            self._emit(run, EventActor.ENGINE, "negotiation.started",
                       f"Negotiating with {', '.join(targets)} (top {b.negotiate_top_n} eligible, one at a time, "
                       f"max {b.max_rounds} rounds each)",
                       {"supplier_ids": targets, "boundaries": _json(b)})
            if not self._open_thread(run, targets[0]):
                raise WorkflowError("policy_violation", f"draft to {targets[0]} blocked by the outbound policy filter")
            return self._save(run)

    def approve_negotiation(self, run_id: str, supplier_id: str, message: str | None = None) -> Run:
        """Human approves the pending draft (optionally edited; edits are re-filtered, G3), which is
        then "sent" to the simulated supplier. The counter is recorded, evaluated and either
        answered with another draft (within the round limit, G2) or settled; when every target
        supplier is settled the run re-scores with the negotiated offers → RECOMMENDED."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.AWAITING_NEGOTIATION_APPROVAL}, "approve_negotiation")
            pending = run.pending_human
            if (pending is None or pending.kind != PendingHumanKind.NEGOTIATION_APPROVAL
                    or pending.details.get("supplier_id") != supplier_id):
                raise WorkflowError("not_pending", f"no negotiation draft awaiting approval for {supplier_id!r}")
            thread = run.negotiations[supplier_id]
            quote = self._quote_for_supplier(run, supplier_id)
            target = NegotiationOffer.model_validate(pending.details["target_offer"])
            round_no = pending.details["round"]
            text = pending.details["draft"] if message is None else message
            edited = text != pending.details["draft"]
            if edited:
                policy = self._check_outbound(run, quote, thread, text, target)
                if not policy.ok:
                    self._emit(run, EventActor.HUMAN, "negotiation.policy_blocked",
                               f"Edited message to {supplier_id} blocked: " + "; ".join(policy.violations),
                               {"supplier_id": supplier_id, "round": round_no, "offer": _json(target),
                                "violations": policy.violations, "source": "human_edit"})
                    raise WorkflowError("policy_violation", "; ".join(policy.violations))
            if not can_open_turn(thread, thread.boundaries):  # G2, enforced here regardless of the agent
                raise WorkflowError("round_limit", f"{supplier_id}: {thread.boundaries.max_rounds} buyer turns already used")

            thread.turns.append(NegotiationTurn(role=NegotiationRole.BUYER, message=text, offer=target,
                                                approved_by_human=True, ts=self.now()))
            run.pending_human = None
            self._transition(run, S.NEGOTIATING, EventActor.HUMAN, "negotiation.sent",
                             f"Round {round_no} message to {supplier_id} approved{' (edited)' if edited else ''} and sent",
                             {"supplier_id": supplier_id, "round": round_no, "offer": _json(target),
                              "message": text, "edited": edited})

            reply = self.supplier.reply(supplier_id, round_no)
            # Reply text is untrusted supplier content: stored verbatim, never interpreted (G4).
            thread.turns.append(NegotiationTurn(role=NegotiationRole.SUPPLIER, message=reply.reply_text,
                                                offer=reply.offer, ts=self.now()))
            if reply.offer is not None:
                thread.current_offer = reply.offer
            self._transition(run, S.COUNTER_RECEIVED, EventActor.SUPPLIER, "supplier.counter_offer",
                             f"{supplier_id} round {round_no}: " + (
                                 f"counter {reply.offer.unit_price}/unit, {reply.offer.lead_time_days} d"
                                 if reply.offer else "no counter-offer (rejected)"),
                             {"supplier_id": supplier_id, "round": round_no,
                              "offer": _json(reply.offer) if reply.offer else None, "reply_text": reply.reply_text})
            self._settle_round(run, thread, quote, reply.offer, round_no)
            return self._save(run)

    def interrupt(self, run_id: str, *, quantity: int | None = None, budget: Decimal | None = None,
                  required_by: date | None = None, reason: str = "") -> Run:
        """Mid-workflow requirement change (D22): request version+1 (previous kept in request_history),
        a pending negotiation draft is discarded, then the same evaluate path runs again on the existing
        quotes (negotiated offers carried over, nothing re-extracted) → RECOMMENDED with a ReplanImpact.
        Nothing eligible afterwards is an escalation for the human, not an error."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.RECOMMENDED, S.EXTRACTED, S.AWAITING_NEGOTIATION_APPROVAL, S.AWAITING_PO_APPROVAL},
                          "interrupt")
            old = run.request
            if budget is not None:
                budget = Decimal(budget).quantize(Decimal("0.01"))
            proposed = {"quantity": quantity, "budget": budget, "required_by": required_by}
            changes = {k: FieldChange(before=getattr(old, k), after=v)
                       for k, v in proposed.items() if v is not None and v != getattr(old, k)}
            if not changes:
                raise WorkflowError("no_change", "interrupt needs at least one of quantity, budget, required_by to change")

            run.request_history.append(old)
            run.request = old.model_copy(update={**{k: c.after for k, c in changes.items()}, "version": old.version + 1})
            self._emit(run, EventActor.HUMAN, "requirement.changed",
                       f"Requirement changed (v{old.version} → v{run.request.version}): " + ", ".join(
                           f"{k} {self._fmt(c.before)} → {self._fmt(c.after)}" for k, c in changes.items())
                       + (f" — {reason}" if reason else ""),
                       {"from_version": old.version, "to_version": run.request.version,
                        "changes": {k: _json(c) for k, c in changes.items()}, "reason": reason,
                        "request": _json(run.request)})

            pending = run.pending_human
            if pending is not None and pending.kind == PendingHumanKind.NEGOTIATION_APPROVAL:
                self._discard_draft(run, pending)
            elif pending is not None and pending.kind == PendingHumanKind.PO_APPROVAL:
                self._discard_po_preview(run)

            self._replans[run.run_id] = _Replan(
                from_version=old.version, to_version=run.request.version, changes=changes,
                scorecards=list(run.scorecards), recommended=recommended_id(run.scorecards),
                math_resolved=bool(run.scorecards))
            revisiting = [r for r in REVISIT_ORDER if any(r in REVISITED_BY_FIELD[k] for k in changes)]
            self._transition(run, S.REPLANNING, EventActor.ENGINE, "replan.started",
                             "Replanning without restart: revisiting " + ", ".join(revisiting)
                             + f" for {len(run.quotes)} existing quote(s)",
                             {"from_version": old.version, "to_version": run.request.version,
                              "revisiting": revisiting, "changed_fields": sorted(changes),
                              "quote_ids": [q.quote_id for q in run.quotes],
                              "negotiated": [q.supplier_id for q in run.quotes if q.negotiated_offer]})
            self._evaluate(run)
            return self._save(run)

    def request_po(self, run_id: str) -> Run:
        """RECOMMENDED → AWAITING_PO_APPROVAL with an unnumbered PO preview for the recommended supplier,
        costed by the engine at the current request version with the effective (negotiated) terms.
        The final document exists only after approve_po (G5); config.approvals.po_generation must be on."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.RECOMMENDED}, "request_po")
            if not run.config.approvals.po_generation:
                raise WorkflowError("approval_required", "PO generation without a human approval gate is not supported (G5)")
            sid = run.recommendation.recommended_supplier_id if run.recommendation else None
            if sid is None:
                raise WorkflowError("no_recommendation", "no recommended supplier to raise a purchase order for")
            if run.recommendation.request_version != run.request.version:
                raise WorkflowError("stale_recommendation",
                                    f"recommendation is for request v{run.recommendation.request_version}, current is v{run.request.version}")
            card = next((c for c in run.scorecards if c.supplier_id == sid), None)
            if card is None or not card.eligible:
                reasons = "; ".join(card.ineligibility_reasons) if card else "not scored"
                raise WorkflowError("supplier_ineligible", f"{sid} is not eligible: {reasons}")
            validated = next((v for v in run.validated if v.supplier_id == sid), None)
            if validated is None:
                raise WorkflowError("quote_not_found", f"no validated quote from {sid!r} in run {run.run_id}")

            run.po_preview = self._build_po(run, validated)
            run.pending_human = PendingHuman(
                kind=PendingHumanKind.PO_APPROVAL, quote_ids=[validated.quote_id],
                message=f"Approve {validated.supplier_name} as final supplier and generate the purchase order",
                details={"supplier_id": sid, "supplier_name": validated.supplier_name,
                         "totals": _json(run.po_preview.totals), "unit_price": str(effective_unit_price(validated)),
                         "lead_time_days": effective_lead_time_days(validated), "negotiated": validated.negotiated,
                         "request_version": run.request.version})
            self._transition(run, S.AWAITING_PO_APPROVAL, EventActor.ENGINE, "po.requested",
                             f"Purchase order for {run.request.quantity:,} × {run.request.product} from {validated.supplier_name} "
                             f"at {effective_unit_price(validated)}/unit ({run.po_preview.totals.total} {run.po_preview.currency} landed) "
                             "awaits human approval",
                             {"po_preview": _json(run.po_preview), "pending_human": _json(run.pending_human)})
            return self._save(run)

    def approve_po(self, run_id: str, approved_by: str) -> Run:
        """Human gate G5: AWAITING_PO_APPROVAL → PO_GENERATED (terminal). The totals are re-derived from the
        engine at approval time and must equal the preview the human saw (totals_changed otherwise)."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.AWAITING_PO_APPROVAL}, "approve_po")
            if not approved_by or not approved_by.strip():
                raise WorkflowError("approver_required", "approve_po needs the approver's name")
            preview = run.po_preview
            if preview is None or run.pending_human is None or run.pending_human.kind != PendingHumanKind.PO_APPROVAL:
                raise WorkflowError("not_pending", "no purchase order awaiting approval")
            quote = self._quote_for_supplier(run, preview.supplier.supplier_id)
            fresh = self._build_po(run, self._recost(run, quote))
            if fresh.totals != preview.totals or fresh.line_items != preview.line_items:
                raise WorkflowError("totals_changed",
                                    f"engine totals changed since the preview ({preview.totals.total} → {fresh.totals.total}); "
                                    "request the purchase order again")
            ts = self.now()
            po = fresh.model_copy(update={
                "po_number": f"PO-{ts:%Y%m%d}-{run.run_id[:6]}", "approved_by": approved_by.strip(), "approved_at": ts})
            run.purchase_order, run.po_preview, run.pending_human = po, None, None
            self._transition(run, S.PO_GENERATED, EventActor.HUMAN, "po.generated",
                             f"{po.po_number} generated for {po.supplier.name}: {po.line_items[0].quantity:,} × "
                             f"{po.line_items[0].unit_price}/unit, total {po.totals.total} {po.currency}; approved by {po.approved_by}",
                             {"purchase_order": _json(po), "approved_by": po.approved_by})
            return self._save(run)

    def reject_po(self, run_id: str, reason: str = "") -> Run:
        """Human declines the final supplier: AWAITING_PO_APPROVAL → RECOMMENDED, preview dropped.
        Negotiation and interrupt remain available afterwards."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.AWAITING_PO_APPROVAL}, "reject_po")
            preview = run.po_preview
            run.po_preview, run.pending_human = None, None
            self._transition(run, S.RECOMMENDED, EventActor.HUMAN, "po.rejected",
                             f"Purchase order for {preview.supplier.name if preview else 'the recommended supplier'} rejected by human"
                             + (f": {reason}" if reason else ""),
                             {"supplier_id": preview.supplier.supplier_id if preview else None, "reason": reason,
                              "po_preview": _json(preview) if preview else None})
            return self._save(run)

    # ------------------------------------------------------------------ purchase order internals

    def _build_po(self, run: Run, validated: ValidatedQuote) -> PurchaseOrder:
        """The only constructor of PurchaseOrder (G5). Every number is the engine's; nothing is computed here."""
        return PurchaseOrder(
            run_id=run.run_id, request_version=run.request.version,
            supplier=SupplierRef(supplier_id=validated.supplier_id, name=validated.supplier_name),
            currency=validated.currency,
            line_items=[PurchaseOrderLine(description=run.request.product, quantity=run.request.quantity,
                                          unit_price=effective_unit_price(validated), line_total=validated.subtotal)],
            totals=PurchaseOrderTotals(subtotal=validated.subtotal, discount=validated.discount, shipping=validated.shipping,
                                       tax=validated.tax, total=validated.landed_cost),
            lead_time_days=effective_lead_time_days(validated), payment_terms=validated.payment_terms,
            negotiated=validated.negotiated)

    def _recost(self, run: Run, quote: NormalizedQuote) -> ValidatedQuote:
        """Engine costing of one quote at the current request (mismatches were resolved before RECOMMENDED)."""
        result = self.engine(run.request, run.config, [quote], {}, include_math_mismatch=True)
        return next(v for v in result.validated if v.quote_id == quote.quote_id)

    def _discard_po_preview(self, run: Run) -> None:
        """An interrupt while a PO awaits approval drops the unnumbered preview (nothing was generated, G5)."""
        preview = run.po_preview
        run.po_preview, run.pending_human = None, None
        self._emit(run, EventActor.ENGINE, "po.discarded",
                   f"Unapproved purchase order preview for {preview.supplier.name if preview else 'the recommended supplier'} "
                   "discarded by the requirement change",
                   {"supplier_id": preview.supplier.supplier_id if preview else None,
                    "po_preview": _json(preview) if preview else None})

    # ------------------------------------------------------------------ replan internals

    def _discard_draft(self, run: Run, pending: PendingHuman) -> None:
        """Drop an unsent negotiation draft (nothing was ever sent, G5). The thread is settled with the
        best offer received so far, if any, so the replan costs the supplier at its negotiated terms."""
        sid, round_no = pending.details["supplier_id"], pending.details["round"]
        run.pending_human = None
        thread = run.negotiations.get(sid)
        applied = None
        if thread is not None and thread.status == NegotiationStatus.OPEN:
            thread.status = NegotiationStatus.CLOSED
            applied = self._best_received(thread)
            if applied is not None:
                thread.current_offer = applied
                quote = self._quote_for_supplier(run, sid)
                run.quotes[self._quote_index(run, quote.quote_id)] = quote.model_copy(update={"negotiated_offer": applied})
        self._emit(run, EventActor.ENGINE, "negotiation.discarded",
                   f"Unsent round {round_no} draft to {sid} discarded by the requirement change"
                   + (f"; keeping the {applied.unit_price}/unit, {applied.lead_time_days} d already offered" if applied
                      else ""),
                   {"supplier_id": sid, "round": round_no, "draft": pending.details.get("draft"),
                    "applied_offer": _json(applied) if applied else None})

    def _finish_replan(self, run: Run, ctx: _Replan) -> None:
        """Build ReplanImpact from the pre-interrupt scorecards, escalate if nothing is eligible, emit replan.completed."""
        before = {c.supplier_id: c for c in ctx.scorecards}
        after = {c.supplier_id: c for c in run.scorecards}
        per_supplier: list[SupplierImpact] = []
        for sid in [c.supplier_id for c in run.scorecards] + [s for s in before if s not in after]:
            b, a = before.get(sid), after.get(sid)
            reasons: list[str] = []
            if a is None:
                reasons.append("no longer scored")
            elif not a.eligible:
                reasons.extend(a.ineligibility_reasons)
                if b is not None and b.eligible:
                    reasons.insert(0, "became ineligible")
            elif b is not None and not b.eligible:
                reasons.append("became eligible")
            per_supplier.append(SupplierImpact(
                supplier_id=sid,
                eligible_before=b.eligible if b else False, eligible_after=a.eligible if a else False,
                landed_before=b.landed_cost if b else Decimal(0), landed_after=a.landed_cost if a else Decimal(0),
                score_before=b.total_score if b else 0.0, score_after=a.total_score if a else 0.0,
                reasons=reasons))
        top = recommended_id(run.scorecards)
        impact = ReplanImpact(
            from_version=ctx.from_version, to_version=ctx.to_version, changes=ctx.changes, per_supplier=per_supplier,
            recommended_before=ctx.recommended, recommended_after=top,
            summary_lines=explain_diff(ctx.scorecards, run.scorecards))
        run.replan_impact = impact
        if top is None and run.recommendation is not None:
            run.recommendation.escalation = Escalation(
                reason="no_eligible_supplier",
                details={"per_supplier": {s.supplier_id: s.reasons for s in per_supplier}})
        if ctx.recommended != top:
            headline = f"Recommendation changed: {ctx.recommended or 'none'} → {top or 'none eligible'}"
        else:
            headline = f"Recommendation unchanged: {top or 'none eligible'}"
        ineligible = [s.supplier_id for s in per_supplier if s.eligible_before and not s.eligible_after]
        self._emit(run, EventActor.ENGINE, "replan.completed",
                   f"Replan v{ctx.from_version} → v{ctx.to_version} complete. {headline}"
                   + (f"; {', '.join(ineligible)} no longer eligible" if ineligible else "")
                   + ("; ESCALATION: no eligible supplier" if top is None else ""),
                   {"impact": _json(impact), "escalation": _json(run.recommendation.escalation)
                    if run.recommendation and run.recommendation.escalation else None})

    def _changes_line(self, ctx: _Replan) -> str:
        return f"Request changed (v{ctx.from_version} → v{ctx.to_version}): " + "; ".join(
            f"{k.replace('_', ' ')} {self._fmt(c.before)} → {self._fmt(c.after)}" for k, c in ctx.changes.items()) + "."

    @staticmethod
    def _fmt(value: Any) -> str:
        return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}" if isinstance(value, Decimal) else str(value)

    # ------------------------------------------------------------------ negotiation internals

    def _negotiation_targets(self, run: Run) -> list[str]:
        """Top negotiate_top_n eligible suppliers (current ranking) that have no thread yet."""
        top = [c.supplier_id for c in run.scorecards if c.eligible][: run.config.negotiation.negotiate_top_n]
        return [sid for sid in top if sid not in run.negotiations]

    def _open_thread(self, run: Run, supplier_id: str) -> bool:
        quote = self._quote_for_supplier(run, supplier_id)
        offer = NegotiationOffer(unit_price=effective_unit_price(quote), lead_time_days=effective_lead_time_days(quote))
        thread = NegotiationThread(run_id=run.run_id, supplier_id=supplier_id, boundaries=run.config.negotiation,
                                   original_offer=offer, current_offer=offer)
        run.negotiations[supplier_id] = thread
        return self._draft_turn(run, thread, quote)

    def _draft_turn(self, run: Run, thread: NegotiationThread, quote: NormalizedQuote) -> bool:
        """Agent drafts → envelope + leakage filter → NEGOTIATION_DRAFTED → AWAITING_NEGOTIATION_APPROVAL.
        Returns False (thread escalated, negotiation.policy_blocked emitted) when the draft is blocked."""
        sid, b = thread.supplier_id, thread.boundaries
        round_no = buyer_turns(thread) + 1
        self._agent_started(run, "negotiation", f"Drafting round {round_no} message to {sid}",
                            {"supplier_id": sid, "round": round_no})
        draft = self.agents["negotiation"].draft(run.request, quote, thread, b)
        target = draft["target_offer"]
        violations = check_negotiation_bounds(thread.original_offer or target, target, b).violations
        violations += self._check_outbound(run, quote, thread, draft["message"], target).violations
        if violations:
            thread.status = NegotiationStatus.ESCALATED
            self._emit(run, EventActor.AGENT, "negotiation.policy_blocked",
                       f"Draft to {sid} blocked: " + "; ".join(violations),
                       {"supplier_id": sid, "round": round_no, "offer": _json(target),
                        "violations": violations, "source": "agent_draft"})
            return False
        self._agent_finished(run, "negotiation", f"Draft ready: ask {target.unit_price}/unit, {target.lead_time_days} d",
                             {"supplier_id": sid, "round": round_no, "offer": _json(target)})
        self._transition(run, S.NEGOTIATION_DRAFTED, EventActor.AGENT, "negotiation.drafted",
                         f"Round {round_no} draft for {sid} passed the outbound policy filter",
                         {"supplier_id": sid, "round": round_no, "offer": _json(target), "draft": draft["message"]})
        run.pending_human = PendingHuman(
            kind=PendingHumanKind.NEGOTIATION_APPROVAL, quote_ids=[quote.quote_id],
            message=f"Approve round {round_no} negotiation message to {quote.supplier_name}",
            details={"supplier_id": sid, "round": round_no, "draft": draft["message"],
                     "target_offer": _json(target), "boundaries": _json(b)})
        self._transition(run, S.AWAITING_NEGOTIATION_APPROVAL, EventActor.ENGINE, "negotiation.awaiting_approval",
                         run.pending_human.message,
                         {"supplier_id": sid, "round": round_no, "offer": _json(target), "pending_human": _json(run.pending_human)})
        return True

    def _settle_round(self, run: Run, thread: NegotiationThread, quote: NormalizedQuote,
                      counter: NegotiationOffer | None, round_no: int) -> None:
        sid, b = thread.supplier_id, thread.boundaries
        self._agent_started(run, "negotiation", f"Evaluating {sid}'s round {round_no} reply",
                            {"supplier_id": sid, "round": round_no})
        verdict = self.agents["negotiation"].evaluate_counter(thread, counter, b)
        if verdict == "counter" and not can_open_turn(thread, b):  # G2: the agent cannot open a third turn
            verdict = "close"
        if verdict == "accept" and counter is None:
            verdict = "close"
        self._agent_finished(run, "negotiation", f"Verdict on {sid} round {round_no}: {verdict}",
                             {"supplier_id": sid, "round": round_no, "offer": _json(counter) if counter else None,
                              "verdict": verdict})
        self._emit(run, EventActor.ENGINE, "negotiation.round_completed",
                   f"{sid} round {round_no} completed: {verdict}"
                   + (f" ({buyer_turns(thread)}/{b.max_rounds} buyer turns used)" if verdict != "accept" else ""),
                   {"supplier_id": sid, "round": round_no, "offer": _json(counter) if counter else None,
                    "verdict": verdict, "buyer_turns": buyer_turns(thread)})
        if verdict == "counter":
            if self._draft_turn(run, thread, quote):
                return
            self._close_thread(run, thread, quote, round_no, NegotiationStatus.ESCALATED)
        elif verdict == "accept":
            self._close_thread(run, thread, quote, round_no, NegotiationStatus.ACCEPTED, counter)
        else:
            self._close_thread(run, thread, quote, round_no, NegotiationStatus.CLOSED)
        self._continue_negotiation(run)

    def _close_thread(self, run: Run, thread: NegotiationThread, quote: NormalizedQuote, round_no: int,
                      status: NegotiationStatus, applied: NegotiationOffer | None = None) -> None:
        """Settle the thread; apply `applied` (or, when closing, the best offer received if it
        improves on the original) as the quote's negotiated_offer."""
        sid = thread.supplier_id
        if applied is None:
            applied = self._best_received(thread)
        thread.status = status
        if applied is not None:
            thread.current_offer = applied
            idx = self._quote_index(run, quote.quote_id)
            run.quotes[idx] = quote.model_copy(update={"negotiated_offer": applied})
        original = thread.original_offer
        summary = f"{sid} thread {status}: " + (
            f"negotiated {applied.unit_price}/unit, {applied.lead_time_days} d "
            f"(was {original.unit_price}/unit, {original.lead_time_days} d)" if applied and original
            else "no improvement obtained; quoted terms stand")
        self._emit(run, EventActor.ENGINE, "negotiation.closed", summary,
                   {"supplier_id": sid, "round": round_no, "status": status, "offer": _json(applied) if applied else None,
                    "original_offer": _json(original) if original else None, "buyer_turns": buyer_turns(thread)})

    def _best_received(self, thread: NegotiationThread) -> NegotiationOffer | None:
        """Lowest price, then shortest lead time, among supplier offers that improve on the original."""
        original = thread.original_offer
        offers = [t.offer for t in thread.turns if t.role == NegotiationRole.SUPPLIER and t.offer is not None]
        if original is not None:
            offers = [o for o in offers if o.unit_price < original.unit_price or o.lead_time_days < original.lead_time_days]
        return min(offers, key=lambda o: (o.unit_price, o.lead_time_days), default=None)

    def _continue_negotiation(self, run: Run) -> None:
        """Next target supplier, or re-score once every target thread is settled."""
        for sid in self._negotiation_targets(run):
            if self._open_thread(run, sid):
                return
        self._rescore(run)

    def _rescore(self, run: Run) -> None:
        before = list(run.scorecards)
        negotiated = {q.supplier_id: _json(q.negotiated_offer) for q in run.quotes if q.negotiated_offer}
        self._transition(run, S.RE_SCORING, EventActor.ENGINE, "rescoring.started",
                         "Re-scoring with negotiated offers from " + (", ".join(negotiated) or "no supplier"),
                         {"negotiated": negotiated})
        self._score_and_recommend(run, self._profiles(run), before)

    def _check_outbound(self, run: Run, quote: NormalizedQuote, thread: NegotiationThread, text: str,
                        target: NegotiationOffer):
        """Leakage filter (G3): only this supplier's own figures and the target may appear."""
        profiles = self._profiles(run)
        for q in run.quotes:  # suppliers without history still must not be named
            profiles.setdefault(q.supplier_id, SupplierProfile(
                supplier_id=q.supplier_id, name=q.supplier_name, on_time_rate=0.0, defect_rate=0.0,
                orders_completed=0, avg_lead_time_days=0, max_capacity_units=0))
        allowed = {quote.unit_price, quote.shipping_cost, target.unit_price}
        if quote.llm_stated_total is not None:
            allowed.add(quote.llm_stated_total)
        for v in run.validated:
            if v.quote_id == quote.quote_id:
                allowed |= {v.subtotal, v.discount, v.pre_tax_total, v.tax, v.landed_cost}
        for offer in [thread.original_offer, thread.current_offer, *(t.offer for t in thread.turns)]:
            if offer is not None:
                allowed.add(offer.unit_price)
        return check_outbound_message(text, profiles[quote.supplier_id], list(profiles.values()), allowed)

    def _profiles(self, run: Run) -> dict[str, SupplierProfile]:
        profiles: dict[str, SupplierProfile] = {}
        for sid in sorted({q.supplier_id for q in run.quotes}):
            profile = self.agents["supplier_intel"].get_profile(sid)
            if profile:
                profiles[sid] = profile
        return profiles

    def _quote_for_supplier(self, run: Run, supplier_id: str) -> NormalizedQuote:
        for q in run.quotes:
            if q.supplier_id == supplier_id:
                return q
        raise WorkflowError("quote_not_found", f"no quote from supplier {supplier_id!r} in run {run.run_id}")

    # ------------------------------------------------------------------ stages

    def _extract(self, run: Run, doc: RawDocument) -> None:
        agent = "document"
        self._agent_started(run, agent, f"Extracting {doc.filename}", {"doc_id": doc.doc_id})
        try:
            quote = self.agents["document"].extract(doc)
        except Exception as exc:  # G6: parse failure → human form, never crash the run
            self._emit(run, EventActor.AGENT, "agent.failed", f"Document agent failed on {doc.filename}: {exc}",
                       {"agent": agent, "doc_id": doc.doc_id, "error": str(exc)})
            self._set_pending_extraction(run, {}, failed={doc.doc_id: str(exc)})
            return
        run.quotes = [q for q in run.quotes if q.quote_id != quote.quote_id] + [quote]
        self._agent_finished(run, agent, f"Extracted {quote.supplier_name}: {quote.quantity_quoted} × {quote.unit_price} {quote.currency}",
                             {"doc_id": doc.doc_id, "quote_id": quote.quote_id})
        low = self._low_confidence_fields(run, quote)
        self._emit(run, EventActor.AGENT, "quote.extracted",
                   f"{quote.quote_id}: {len(low)} critical field(s) below confidence threshold" if low
                   else f"{quote.quote_id}: all critical fields confident",
                   {"doc_id": doc.doc_id, "quote_id": quote.quote_id, "supplier_id": quote.supplier_id,
                    "field_confidence": quote.field_confidence, "low_confidence_fields": low, "quote": _json(quote)})

    def _finish_extraction(self, run: Run) -> None:
        """EXTRACTING/NEEDS_HUMAN_EXTRACTION → EXTRACTED when every quote is clean."""
        if self._check_extraction(run):
            self._transition(run, S.EXTRACTED, EventActor.ENGINE, "extraction.completed",
                             f"{len(run.quotes)} quote(s) extracted; ready for evaluation",
                             {"quote_ids": [q.quote_id for q in run.quotes]})

    def _quote_id_for_document(self, run: Run, doc_id: str) -> str | None:
        return next((q.quote_id for q in run.quotes if q.doc_id == doc_id), None)

    def _low_confidence_fields(self, run: Run, quote: NormalizedQuote) -> list[str]:
        threshold = run.config.thresholds.min_confidence
        return [f for f in CRITICAL_FIELDS if quote.field_confidence.get(f, 0.0) < threshold]

    def _check_extraction(self, run: Run) -> bool:
        """True if every quote is clean; otherwise sets NEEDS_HUMAN_EXTRACTION."""
        low = {q.quote_id: fields for q in run.quotes if (fields := self._low_confidence_fields(run, q))}
        failed = run.pending_human.details.get("failed_documents", {}) if run.pending_human else {}
        if not low and not failed:
            run.pending_human = None
            return True
        self._set_pending_extraction(run, low, failed)
        return False

    def _set_pending_extraction(self, run: Run, low: dict[str, list[str]], failed: dict[str, str]) -> None:
        prev = run.pending_human
        merged_failed = {**(prev.details.get("failed_documents", {}) if prev else {}), **failed}
        run.pending_human = PendingHuman(
            kind=PendingHumanKind.EXTRACTION,
            quote_ids=sorted(low),
            message="Confirm low-confidence critical fields" + (" and re-upload failed documents" if merged_failed else ""),
            details={"fields": low, "failed_documents": merged_failed},
        )
        if run.state != S.NEEDS_HUMAN_EXTRACTION:
            self._transition(run, S.NEEDS_HUMAN_EXTRACTION, EventActor.ENGINE, "extraction.needs_human",
                             run.pending_human.message, _json(run.pending_human))

    def _evaluate(self, run: Run) -> None:
        # VALIDATING: cost + checks (no profiles yet)
        self._transition(run, S.VALIDATING, EventActor.ENGINE, "validation.started",
                         f"Validating {len(run.quotes)} quote(s) at {run.request.quantity:,} units"
                         + (f" (request v{run.request.version})" if run.request.version > 1 else ""))
        # After a replan the human already resolved every mismatch (G1); the confirmed totals are authoritative
        # and must not re-trigger the gate at the new quantity.
        replan = self._replans.get(run.run_id)
        result = self.engine(run.request, run.config, run.quotes, {},
                             include_math_mismatch=bool(replan and replan.math_resolved))
        run.validated = result.validated
        self._emit(run, EventActor.ENGINE, "quotes.validated",
                   "; ".join(f"{v.supplier_id} landed {v.landed_cost}" for v in run.validated),
                   {"validated": _json(run.validated)})
        if result.stopped_for_math_mismatch:
            details = {
                v.quote_id: {"supplier_id": v.supplier_id, "computed_pre_tax_total": str(self._quoted_total(v)),
                             "stated_total": str(v.llm_stated_total)}
                for v in run.validated if v.quote_id in result.stopped_for_math_mismatch
            }
            run.pending_human = PendingHuman(
                kind=PendingHumanKind.CALC_MISMATCH, quote_ids=list(details),
                message="Stated totals do not match computed totals; confirm before scoring", details=details)
            self._transition(run, S.CALC_MISMATCH, EventActor.ENGINE, "calc.mismatch",
                             "Calculation mismatch on " + ", ".join(
                                 f"{d['supplier_id']} (computed {d['computed_pre_tax_total']} vs stated {d['stated_total']})"
                                 for d in details.values()),
                             _json(run.pending_human))
            return

        # ENRICHING: supplier history
        self._transition(run, S.ENRICHING, EventActor.ENGINE, "enrichment.started", "Fetching supplier history")
        profiles: dict[str, SupplierProfile] = {}
        for sid in sorted({q.supplier_id for q in run.quotes}):
            self._agent_started(run, "supplier_intel", f"Looking up {sid}", {"supplier_id": sid})
            profile = self.agents["supplier_intel"].get_profile(sid)
            if profile:
                profiles[sid] = profile
                self._agent_finished(run, "supplier_intel",
                                     f"{profile.name}: on-time {profile.on_time_rate:.0%}, defects {profile.defect_rate:.1%}"
                                     + (", BLACKLISTED" if profile.blacklisted else ""),
                                     {"supplier_id": sid, "profile": _json(profile)})
            else:
                self._agent_finished(run, "supplier_intel", f"No history for {sid}", {"supplier_id": sid, "profile": None})

        # SCORING
        self._transition(run, S.SCORING, EventActor.ENGINE, "scoring.started", "Scoring eligible quotes")
        self._score_and_recommend(run, profiles)

    def _score_and_recommend(self, run: Run, profiles: dict[str, SupplierProfile],
                             before: list[Scorecard] | None = None) -> None:
        """engine → quotes.scored → RECOMMENDED → decision agent explanation. `before` (previous
        scorecards) turns on the engine diff and its change_explanation (re-score / replan)."""
        replan = self._replans.get(run.run_id)
        if before is None and replan is not None:
            before = replan.scorecards or None
        result = self.engine(run.request, run.config, run.quotes, profiles,
                             include_math_mismatch=bool(replan and replan.math_resolved))
        run.validated, run.scorecards = result.validated, result.scorecards
        ranked = [c.supplier_id for c in run.scorecards]
        self._emit(run, EventActor.ENGINE, "quotes.scored",
                   "Ranking: " + ", ".join(f"{c.supplier_id} {c.total_score:.1f}" + ("" if c.eligible else " (ineligible)")
                                           for c in run.scorecards),
                   {"scorecards": _json(run.scorecards), "ranked": ranked})

        # RECOMMENDED + explanation
        top = recommended_id(run.scorecards)
        self._transition(run, S.RECOMMENDED, EventActor.ENGINE, "recommendation.ranked",
                         f"Recommended supplier: {top or 'none eligible'}", {"recommended_supplier_id": top, "ranked": ranked})
        diff_lines = explain_diff(before, run.scorecards) if before is not None else None
        if replan is not None:  # lead the change explanation with what the human changed
            diff_lines = [self._changes_line(replan)] + (diff_lines or [])
        self._agent_started(run, "decision", "Explaining the ranking" + (" and what changed" if diff_lines else ""))
        explanation = self.agents["decision"].explain(run.request, run.scorecards, run.validated, diff_lines)
        run.recommendation = Recommendation(
            run_id=run.run_id, request_version=run.request.version, ranked=ranked, recommended_supplier_id=top,
            rationale=explanation["rationale"], change_explanation=explanation["change_explanation"])
        self._agent_finished(run, "decision", explanation["rationale"][:160],
                             {"recommendation": _json(run.recommendation), "diff": diff_lines})
        self._emit(run, EventActor.ENGINE, "recommendation.ready", f"Recommendation stored for {top or 'no supplier'}",
                   {"recommendation": _json(run.recommendation)})
        if replan is not None:
            del self._replans[run.run_id]
            self._finish_replan(run, replan)

    # ------------------------------------------------------------------ helpers

    def _require(self, run: Run, allowed: set[WorkflowState], action: str) -> None:
        if run.state not in allowed:
            raise WorkflowError("illegal_transition",
                                f"{action} not allowed in state {run.state}; expected one of {sorted(s.value for s in allowed)}")

    @staticmethod
    def _quoted_total(quote: NormalizedQuote) -> Decimal:
        """Engine pre-tax total for the quantity and unit price the document itself quotes (what G1 compares)."""
        return pre_tax_total(quote, quote.quantity_quoted, unit_price=quote.unit_price)[2]

    def _quote_index(self, run: Run, quote_id: str) -> int:
        for i, q in enumerate(run.quotes):
            if q.quote_id == quote_id:
                return i
        raise WorkflowError("quote_not_found", f"unknown quote_id {quote_id!r} in run {run.run_id}")

    def _transition(self, run: Run, new: WorkflowState, actor: EventActor, type: str, summary: str,
                    payload: dict[str, Any] | None = None) -> None:
        before = run.state
        run.state = new
        run.updated_at = self.now()
        self.events.emit(run, actor, type, summary, payload, state_before=before, state_after=new)

    def _emit(self, run: Run, actor: EventActor, type: str, summary: str, payload: dict[str, Any] | None = None,
              state_after: WorkflowState | None = None) -> None:
        run.updated_at = self.now()
        self.events.emit(run, actor, type, summary, payload, state_before=None if state_after else run.state,
                         state_after=state_after or run.state)

    def _agent_started(self, run: Run, agent: str, summary: str, payload: dict[str, Any] | None = None) -> None:
        self._emit(run, EventActor.AGENT, "agent.started", summary, {"agent": agent, **(payload or {})})

    def _agent_finished(self, run: Run, agent: str, summary: str, payload: dict[str, Any] | None = None) -> None:
        self._emit(run, EventActor.AGENT, "agent.finished", summary, {"agent": agent, **(payload or {})})

    def _save(self, run: Run) -> Run:
        return self.store.put(run)
