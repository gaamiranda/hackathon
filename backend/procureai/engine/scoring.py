"""Deterministic eligibility, dimension scores and weighted ranking (PLAN.md §4, D2).

Dimension scores are 0–100; score_breakdown holds weight × dimension score and
sums to total_score. Ineligible quotes score 0 but keep their landed cost.
"""

from procureai.domain.models import (
    ProcurementConfig,
    ProcurementRequest,
    Scorecard,
    SupplierProfile,
    ValidatedQuote,
)

# Risk normalisation constants (see risk_score docstring).
ON_TIME_FLOOR = 0.80  # on-time rate at/below this = maximum delivery risk
CAPACITY_COMFORT = 0.80  # utilisation at/below this adds no capacity risk


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def reliability_score(profile: SupplierProfile) -> float:
    """reliability (0..1) = 0.5 × on_time_rate + 0.5 × (1 − defect_rate)."""
    return 0.5 * profile.on_time_rate + 0.5 * (1.0 - profile.defect_rate)


def risk_score(profile: SupplierProfile, quantity: int, config: ProcurementConfig) -> float:
    """risk (0 = none, 1 = max), deterministic from history and capacity headroom.

    defect_risk   = min(defect_rate / thresholds.max_defect_rate, 1)
    delivery_risk = min((1 − on_time_rate) / (1 − ON_TIME_FLOOR), 1)      # 80% on-time → 1
    base          = 0.5 × defect_risk + 0.5 × delivery_risk
    utilisation   = quantity / max_capacity_units                           # 1 if capacity is 0
    capacity_risk = clamp((utilisation − CAPACITY_COMFORT) / (1 − CAPACITY_COMFORT), 0, 1)
    risk          = base + (1 − base) × capacity_risk

    Capacity acts as an amplifier: orders using ≤ 80% of a supplier's capacity add
    no risk; between 80% and 100% the remaining risk headroom fills up linearly.
    """
    max_defect = config.thresholds.max_defect_rate or 1e-9
    defect_risk = _clamp01(profile.defect_rate / max_defect)
    delivery_risk = _clamp01((1.0 - profile.on_time_rate) / (1.0 - ON_TIME_FLOOR))
    base = 0.5 * defect_risk + 0.5 * delivery_risk
    utilisation = quantity / profile.max_capacity_units if profile.max_capacity_units else 1.0
    capacity_risk = _clamp01((utilisation - CAPACITY_COMFORT) / (1.0 - CAPACITY_COMFORT))
    return _clamp01(base + (1.0 - base) * capacity_risk)


def _min_max(value: float, lo: float, hi: float) -> float:
    """100 for lo, 0 for hi, linear in between; 100 when lo == hi (single candidate)."""
    if hi == lo:
        return 100.0
    return 100.0 * (hi - value) / (hi - lo)


def ineligibility_reasons(
    vq: ValidatedQuote,
    profile: SupplierProfile | None,
    request: ProcurementRequest,
    config: ProcurementConfig,
) -> list[str]:
    """Why a quote cannot be recommended; empty list = eligible.

    Ineligible if any of moq_ok / lead_time_ok / budget_ok / capacity is False,
    the profile is missing, the supplier is blacklisted, or
    defect_rate > thresholds.max_defect_rate.
    """
    reasons: list[str] = []
    if not vq.checks.moq_ok:
        reasons.append(f"below MOQ {vq.moq}")
    if not vq.checks.lead_time_ok:
        reasons.append(f"lead time {vq.lead_time_days} d misses the deadline")
    if not vq.checks.budget_ok:
        reasons.append(f"landed cost {vq.landed_cost} over budget {request.budget}")
    if not vq.checks.capacity_ok:
        reasons.append(f"quantity {request.quantity} exceeds capacity {vq.capacity_units}")
    if profile is None:
        reasons.append("no supplier history on record")
    else:
        if profile.blacklisted:
            reasons.append("supplier is blacklisted")
        if profile.defect_rate > config.thresholds.max_defect_rate:
            reasons.append(
                f"defect rate {profile.defect_rate:.1%} exceeds {config.thresholds.max_defect_rate:.1%}"
            )
    return reasons


def score(
    validated: list[ValidatedQuote],
    profiles: dict[str, SupplierProfile],
    request: ProcurementRequest,
    config: ProcurementConfig,
    include_math_mismatch: bool = False,
) -> list[Scorecard]:
    """Score and rank quotes; returns Scorecards sorted by total_score desc, then supplier_id.

    Quotes with checks.math_ok == False are dropped unless include_math_mismatch
    (the workflow stops them for human review, G1).

    For eligible quotes:
      price       = min-max over eligible landed_cost (cheapest → 100)
      lead_time   = min-max over eligible lead_time_days (fastest → 100)
      reliability = reliability_score × 100
      risk        = (1 − risk_score) × 100
      breakdown[d] = round(weights[d] × dimension_d, 2);  total = Σ breakdown
    Ineligible quotes: total_score 0, empty breakdown, reasons listed.
    """
    candidates = [vq for vq in validated if vq.checks.math_ok or include_math_mismatch]
    weights = config.weights

    rows: list[tuple[ValidatedQuote, SupplierProfile | None, list[str]]] = [
        (vq, profiles.get(vq.supplier_id), ineligibility_reasons(vq, profiles.get(vq.supplier_id), request, config))
        for vq in candidates
    ]
    eligible = [vq for vq, _, reasons in rows if not reasons]
    costs = [float(vq.landed_cost) for vq in eligible]
    leads = [float(vq.lead_time_days) for vq in eligible]
    cost_lo, cost_hi = (min(costs), max(costs)) if costs else (0.0, 0.0)
    lead_lo, lead_hi = (min(leads), max(leads)) if leads else (0.0, 0.0)

    cards: list[Scorecard] = []
    for vq, profile, reasons in rows:
        rel = reliability_score(profile) if profile else 0.0
        risk = risk_score(profile, request.quantity, config) if profile else 1.0
        if reasons:
            cards.append(
                Scorecard(
                    supplier_id=vq.supplier_id,
                    landed_cost=vq.landed_cost,
                    lead_time_days=vq.lead_time_days,
                    reliability_score=rel,
                    risk_score=risk,
                    total_score=0.0,
                    score_breakdown={},
                    eligible=False,
                    ineligibility_reasons=reasons,
                )
            )
            continue

        dims = {
            "price": _min_max(float(vq.landed_cost), cost_lo, cost_hi),
            "lead_time": _min_max(float(vq.lead_time_days), lead_lo, lead_hi),
            "reliability": rel * 100.0,
            "risk": (1.0 - risk) * 100.0,
        }
        breakdown = {
            "price": round(weights.price * dims["price"], 2),
            "lead_time": round(weights.lead_time * dims["lead_time"], 2),
            "reliability": round(weights.reliability * dims["reliability"], 2),
            "risk": round(weights.risk * dims["risk"], 2),
        }
        total = round(sum(breakdown.values()), 2)
        cards.append(
            Scorecard(
                supplier_id=vq.supplier_id,
                landed_cost=vq.landed_cost,
                lead_time_days=vq.lead_time_days,
                reliability_score=round(rel, 4),
                risk_score=round(risk, 4),
                total_score=total,
                score_breakdown=breakdown,
                eligible=True,
                ineligibility_reasons=[],
            )
        )

    cards.sort(key=lambda c: (-c.total_score, c.supplier_id))
    return cards
