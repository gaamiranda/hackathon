"""Deterministic mock agents backed by fixtures (PLAN.md D3). No LLM, no network."""

import json
from decimal import Decimal
from pathlib import Path

from procureai.agents.base import CounterVerdict, Explanation, NegotiationDraft
from procureai.domain.models import (
    NegotiationBoundaries,
    NegotiationOffer,
    NegotiationRole,
    NegotiationThread,
    NormalizedQuote,
    ProcurementRequest,
    RawDocument,
    Scorecard,
    ScoringWeights,
    SupplierProfile,
    ValidatedQuote,
)
from procureai.engine.costing import effective_lead_time_days, effective_unit_price, q2
from procureai.engine.policy import buyer_turns, can_open_turn

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
SUPPLIER_HISTORY = DATA_DIR / "supplier_history.json"


# Demo knob: a filename containing this token gets low confidence on these fields (→ NEEDS_HUMAN_EXTRACTION, G6).
LOWCONF_TOKEN = "lowconf"
LOWCONF_FIELDS = ("unit_price", "lead_time_days")
LOWCONF_VALUE = 0.5


class MockDocumentAgent:
    """Returns data/synthetic/<filename>.expected.json for a known filename.

    Scenario A's ground truths sit in data/synthetic/, scenario B's (T22) in data/synthetic/scenario_b/;
    both directories are searched, so a filename alone selects the fixture regardless of scenario.

    `<name>_lowconf<ext>` (e.g. supplier_c_lowconf.eml.txt) maps to the matching supplier's ground truth
    with `unit_price` and `lead_time_days` confidence lowered to 0.5, to demo the extraction gate."""

    def __init__(self, synthetic_dir: Path = SYNTHETIC_DIR) -> None:
        self.synthetic_dir = synthetic_dir
        # Root first: a name present in both directories resolves to scenario A.
        self.search_dirs = [d for d in (synthetic_dir, *sorted(p for p in synthetic_dir.glob("*") if p.is_dir())) if d.is_dir()]

    def extract(self, doc: RawDocument) -> NormalizedQuote:
        name = Path(doc.filename).name
        lowconf = LOWCONF_TOKEN in name
        expected = self._ground_truth(name)
        if expected is None:
            known = sorted(p.name.removesuffix(".expected.json")
                           for d in self.search_dirs for p in d.glob("*.expected.json"))
            raise FileNotFoundError(
                f"MockDocumentAgent has no ground truth for '{doc.filename}'. Known files: {known}"
            )
        quote = NormalizedQuote.model_validate_json(expected.read_text())
        confidence = quote.field_confidence
        if lowconf:
            confidence = {**confidence, **{f: LOWCONF_VALUE for f in LOWCONF_FIELDS}}
        return quote.model_copy(update={"doc_id": doc.doc_id, "field_confidence": confidence})

    def _ground_truth(self, name: str) -> Path | None:
        for directory in self.search_dirs:
            found = self._ground_truth_in(directory, name)
            if found is not None:
                return found
        return None

    @staticmethod
    def _ground_truth_in(directory: Path, name: str) -> Path | None:
        direct = directory / f"{name}.expected.json"
        if direct.exists():
            return direct
        if LOWCONF_TOKEN not in name:
            return None
        # supplier_c_cobalt_lowconf.eml.txt → supplier_c_cobalt.eml.txt; supplier_c_lowconf.eml.txt → supplier_c_*.eml.txt
        stripped = directory / f"{name.replace('_' + LOWCONF_TOKEN, '').replace(LOWCONF_TOKEN, '')}.expected.json"
        if stripped.exists():
            return stripped
        prefix, _, suffix = name.partition(LOWCONF_TOKEN)
        matches = sorted(directory.glob(f"{prefix}*{suffix}.expected.json"))
        return matches[0] if len(matches) == 1 else None


class MockSupplierIntelAgent:
    """Reads the read-only seed data/supplier_history.json."""

    def __init__(self, history_path: Path = SUPPLIER_HISTORY) -> None:
        rows = json.loads(history_path.read_text())
        self._profiles = {p.supplier_id: p for p in (SupplierProfile.model_validate(r) for r in rows)}

    def get_profile(self, supplier_id: str) -> SupplierProfile | None:
        return self._profiles.get(supplier_id)


class MockDecisionAgent:
    """Templated, deterministic rationale built from engine output only."""

    def explain(
        self,
        request: ProcurementRequest,
        scorecards: list[Scorecard],
        validated: list[ValidatedQuote],
        diff_lines: list[str] | None = None,
        *,
        profiles: dict[str, SupplierProfile] | None = None,  # unused: the template only repeats engine output
        weights: ScoringWeights | None = None,
        before: list[Scorecard] | None = None,
    ) -> Explanation:
        names = {vq.supplier_id: vq.supplier_name for vq in validated}
        eligible = [c for c in scorecards if c.eligible]
        parts: list[str] = []

        if eligible:
            top = eligible[0]
            bd = ", ".join(f"{k} {v:.1f}" for k, v in top.score_breakdown.items())
            parts.append(
                f"Recommended: {names.get(top.supplier_id, top.supplier_id)} ({top.supplier_id}) with "
                f"{top.total_score:.1f}/100 (breakdown: {bd}). Landed cost {top.landed_cost:,.2f} "
                f"{request.currency} for {request.quantity:,} units, lead time {top.lead_time_days} days."
            )
            for c in eligible[1:]:
                parts.append(
                    f"{names.get(c.supplier_id, c.supplier_id)} scored {c.total_score:.1f} "
                    f"(landed cost {c.landed_cost:,.2f}, {c.lead_time_days} days)."
                )
        else:
            parts.append("No eligible supplier: every quote failed at least one policy check.")

        for c in scorecards:
            if not c.eligible:
                parts.append(f"{names.get(c.supplier_id, c.supplier_id)} is ineligible: {'; '.join(c.ineligibility_reasons)}.")

        change = " ".join(diff_lines) if diff_lines else None
        return Explanation(rationale=" ".join(parts), change_explanation=change)


class MockNegotiationAgent:
    """Templated drafts and a fixed accept rule (D17). Sees one supplier's quote and thread only."""

    LEAD_TIME_ASK_DAYS = 2

    def draft(
        self,
        request: ProcurementRequest,
        quote: NormalizedQuote,
        thread: NegotiationThread,
        boundaries: NegotiationBoundaries,
    ) -> NegotiationDraft:
        original = thread.original_offer or NegotiationOffer(
            unit_price=effective_unit_price(quote), lead_time_days=effective_lead_time_days(quote))
        current = thread.current_offer or original
        ask = q2(current.unit_price * (1 - boundaries.default_ask_pct / Decimal(100)))
        floor = q2(original.unit_price * (1 - boundaries.max_discount_ask_pct / Decimal(100)))
        target = NegotiationOffer(
            unit_price=min(current.unit_price, max(ask, floor)),
            lead_time_days=min(current.lead_time_days,
                               max(boundaries.min_lead_time_days, current.lead_time_days - self.LEAD_TIME_ASK_DAYS)),
        )
        cur = request.currency
        if buyer_turns(thread) == 0:
            opening = (
                f"Thank you for quotation {quote.quote_id} for {request.quantity:,} units of {request.product} at "
                f"{cur} {current.unit_price} per unit with a {current.lead_time_days}-day lead time. "
                f"We would like to place this order with you, but to fit our plan we need a unit price of "
                f"{cur} {target.unit_price} and delivery within {target.lead_time_days} days."
            )
        else:
            opening = (
                f"Thank you for your revised offer of {cur} {current.unit_price} per unit with a "
                f"{current.lead_time_days}-day lead time; we appreciate the movement. To close this order today "
                f"we would need {cur} {target.unit_price} per unit and delivery within {target.lead_time_days} days."
            )
        message = (
            f"Dear {quote.supplier_name} team,\n\n{opening} Could you confirm whether you can meet these terms? "
            "We are ready to confirm the order promptly on your reply.\n\nKind regards,\nProcurement Team"
        )
        return NegotiationDraft(message=message, target_offer=target)

    def evaluate_counter(
        self,
        thread: NegotiationThread,
        counter: NegotiationOffer | None,
        boundaries: NegotiationBoundaries,
    ) -> CounterVerdict:
        """accept if the counter improves the price by ≥ half the ask or meets the lead-time target;
        counter while another buyer turn is allowed; at the limit accept any price improvement,
        else close (supplier rejected). Offers only — reply text is never read (G4)."""
        _, baseline = self._last_ask(thread)
        if self.meets_acceptance_rule(thread, counter):
            return "accept"
        if can_open_turn(thread, boundaries):
            return "counter"
        if counter is not None and counter.unit_price < baseline.unit_price:
            return "accept"
        return "close"

    @classmethod
    def meets_acceptance_rule(cls, thread: NegotiationThread, counter: NegotiationOffer | None) -> bool:
        """D17 acceptance rule on its own: the counter takes at least half the price we asked for, or
        meets the lead time we asked for. The live agent uses it to bound what the LLM may decide."""
        target, baseline = cls._last_ask(thread)
        if counter is None or target is None:
            return False
        half_ask = (baseline.unit_price - target.unit_price) / 2
        return (baseline.unit_price - counter.unit_price >= half_ask
                or counter.lead_time_days <= target.lead_time_days)

    @staticmethod
    def _last_ask(thread: NegotiationThread) -> tuple[NegotiationOffer | None, NegotiationOffer]:
        """(target of the last buyer turn, the supplier offer standing when that ask was made)."""
        baseline = thread.original_offer or NegotiationOffer(unit_price=Decimal(0), lead_time_days=0)
        last_buyer = next((i for i in range(len(thread.turns) - 1, -1, -1)
                           if thread.turns[i].role == NegotiationRole.BUYER), None)
        if last_buyer is None:
            return None, baseline
        for turn in thread.turns[:last_buyer]:
            if turn.role == NegotiationRole.SUPPLIER and turn.offer is not None:
                baseline = turn.offer
        return thread.turns[last_buyer].offer, baseline
