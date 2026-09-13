"""Week 1 straight-line pipeline (PLAN.md §2 state machine, §17 gates G1/G6).

CREATED → EXTRACTING → [NEEDS_HUMAN_EXTRACTION] → VALIDATING → [CALC_MISMATCH]
        → ENRICHING → SCORING → RECOMMENDED

The orchestrator owns state and calls agents/engine; it never does arithmetic.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from procureai.agents.base import CRITICAL_FIELDS, AgentSet
from procureai.domain.models import (
    EventActor,
    NormalizedQuote,
    PendingHuman,
    PendingHumanKind,
    ProcurementConfig,
    ProcurementRequest,
    RawDocument,
    Recommendation,
    Run,
    SupplierProfile,
    WorkflowState,
)
from procureai.engine import EvaluationResult, evaluate, recommended_id
from procureai.workflow.errors import WorkflowError
from procureai.workflow.events import EventBus
from procureai.workflow.store import RunStore

S = WorkflowState
EngineFn = Callable[..., EvaluationResult]


def _json(model_or_list: Any) -> Any:
    if isinstance(model_or_list, list):
        return [m.model_dump(mode="json") for m in model_or_list]
    return model_or_list.model_dump(mode="json")


class Orchestrator:
    def __init__(
        self,
        agents: AgentSet,
        store: RunStore,
        events: EventBus | None = None,
        engine: EngineFn = evaluate,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.agents = agents
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.events = events or EventBus(store, now=self.now)
        self.engine = engine
        self.new_id = id_factory or (lambda: f"run-{uuid4().hex[:8]}")

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
        """Extract every document. Ends in EXTRACTING (clean, ready for run_evaluation)
        or NEEDS_HUMAN_EXTRACTION (low-confidence / missing critical fields, G6)."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.CREATED, S.EXTRACTING}, "add_documents")
            if not docs:
                raise WorkflowError("no_documents", "add_documents needs at least one document")
            run.documents.extend(docs)
            self._transition(run, S.EXTRACTING, EventActor.ENGINE, "documents.added",
                             f"{len(docs)} document(s) queued for extraction",
                             {"documents": [{"doc_id": d.doc_id, "filename": d.filename, "source": d.source} for d in docs]})
            for doc in docs:
                self._extract(run, doc)
            self._check_extraction(run)
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
                self._transition(run, S.EXTRACTING, EventActor.HUMAN, "extraction.resumed",
                                 "All critical fields confirmed; resuming")
                self._evaluate(run)
            return self._save(run)

    def run_evaluation(self, run_id: str) -> Run:
        """VALIDATING → (CALC_MISMATCH stop) → ENRICHING → SCORING → RECOMMENDED."""
        with self.store.lock:
            run = self.store.get(run_id)
            self._require(run, {S.EXTRACTING}, "run_evaluation")
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
            computed = next(v.pre_tax_total for v in run.validated if v.quote_id == quote_id)
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
                self._transition(run, S.EXTRACTING, EventActor.HUMAN, "mismatch.resolved",
                                 "All calculation mismatches resolved; resuming")
                self._evaluate(run)
            return self._save(run)

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
                         f"Validating {len(run.quotes)} quote(s) at {run.request.quantity:,} units")
        result = self.engine(run.request, run.config, run.quotes, {})
        run.validated = result.validated
        self._emit(run, EventActor.ENGINE, "quotes.validated",
                   "; ".join(f"{v.supplier_id} landed {v.landed_cost}" for v in run.validated),
                   {"validated": _json(run.validated)})
        if result.stopped_for_math_mismatch:
            details = {
                v.quote_id: {"supplier_id": v.supplier_id, "computed_pre_tax_total": str(v.pre_tax_total),
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
        result = self.engine(run.request, run.config, run.quotes, profiles)
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
        self._agent_started(run, "decision", "Explaining the ranking")
        explanation = self.agents["decision"].explain(run.request, run.scorecards, run.validated, None)
        run.recommendation = Recommendation(
            run_id=run.run_id, request_version=run.request.version, ranked=ranked, recommended_supplier_id=top,
            rationale=explanation["rationale"], change_explanation=explanation["change_explanation"])
        self._agent_finished(run, "decision", explanation["rationale"][:160], {"recommendation": _json(run.recommendation)})
        self._emit(run, EventActor.ENGINE, "recommendation.ready", f"Recommendation stored for {top or 'no supplier'}",
                   {"recommendation": _json(run.recommendation)})

    # ------------------------------------------------------------------ helpers

    def _require(self, run: Run, allowed: set[WorkflowState], action: str) -> None:
        if run.state not in allowed:
            raise WorkflowError("illegal_transition",
                                f"{action} not allowed in state {run.state}; expected one of {sorted(s.value for s in allowed)}")

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
