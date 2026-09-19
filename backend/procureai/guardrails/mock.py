"""Deterministic mock judge (PLAN.md D3, D20). No network; the pipeline behaves identically to the live judge
on the demo fixtures so `just test` and the mock demo exercise every guardrail event."""

from collections.abc import Iterable
from decimal import Decimal

from procureai.agents.base import CRITICAL_FIELDS
from procureai.domain.models import NormalizedQuote, SupplierProfile
from procureai.engine.policy import check_outbound_message
from procureai.guardrails.base import ExtractionVerdict, FieldVerdict, InjectionVerdict, LeakVerdict
from procureai.guardrails.questions import LEAK_REASONS

SUPPORTED_P = 0.99
UNSUPPORTED_P = 0.3
LOW_CONFIDENCE = 0.85  # mirrors the fixture's thresholds.min_confidence: a field the agent doubts, the judge doubts
LEAK_P = 0.95
NO_LEAK_P = 0.05
INJECTION_P = 0.98
NO_INJECTION_P = 0.02
INJECTION_MARKERS = ("ignore previous instructions", "procurement system")


class MockGuardrailJudge:
    def verify_extraction(self, doc_text: str, quote: NormalizedQuote) -> ExtractionVerdict:
        fields = {}
        for field in CRITICAL_FIELDS:
            low = quote.field_confidence.get(field, 0.0) < LOW_CONFIDENCE
            fields[field] = FieldVerdict(supported=not low, probability=UNSUPPORTED_P if low else SUPPORTED_P)
        return ExtractionVerdict(fields=fields)

    def check_outbound(
        self,
        message: str,
        own_supplier_name: str,
        other_supplier_names: list[str],
        *,
        allowed_amounts: Iterable[Decimal] = (),
    ) -> LeakVerdict:
        """Reuses the deterministic filter (engine.policy) over name-only profiles: another supplier's name,
        or a price-like amount outside the allowed set, is a leak."""
        own = _profile("own", own_supplier_name)
        others = [_profile(f"other-{i}", name) for i, name in enumerate(other_supplier_names)]
        result = check_outbound_message(message, own, [own, *others], set(allowed_amounts))
        reasons: list[str] = []
        if any(v.startswith("mentions other supplier") for v in result.violations):
            reasons.append(LEAK_REASONS["names_competitor"])
        if any(v.startswith("amount ") for v in result.violations):
            reasons.append(LEAK_REASONS["reveals_other_terms"])
        return LeakVerdict(leaks=bool(reasons), probability=LEAK_P if reasons else NO_LEAK_P, reasons=reasons)

    def detect_injection(self, text: str) -> InjectionVerdict:
        lowered = text.lower()
        hit = any(marker in lowered for marker in INJECTION_MARKERS)
        return InjectionVerdict(injection=hit, probability=INJECTION_P if hit else NO_INJECTION_P)


def _profile(supplier_id: str, name: str) -> SupplierProfile:
    return SupplierProfile(supplier_id=supplier_id, name=name, on_time_rate=0.0, defect_rate=0.0,
                           orders_completed=0, avg_lead_time_days=0, max_capacity_units=0)
