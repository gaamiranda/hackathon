"""The number guard (PLAN.md G1, D2): no LLM prose may contain a number nobody gave it.

The rule: every number in a model's answer must already appear in the JSON we sent it —
otherwise the whole answer is discarded and deterministic text is used instead. The engine owns
the arithmetic, but the *prose* around the figures is written by Claude, and prose is where a
model quietly computes. It does: T7 caught Sonnet inventing a cost difference in 2 of 3 first-draft
rationales, T10c in 3 of 3 through OpenClaw, T20 in the first negotiation recording.

"Given" means any digit run in the request body, whether it was a JSON number or sat inside a
string. Formatting is ignored ("27,618.42" == "27618.4200") and a percentage also matches its
fraction ("97%" == 0.97). Nothing else: a rounded, converted, summed or differenced number is
exactly what this guard exists to catch. Used by agents/decision.py (rationale, change explanation)
and agents/negotiation.py (buyer message, summary, verdict reason); a trip is never silent — the
agent sets last_fallback={"reason": "guard_trip", ...} and the orchestrator emits agent.failed.
"""

import re
from decimal import Decimal, InvalidOperation

# Digits with optional thousands separators, decimals and a % sign; "-" is not captured so a range
# like "10-8 days" or a diff arrow "v1 → v2" still yields plain numbers.
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.,])\d+(?:\.\d+)?%?")


def numbers_in(text: str) -> list[tuple[str, Decimal, bool]]:
    """(token, value, is_percent) for every number in `text`, thousands separators removed."""
    out = []
    for token in NUMBER.findall(text):
        percent = token.endswith("%")
        try:
            value = Decimal(token.rstrip("%").replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the regex only matches digit runs
            continue
        out.append((token, value.normalize(), percent))
    return out


def numbers_the_model_was_given(input_json: str) -> set[Decimal]:
    """The allowed set: every number in the request body we sent — JSON numbers and digits
    inside JSON strings (ineligibility reasons, diff lines, boundaries)."""
    return {value for _, value, _ in numbers_in(input_json)}


def foreign_numbers(text: str, input_json: str) -> set[str]:
    """Tokens in `text` whose value is not in the allowed set. Equal values match regardless of
    "," / ".00" formatting, and "97%" also matches an input 0.97 (same value as a fraction)."""
    allowed = numbers_the_model_was_given(input_json)
    bad: set[str] = set()
    for token, value, percent in numbers_in(text):
        candidates = {value, (value / 100).normalize()} if percent else {value}
        if candidates.isdisjoint(allowed):
            bad.add(token)
    return bad
