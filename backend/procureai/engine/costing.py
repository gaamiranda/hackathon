"""Deterministic quote costing: the PLAN.md §4 formula chain (D2, G1).

G1 — deterministic math: every figure the buyer ever sees is computed here, in Decimal,
from the extracted fields. The LLM's own `llm_stated_total` is *only ever compared* against
`cost_chain()`; a mismatch stops the workflow for a human (CALC_MISMATCH) instead of being
quietly corrected. No number in this module comes from a model.

All arithmetic is Decimal, quantised to 2 dp ROUND_HALF_UP after every step (`q2`), so the
chain is reproducible to the cent and the same on every machine.

The buyer's cost uses request.quantity; the math check uses quote.quantity_quoted
because the supplier's printed total refers to the quantity they quoted.

A quote with `negotiated_offer` is costed at the negotiated unit price and lead time
(effective_unit_price / effective_lead_time_days, D17); the math check still uses
the original price because the printed total predates the negotiation.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from procureai.domain.models import (
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    QuoteChecks,
    ValidatedQuote,
)

CENT = Decimal("0.01")
PERCENT = Decimal(100)  # discount_pct and tax_rate_pct are percentages, not fractions
MATH_TOLERANCE = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    """Quantise to 2 dp, ROUND_HALF_UP. Applied after every step of the chain."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def effective_unit_price(quote: NormalizedQuote) -> Decimal:
    """Negotiated unit price when a counter-offer was accepted, else the quoted one."""
    return quote.negotiated_offer.unit_price if quote.negotiated_offer else quote.unit_price


def effective_lead_time_days(quote: NormalizedQuote) -> int:
    """Negotiated lead time when a counter-offer was accepted, else the quoted one."""
    return quote.negotiated_offer.lead_time_days if quote.negotiated_offer else quote.lead_time_days


class CostChain(NamedTuple):
    """The five figures of the §4 chain, in the order they are computed."""

    subtotal: Decimal
    discount: Decimal
    pre_tax_total: Decimal
    tax: Decimal
    landed_cost: Decimal


def cost_chain(
    quote: NormalizedQuote, quantity: int, tax_rate_pct: Decimal, unit_price: Decimal | None = None
) -> CostChain:
    """Cost `quantity` units of `quote`. The whole PLAN.md §4 chain, top to bottom (G1).

    `unit_price` overrides the effective price (used by the math check, which must re-derive
    the supplier's printed total from the *quoted* price). `tax_rate_pct` is buyer-side, from
    ProcurementConfig: supplier documents never include tax.
    """
    price = effective_unit_price(quote) if unit_price is None else unit_price

    # PLAN.md §4, one line per formula. q2() = quantise to the cent, ROUND_HALF_UP.
    subtotal = q2(Decimal(quantity) * price)                             # subtotal      = quantity × unit_price
    discount = q2(subtotal * quote.discount_pct / PERCENT)               # discount      = subtotal × discount_pct
    pre_tax = q2(subtotal - discount + quote.shipping_cost)              # pre_tax_total = subtotal − discount + shipping
    tax = q2(pre_tax * tax_rate_pct / PERCENT)                           # tax           = pre_tax_total × tax_rate_pct
    landed = q2(pre_tax + tax)                                           # landed_cost   = pre_tax_total + tax

    return CostChain(subtotal, discount, pre_tax, tax, landed)


def pre_tax_total(
    quote: NormalizedQuote, quantity: int, unit_price: Decimal | None = None
) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, discount, pre_tax_total): the first three links of `cost_chain` (no tax)."""
    chain = cost_chain(quote, quantity, Decimal(0), unit_price)
    return chain.subtotal, chain.discount, chain.pre_tax_total


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
    """Run `cost_chain` at the buyer's request.quantity, then every deterministic check.

    Checks (all engine-side; the LLM sees none of this):
      math_ok      |pre_tax_total(quantity_quoted, original unit_price) − llm_stated_total| ≤ 0.01
                   (no stated total → True, with issue "no stated total"). G1: a mismatch is
                   surfaced to a human, never corrected silently.
      moq_ok       request.quantity ≥ moq
      lead_time_ok effective_lead_time_days ≤ days_available(request)
      budget_ok    landed_cost ≤ request.budget
      capacity_ok  request.quantity ≤ capacity_units (True when no capacity stated)
    """
    subtotal, discount, pre_tax, tax, landed = cost_chain(quote, request.quantity, config.tax_rate_pct)

    issues: list[str] = []

    if quote.llm_stated_total is None:
        math_ok = True
        issues.append("no stated total on document; math check skipped")
    else:
        # G1: re-derive the supplier's printed total from its own quantity and price, and compare.
        _, _, quoted_pre_tax = pre_tax_total(quote, quote.quantity_quoted, unit_price=quote.unit_price)
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
    lead_time = effective_lead_time_days(quote)
    lead_ok = lead_time <= available
    if not lead_ok:
        issues.append(f"lead time {lead_time} d exceeds the {available} d available")

    budget_ok = landed <= request.budget
    if not budget_ok:
        issues.append(f"landed cost {landed} exceeds budget {request.budget}")

    cap_ok = capacity_ok(quote, request)
    if not cap_ok:
        issues.append(f"quantity {request.quantity} exceeds supplier capacity {quote.capacity_units}")

    if quote.quantity_quoted != request.quantity:
        issues.append(
            f"quoted quantity {quote.quantity_quoted} differs from requested {request.quantity}; "
            "costs computed at requested quantity"
        )

    if quote.negotiated_offer:
        issues.append(
            f"costed at negotiated {effective_unit_price(quote)}/unit, {lead_time} d "
            f"(quoted {quote.unit_price}/unit, {quote.lead_time_days} d)"
        )

    return ValidatedQuote(
        **quote.model_dump(),
        subtotal=subtotal,
        discount=discount,
        shipping=q2(quote.shipping_cost),
        pre_tax_total=pre_tax,
        tax=tax,
        landed_cost=landed,
        checks=QuoteChecks(
            moq_ok=moq_ok, lead_time_ok=lead_ok, budget_ok=budget_ok, math_ok=math_ok, capacity_ok=cap_ok
        ),
        issues=issues,
        negotiated=quote.negotiated_offer is not None,
    )
