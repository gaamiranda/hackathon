"""Deterministic engine: the only place business arithmetic lives (PLAN.md D2).

    result = evaluate(request, config, quotes, profiles)
"""

from pydantic import BaseModel

from procureai.domain.models import (
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    Scorecard,
    SupplierProfile,
    ValidatedQuote,
)
from procureai.engine.costing import compute_costs
from procureai.engine.diff import explain_diff, recommended_id
from procureai.engine.policy import (
    PolicyResult,
    can_open_turn,
    check_negotiation_bounds,
    check_outbound_message,
)
from procureai.engine.scoring import score


class EvaluationResult(BaseModel):
    validated: list[ValidatedQuote]
    scorecards: list[Scorecard]
    stopped_for_math_mismatch: list[str]  # quote_ids excluded from scoring pending human review


def evaluate(
    request: ProcurementRequest,
    config: ProcurementConfig,
    quotes: list[NormalizedQuote],
    profiles: dict[str, SupplierProfile],
    include_math_mismatch: bool = False,
) -> EvaluationResult:
    """compute_costs() every quote, then score(). Quotes failing math_ok are
    listed in stopped_for_math_mismatch and left out of scorecards unless
    include_math_mismatch (i.e. after a human confirmed the numbers)."""
    validated = [compute_costs(q, request, config) for q in quotes]
    stopped = [] if include_math_mismatch else [vq.quote_id for vq in validated if not vq.checks.math_ok]
    cards = score(validated, profiles, request, config, include_math_mismatch=include_math_mismatch)
    return EvaluationResult(validated=validated, scorecards=cards, stopped_for_math_mismatch=stopped)


__all__ = [
    "EvaluationResult",
    "PolicyResult",
    "can_open_turn",
    "check_negotiation_bounds",
    "check_outbound_message",
    "compute_costs",
    "evaluate",
    "explain_diff",
    "recommended_id",
    "score",
]
