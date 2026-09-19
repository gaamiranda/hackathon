"""GuardrailJudge protocol and verdict types (PLAN.md D20).

Every method returns a pydantic verdict. A verdict with `evaluated=False` (probabilities None, flags False)
means the judge could not answer — SDK error, network, cache miss in replay_only, budget exhausted — and the
caller must proceed exactly as if no judge were configured. Implementations never raise into the orchestrator.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from procureai.domain.models import NormalizedQuote


class FieldVerdict(BaseModel):
    supported: bool
    probability: float | None


class ExtractionVerdict(BaseModel):
    """Per critical field: P(the document states the extracted value)."""

    fields: dict[str, FieldVerdict] = {}
    evaluated: bool = True
    error: str | None = None

    @property
    def unsupported(self) -> list[str]:
        return [f for f, v in self.fields.items() if not v.supported]

    @property
    def lowest_probability(self) -> float | None:
        probs = [v.probability for v in self.fields.values() if v.probability is not None]
        return min(probs) if probs else None


class LeakVerdict(BaseModel):
    """Does an outbound message reveal another supplier's prices, terms or identity?"""

    leaks: bool = False
    probability: float | None = None
    reasons: list[str] = []
    evaluated: bool = True
    error: str | None = None


class InjectionVerdict(BaseModel):
    """Does untrusted text carry instructions aimed at an AI/automated system rather than a human reader?"""

    injection: bool = False
    probability: float | None = None
    evaluated: bool = True
    error: str | None = None


def not_evaluated(kind: type[ExtractionVerdict] | type[LeakVerdict] | type[InjectionVerdict], error: str):
    return kind(evaluated=False, error=error)


@runtime_checkable
class GuardrailJudge(Protocol):
    def verify_extraction(self, doc_text: str, quote: NormalizedQuote) -> ExtractionVerdict:
        """One question per CRITICAL_FIELD on the quote, batched in one call with the document as state."""
        ...

    def check_outbound(
        self,
        message: str,
        own_supplier_name: str,
        other_supplier_names: list[str],
        *,
        allowed_amounts: Iterable[Decimal] = (),
    ) -> LeakVerdict:
        """Semantic leakage check on top of the regex filter (G3). `allowed_amounts` are this supplier's own
        figures (the regex filter's allowed set); the mock uses them, the live judge reads the message."""
        ...

    def detect_injection(self, text: str) -> InjectionVerdict:
        """Instructions addressed to an AI system / procurement software rather than a human reader (G4)."""
        ...
