"""Deterministic quote costing (PLAN.md §4 formula chain, D2, G1).

All arithmetic is Decimal, rounded ROUND_HALF_UP to 2 dp after every step.
The buyer's cost uses request.quantity; the math check uses quote.quantity_quoted
because the supplier's printed total refers to the quantity they quoted.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from procureai.domain.models import (
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    QuoteChecks,
    ValidatedQuote,
)

CENT = Decimal("0.01")
MATH_TOLERANCE = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    """Round to 2 dp, ROUND_HALF_UP."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def pre_tax_total(quote: NormalizedQuote, quantity: int) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, discount, pre_tax_total) for `quantity` units.

    subtotal      = quantity × unit_price
    discount      = subtotal × discount_pct / 100
    pre_tax_total = subtotal − discount + shipping_cost
    """
    subtotal = q2(Decimal(quantity) * quote.unit_price)
    discount = q2(subtotal * quote.discount_pct / Decimal(100))
    pre_tax = q2(subtotal - discount + quote.shipping_cost)
    return subtotal, discount, pre_tax


def days_available(request: ProcurementRequest) -> int:
    """Calendar days between request.created_at (date part) and request.required_by."""
    created: date = request.created_at.date()
    return (request.required_by - created).days


def capacity_ok(quote: NormalizedQuote, request: ProcurementRequest) -> bool:
    """True if the quote states no capacity or capacity_units ≥ request.quantity."""
    return quote.capacity_units is None or request.quantity <= quote.capacity_units


def compute_costs(
    quote: NormalizedQuote, request: ProcurementRequest, config: ProcurementConfig
) -> ValidatedQuote:
    """Cost the quote for the buyer's request.quantity and run every check.

    tax         = pre_tax_total × tax_rate_pct / 100
    landed_cost = pre_tax_total + tax

    Checks:
      math_ok      |pre_tax_total(quantity_quoted) − llm_stated_total| ≤ 0.01
                   (no stated total → True, with issue "no stated total")
      moq_ok       request.quantity ≥ moq
      lead_time_ok lead_time_days ≤ days_available(request)
      budget_ok    landed_cost ≤ request.budget
      capacity     request.quantity ≤ capacity_units (issue only; QuoteChecks has
                   no capacity field, scoring re-derives it via capacity_ok())
    """
    subtotal, discount, pre_tax = pre_tax_total(quote, request.quantity)
    tax = q2(pre_tax * config.tax_rate_pct / Decimal(100))
    landed = q2(pre_tax + tax)

    issues: list[str] = []

    if quote.llm_stated_total is None:
        math_ok = True
        issues.append("no stated total on document; math check skipped")
    else:
        _, _, quoted_pre_tax = pre_tax_total(quote, quote.quantity_quoted)
        math_ok = abs(quoted_pre_tax - quote.llm_stated_total) <= MATH_TOLERANCE
        if not math_ok:
            issues.append(
                f"stated total {quote.llm_stated_total} does not match computed "
                f"{quoted_pre_tax} for {quote.quantity_quoted} units"
            )

    moq_ok = request.quantity >= quote.moq
    if not moq_ok:
        issues.append(f"quantity {request.quantity} is below MOQ {quote.moq}")

    available = days_available(request)
    lead_ok = quote.lead_time_days <= available
    if not lead_ok:
        issues.append(f"lead time {quote.lead_time_days} d exceeds the {available} d available")

    budget_ok = landed <= request.budget
    if not budget_ok:
        issues.append(f"landed cost {landed} exceeds budget {request.budget}")

    if not capacity_ok(quote, request):
        issues.append(f"quantity {request.quantity} exceeds supplier capacity {quote.capacity_units}")

    if quote.quantity_quoted != request.quantity:
        issues.append(
            f"quoted quantity {quote.quantity_quoted} differs from requested {request.quantity}; "
            "costs computed at requested quantity"
        )

    return ValidatedQuote(
        **quote.model_dump(),
        subtotal=subtotal,
        discount=discount,
        shipping=q2(quote.shipping_cost),
        tax=tax,
        landed_cost=landed,
        checks=QuoteChecks(moq_ok=moq_ok, lead_time_ok=lead_ok, budget_ok=budget_ok, math_ok=math_ok),
        issues=issues,
    )
