"""Plain-English diff between two scorecard sets (replan / re-score explanation).

Deterministic; feeds the LLM change explanation and is shown raw when the LLM is down (G6).
"""

from procureai.domain.models import Scorecard


def recommended_id(cards: list[Scorecard]) -> str | None:
    """First eligible supplier in a ranked list, or None."""
    return next((c.supplier_id for c in cards if c.eligible), None)


def explain_diff(before: list[Scorecard], after: list[Scorecard]) -> list[str]:
    """Lines, in order: recommendation change, then per supplier (sorted by id)
    eligibility change, landed cost change, score change, added/removed."""
    lines: list[str] = []
    old_rec, new_rec = recommended_id(before), recommended_id(after)
    if old_rec != new_rec:
        lines.append(f"Recommended supplier changed from {old_rec or 'none'} to {new_rec or 'none'}.")
    else:
        lines.append(f"Recommended supplier unchanged: {new_rec or 'none'}.")

    b = {c.supplier_id: c for c in before}
    a = {c.supplier_id: c for c in after}
    for sid in sorted(set(b) | set(a)):
        if sid not in a:
            lines.append(f"{sid} is no longer scored.")
            continue
        if sid not in b:
            lines.append(f"{sid} is newly scored (eligible={a[sid].eligible}).")
            continue
        old, new = b[sid], a[sid]
        if old.eligible and not new.eligible:
            lines.append(f"{sid} became ineligible because: {'; '.join(new.ineligibility_reasons)}.")
        elif not old.eligible and new.eligible:
            lines.append(f"{sid} became eligible.")
        if old.landed_cost != new.landed_cost:
            lines.append(f"Landed cost of {sid} changed from {old.landed_cost:,.2f} to {new.landed_cost:,.2f}.")
        if new.eligible and old.eligible and old.total_score != new.total_score:
            lines.append(f"Score of {sid} changed from {old.total_score:.2f} to {new.total_score:.2f}.")
    return lines
