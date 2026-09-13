"""Engine golden tests. Every expected number below is hand-computed from PLAN.md §15."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from procureai.domain.models import (
    NegotiationBoundaries,
    NegotiationOffer,
    NegotiationRole,
    NegotiationThread,
    NegotiationTurn,
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    ScoringWeights,
    SupplierProfile,
)
from procureai.engine import (
    can_open_turn,
    check_negotiation_bounds,
    check_outbound_message,
    compute_costs,
    evaluate,
    explain_diff,
    recommended_id,
    score,
)

SYNTHETIC = Path(__file__).resolve().parents[2] / "data" / "synthetic"
D = Decimal


def load_quote(name: str) -> NormalizedQuote:
    return NormalizedQuote.model_validate_json((SYNTHETIC / f"{name}.expected.json").read_text())


@pytest.fixture
def quotes() -> dict[str, NormalizedQuote]:
    return {
        "a": load_quote("supplier_a_apex.pdf"),
        "b": load_quote("supplier_b_borealis.xlsx"),
        "c": load_quote("supplier_c_cobalt.eml.txt"),
    }


@pytest.fixture
def profiles() -> dict[str, SupplierProfile]:
    def p(sid, name, on_time, defect, cap):
        return SupplierProfile(
            supplier_id=sid, name=name, on_time_rate=on_time, defect_rate=defect,
            orders_completed=20, avg_lead_time_days=10, max_capacity_units=cap,
        )

    return {
        "sup_a": p("sup_a", "Apex Components Ltd", 0.82, 0.045, 20000),
        "sup_b": p("sup_b", "Borealis Manufacturing AS", 0.97, 0.008, 4000),
        "sup_c": p("sup_c", "Cobalt Industrial", 0.94, 0.015, 10000),
    }


@pytest.fixture
def config() -> ProcurementConfig:
    return ProcurementConfig(
        weights=ScoringWeights(price=0.4, lead_time=0.2, reliability=0.25, risk=0.15),
        negotiation=NegotiationBoundaries(max_discount_ask_pct=D("5"), min_lead_time_days=5, max_rounds=2),
        tax_rate_pct=D("9"),
    )


def request(quantity: int = 2000, budget: str = "30000.00") -> ProcurementRequest:
    return ProcurementRequest(
        id="req-demo-001", product="Product X", quantity=quantity, required_by=date(2026, 9, 29),
        budget=D(budget), currency="USD", created_at=datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------- costing


def test_supplier_b_costs_at_2000_units(quotes, config):
    # 2000 × 12.80 = 25600; 2% = 512; +250 shipping = 25338; 9% tax = 2280.42; landed 27618.42
    vq = compute_costs(quotes["b"], request(), config)
    assert vq.subtotal == D("25600.00")
    assert vq.discount == D("512.00")
    assert vq.subtotal - vq.discount + vq.shipping == D("25338.00")
    assert vq.tax == D("2280.42")
    assert vq.landed_cost == D("27618.42")
    assert vq.checks.model_dump() == {"moq_ok": True, "lead_time_ok": True, "budget_ok": True, "math_ok": True}
    assert vq.issues == []


def test_supplier_a_math_mismatch(quotes, config):
    # 2000 × 11.20 = 22400 + 400 shipping = 22800.00, but document says 22040.00
    vq = compute_costs(quotes["a"], request(), config)
    assert vq.checks.math_ok is False
    assert "22040.00" in vq.issues[0] and "22800.00" in vq.issues[0]
    assert vq.landed_cost == D("24852.00")  # 22800 × 1.09
    assert vq.checks.moq_ok and vq.checks.lead_time_ok and vq.checks.budget_ok


def test_supplier_c_costs(quotes, config):
    # 2000 × 13.40 = 26800, free shipping, 9% tax = 2412 → 29212.00
    vq = compute_costs(quotes["c"], request(), config)
    assert vq.tax == D("2412.00")
    assert vq.landed_cost == D("29212.00")
    assert vq.shipping == D("0.00")
    assert all(vq.checks.model_dump().values())


def test_missing_stated_total_is_ok_with_issue(quotes, config):
    q = quotes["b"].model_copy(update={"llm_stated_total": None})
    vq = compute_costs(q, request(), config)
    assert vq.checks.math_ok is True
    assert any("no stated total" in i for i in vq.issues)


def test_failed_checks_produce_issues(quotes, config):
    vq = compute_costs(quotes["b"], request(quantity=500, budget="100.00"), config)
    assert not vq.checks.moq_ok and not vq.checks.budget_ok
    assert any("MOQ" in i for i in vq.issues) and any("budget" in i for i in vq.issues)
    late = compute_costs(quotes["b"].model_copy(update={"lead_time_days": 15}), request(), config)
    assert not late.checks.lead_time_ok


# --------------------------------------------------------------------------- evaluate / scoring


def test_evaluate_stops_math_mismatch(quotes, profiles, config):
    res = evaluate(request(), config, list(quotes.values()), profiles)
    assert res.stopped_for_math_mismatch == [quotes["a"].quote_id]
    assert [c.supplier_id for c in res.scorecards] == ["sup_b", "sup_c"]
    assert len(res.validated) == 3


def test_ranking_at_2000_units_b_first(quotes, profiles, config):
    res = evaluate(request(), config, list(quotes.values()), profiles, include_math_mismatch=True)
    assert res.stopped_for_math_mismatch == []
    assert [c.supplier_id for c in res.scorecards][0] == "sup_b"
    assert {c.supplier_id for c in res.scorecards} == {"sup_a", "sup_b", "sup_c"}
    for c in res.scorecards:
        assert c.eligible
        assert abs(sum(c.score_breakdown.values()) - c.total_score) < 0.01
        assert 0 <= c.total_score <= 100


def test_interrupt_to_5000_units_flips_to_c(quotes, profiles, config):
    before = evaluate(request(), config, list(quotes.values()), profiles).scorecards
    after = evaluate(request(5000, "75000.00"), config, list(quotes.values()), profiles).scorecards

    assert recommended_id(before) == "sup_b"
    assert recommended_id(after) == "sup_c"
    b = next(c for c in after if c.supplier_id == "sup_b")
    assert not b.eligible and "capacity 4000" in b.ineligibility_reasons[0]
    assert b.landed_cost == D("68637.30")  # 64000 − 1280 + 250 = 62970 × 1.09

    lines = explain_diff(before, after)
    assert "Recommended supplier changed from sup_b to sup_c." in lines
    assert any(line.startswith("sup_b became ineligible because") for line in lines)


def test_single_eligible_quote_scores_100_on_price_and_lead(quotes, profiles, config):
    vq = compute_costs(quotes["b"], request(), config)
    [card] = score([vq], profiles, request(), config)
    assert card.eligible
    assert card.score_breakdown["price"] == pytest.approx(40.0)
    assert card.score_breakdown["lead_time"] == pytest.approx(20.0)


def test_zero_eligible_quotes(quotes, profiles, config):
    blacklisted = {sid: p.model_copy(update={"blacklisted": True}) for sid, p in profiles.items()}
    res = evaluate(request(), config, list(quotes.values()), blacklisted)
    assert res.scorecards and all(not c.eligible for c in res.scorecards)
    assert all("blacklisted" in c.ineligibility_reasons[0] for c in res.scorecards)
    assert recommended_id(res.scorecards) is None


def test_defect_rate_over_threshold_is_ineligible(quotes, profiles, config):
    risky = {**profiles, "sup_b": profiles["sup_b"].model_copy(update={"defect_rate": 0.06})}
    res = evaluate(request(), config, list(quotes.values()), risky)
    b = next(c for c in res.scorecards if c.supplier_id == "sup_b")
    assert not b.eligible and "defect rate" in b.ineligibility_reasons[0]
    assert b.landed_cost == D("27618.42")


# --------------------------------------------------------------------------- policy


def test_outbound_message_leaks(profiles):
    own, others = profiles["sup_b"], list(profiles.values())
    allowed = {D("12.40")}
    bad = check_outbound_message("Apex offered us 22,800.00, can you beat it?", own, others, allowed)
    assert not bad.ok
    assert any("Apex" in v for v in bad.violations)
    assert any("22,800.00" in v for v in bad.violations)
    assert any("beat" in v for v in bad.violations)

    good = check_outbound_message(
        "Could you offer USD 12.40 per unit while keeping the 10-day lead time?", own, others, allowed
    )
    assert good.ok, good.violations


def test_negotiation_bounds():
    b = NegotiationBoundaries(max_discount_ask_pct=D("5"), min_lead_time_days=5)
    current = NegotiationOffer(unit_price=D("12.80"), lead_time_days=10)
    assert check_negotiation_bounds(current, NegotiationOffer(unit_price=D("12.40"), lead_time_days=10), b).ok
    too_low = check_negotiation_bounds(current, NegotiationOffer(unit_price=D("12.00"), lead_time_days=10), b)
    assert not too_low.ok and "discount ask" in too_low.violations[0]
    too_fast = check_negotiation_bounds(current, NegotiationOffer(unit_price=D("12.80"), lead_time_days=3), b)
    assert not too_fast.ok and "lead time" in too_fast.violations[0]


def test_third_buyer_turn_is_rejected():
    b = NegotiationBoundaries(max_discount_ask_pct=D("5"), min_lead_time_days=5, max_rounds=2)
    ts = datetime(2026, 9, 15, 9, 20, tzinfo=timezone.utc)

    def turn(role):
        return NegotiationTurn(role=role, message="...", ts=ts)

    thread = NegotiationThread(run_id="r", supplier_id="sup_b", boundaries=b, turns=[])
    assert can_open_turn(thread, b)
    thread.turns = [turn(NegotiationRole.BUYER), turn(NegotiationRole.SUPPLIER)]
    assert can_open_turn(thread, b)
    thread.turns += [turn(NegotiationRole.BUYER), turn(NegotiationRole.SUPPLIER)]
    assert not can_open_turn(thread, b)
