"""Deterministic negotiation guardrails (PLAN.md D7, D9, G2, G3).

Runs on every outbound draft BEFORE the human gate. Pure string/Decimal logic.
"""

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from procureai.domain.models import (
    NegotiationBoundaries,
    NegotiationOffer,
    NegotiationRole,
    NegotiationThread,
    SupplierProfile,
)


class PolicyResult(BaseModel):
    ok: bool
    violations: list[str] = []


FORBIDDEN_PHRASES = ("beat", "match their", "competitor")

# "USD 1,234.56", "$1234", "€ 12.5", "12.80" (any number with exactly 2 decimals)
_CURRENCY_AMOUNT = re.compile(
    r"(?:USD|EUR|GBP|SGD|JPY|CNY|\$|€|£)\s?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE
)
_TWO_DECIMALS = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?![\d.%])")

_GENERIC_NAME_WORDS = {"the", "ltd", "inc", "co", "as", "gmbh", "llc", "plc", "and", "of"}


def _amounts_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for pattern in (_CURRENCY_AMOUNT, _TWO_DECIMALS):
        for raw in pattern.findall(text):
            try:
                found.add(Decimal(raw.replace(",", "")).quantize(Decimal("0.01")))
            except InvalidOperation:
                continue
    return found


def _name_tokens(profile: SupplierProfile) -> list[str]:
    """Full name plus its first distinctive word (e.g. "Apex" from "Apex Components Ltd")."""
    tokens = [profile.name]
    for word in profile.name.split():
        if word.lower() not in _GENERIC_NAME_WORDS and len(word) > 3:
            tokens.append(word)
            break
    return tokens


def check_outbound_message(
    text: str,
    own_supplier: SupplierProfile,
    all_profiles: list[SupplierProfile],
    allowed_amounts: set[Decimal],
) -> PolicyResult:
    """Block cross-supplier leakage in a draft addressed to own_supplier.

    Violations:
      - any other supplier's name or first distinctive word (case-insensitive, whole word)
      - any currency amount in the text not in allowed_amounts (compared at 2 dp)
      - any of FORBIDDEN_PHRASES ("beat", "match their", "competitor"), whole word
    """
    violations: list[str] = []
    lowered = text.lower()

    for profile in all_profiles:
        if profile.supplier_id == own_supplier.supplier_id:
            continue
        for token in _name_tokens(profile):
            if re.search(rf"\b{re.escape(token.lower())}\b", lowered):
                violations.append(f"mentions other supplier '{token}' ({profile.supplier_id})")
                break

    allowed = {a.quantize(Decimal("0.01")) for a in allowed_amounts}
    for amount in sorted(_amounts_in(text) - allowed):
        violations.append(f"amount {amount:,.2f} is not in the allowed set")

    for phrase in FORBIDDEN_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            violations.append(f"forbidden phrase '{phrase}'")

    return PolicyResult(ok=not violations, violations=violations)


def check_negotiation_bounds(
    current: NegotiationOffer, proposed: NegotiationOffer, boundaries: NegotiationBoundaries
) -> PolicyResult:
    """Enforce the negotiation envelope.

    discount_ask_pct = (current.unit_price − proposed.unit_price) / current.unit_price × 100
    Violations: discount_ask_pct > max_discount_ask_pct;
                proposed.lead_time_days < min_lead_time_days.
    """
    violations: list[str] = []
    if current.unit_price > 0:
        ask = (current.unit_price - proposed.unit_price) / current.unit_price * Decimal(100)
        if ask > boundaries.max_discount_ask_pct:
            violations.append(
                f"discount ask {ask.quantize(Decimal('0.01'))}% exceeds max {boundaries.max_discount_ask_pct}%"
            )
    if proposed.lead_time_days < boundaries.min_lead_time_days:
        violations.append(
            f"lead time {proposed.lead_time_days} d is below minimum {boundaries.min_lead_time_days} d"
        )
    return PolicyResult(ok=not violations, violations=violations)


# ---------------------------------------------------------------------- round cap (G2)
# One path, no prompt involved:
#   ProcurementConfig.negotiation.max_rounds  (domain/models.py, default 2, ge=1 le=2)
#     → NegotiationThread.boundaries          (copied when the thread is opened)
#       → can_open_turn()                     (asked by the orchestrator before every buyer turn)


def buyer_turns(thread: NegotiationThread) -> int:
    """Buyer messages already sent on this thread. Supplier replies do not count."""
    return sum(1 for t in thread.turns if t.role == NegotiationRole.BUYER)


def can_open_turn(thread: NegotiationThread, boundaries: NegotiationBoundaries) -> bool:
    """May the buyer send another message on this thread? (G2: hard cap, max_rounds = 2.)

    The only question the state machine asks about round count, and the only answer that
    matters: the agent's own opinion is checked against it, never the other way round.
    """
    return buyer_turns(thread) < boundaries.max_rounds
