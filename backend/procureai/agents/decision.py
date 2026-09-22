"""LLM-backed Decision Agent (PLAN.md §3, D2, D18, G1): explains, never scores or does maths.

Two single-shot JSON calls at most: the rationale (task "explain", LLM_MODEL) and, only after a
replan / re-score, the change explanation (task "explain_diff", LLM_MODEL_FAST). The model sees
engine output only — scorecards, validated figures, supplier history, diff lines — never document
or supplier text. Every number in the prose must already be in the input JSON (number guard);
anything else, a parse failure or a gateway outage falls back to MockDecisionAgent's templated
text so the demo keeps running (G6).
"""

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from procureai.agents.base import Explanation
from procureai.agents.mock import MockDecisionAgent
from procureai.agents.prompts.decision import (
    CHANGE_KEYS,
    CHANGE_SYSTEM,
    RATIONALE_KEYS,
    RATIONALE_SYSTEM,
    user_message,
)
from procureai.config.settings import Settings
from procureai.domain.models import (
    ProcurementRequest,
    Scorecard,
    ScoringWeights,
    SupplierProfile,
    ValidatedQuote,
)
from procureai.engine.costing import days_available
from procureai.llm.base import LLMClient, LLMRequestTooLarge, LLMUnavailable

log = logging.getLogger(__name__)

RATIONALE_TASK, RATIONALE_MAX_TOKENS = "explain", 400
CHANGE_TASK, CHANGE_MAX_TOKENS = "explain_diff", 250

REQUEST_CHANGE_PREFIX = "Request changed"  # orchestrator._changes_line leads diff_lines on a replan


class LiveDecisionAgent:
    """Real Claude prose for the two narrative moments; deterministic guard and fallback around it."""

    def __init__(self, llm: LLMClient, settings: Settings, *, llm_fast: LLMClient | None = None) -> None:
        self.llm = llm
        self.llm_fast = llm_fast or llm  # change explanations on LLM_MODEL_FAST when the factory provides it
        self.settings = settings
        self.fallback = MockDecisionAgent()
        # Route that served the last explain() (LLMResult.backend, D11); "template" when every call fell back
        # to canned text. Read by the orchestrator for the agent.finished payload.
        self.last_backend: str | None = None
        # First fallback of the last explain(), {"reason": guard_trip|parse_error|llm_unavailable, "detail"}, or
        # None when every call was used as answered (D26, T16). The orchestrator turns it into agent.failed.
        self.last_fallback: dict[str, str] | None = None

    def explain(
        self,
        request: ProcurementRequest,
        scorecards: list[Scorecard],
        validated: list[ValidatedQuote],
        diff_lines: list[str] | None = None,
        *,
        profiles: dict[str, SupplierProfile] | None = None,
        weights: ScoringWeights | None = None,
        before: list[Scorecard] | None = None,
    ) -> Explanation:
        templated = self.fallback.explain(request, scorecards, validated, diff_lines)
        self.last_backend = "template"
        self.last_fallback = None

        payload = self.rationale_input(request, scorecards, validated, profiles or {}, weights)
        answer = self._ask(self.llm, RATIONALE_TASK, RATIONALE_SYSTEM, payload, RATIONALE_MAX_TOKENS, RATIONALE_KEYS)
        rationale = self._compose_rationale(answer) if answer else templated["rationale"]

        change = templated["change_explanation"]
        if diff_lines:
            payload = self.change_input(scorecards, validated, diff_lines, before)
            answer = self._ask(self.llm_fast, CHANGE_TASK, CHANGE_SYSTEM, payload, CHANGE_MAX_TOKENS, CHANGE_KEYS)
            if answer:
                change = answer["explanation"]
        return Explanation(rationale=rationale, change_explanation=change)

    # ------------------------------------------------------------------ inputs (engine facts only)

    @staticmethod
    def rationale_input(
        request: ProcurementRequest,
        scorecards: list[Scorecard],
        validated: list[ValidatedQuote],
        profiles: dict[str, SupplierProfile],
        weights: ScoringWeights | None,
    ) -> dict[str, Any]:
        """Compact JSON the model explains. Money is pre-formatted and scores pre-rounded so there is
        nothing left to round; dates are given as the delivery window in days (what the engine checks,
        D13) so the prompt — and its cache key — is the same whichever day the demo runs."""
        by_id = {vq.supplier_id: vq for vq in validated}
        suppliers = []
        for rank, card in enumerate(scorecards, start=1):
            vq, profile = by_id.get(card.supplier_id), profiles.get(card.supplier_id)
            suppliers.append({
                "rank": rank,
                "id": card.supplier_id,
                "name": vq.supplier_name if vq else card.supplier_id,
                "eligible": card.eligible,
                "ineligibility_reasons": card.ineligibility_reasons,
                "landed_cost": _money(card.landed_cost),
                "unit_price": _money(_effective_price(vq)) if vq else None,
                "negotiated": bool(vq and vq.negotiated),
                "lead_time_days": card.lead_time_days,
                "on_time_pct": _pct(profile.on_time_rate) if profile else None,
                "defect_pct": _pct(profile.defect_rate) if profile else None,
                "total_score": round(card.total_score, 1),
                "score_breakdown": {k: round(v, 1) for k, v in card.score_breakdown.items()},
            })
        return {
            "request": {
                "product": request.product,
                "quantity": request.quantity,
                "delivery_window_days": days_available(request),
                "budget": _money(request.budget),
                "currency": request.currency,
            },
            "weights": weights.model_dump(mode="json") if weights else None,
            "score_scale": 100,
            "suppliers": suppliers,
        }

    @staticmethod
    def change_input(
        scorecards: list[Scorecard],
        validated: list[ValidatedQuote],
        diff_lines: list[str],
        before: list[Scorecard] | None,
    ) -> dict[str, Any]:
        names = {vq.supplier_id: vq.supplier_name for vq in validated}
        prev = {c.supplier_id: c for c in before or []}
        if diff_lines and diff_lines[0].startswith(REQUEST_CHANGE_PREFIX):
            what_changed = [diff_lines[0]]
        else:  # re-score after negotiation: the accepted offers, from the validated quotes
            what_changed = [_negotiated_line(vq) for vq in validated if vq.negotiated and vq.negotiated_offer]
            what_changed = what_changed or ["negotiation closed without improved offers"]
        per_supplier = []
        for card in scorecards:
            old = prev.get(card.supplier_id)
            per_supplier.append({
                "id": card.supplier_id,
                "name": names.get(card.supplier_id, card.supplier_id),
                "before": _card_figures(old) if old else None,
                "after": _card_figures(card),
            })
        for sid, old in prev.items():
            if sid not in {c.supplier_id for c in scorecards}:
                per_supplier.append({"id": sid, "name": names.get(sid, sid), "before": _card_figures(old), "after": None})
        return {
            "what_changed": what_changed,
            "diff_lines": diff_lines,
            "recommended_before": _recommended_name(before or [], names) if before is not None else None,
            "recommended_after": _recommended_name(scorecards, names),
            "per_supplier": per_supplier,
        }

    # ------------------------------------------------------------------ one guarded call

    def _ask(self, llm: LLMClient, task: str, system: str, payload: dict, max_tokens: int, keys: tuple[str, ...]) -> dict | None:
        user = user_message(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        try:
            result = llm.complete(task, system, user, max_tokens=max_tokens, json_mode=True)
        except (LLMUnavailable, LLMRequestTooLarge) as exc:  # G6: templated text keeps the run alive
            log.warning("decision agent %s: gateway unavailable (%s); using templated text", task, exc)
            self._fell_back("llm_unavailable", f"{task}: {exc}")
            return None
        self.last_backend = result.backend
        answer = result.parsed_json
        if not isinstance(answer, dict) or not isinstance(answer.get(keys[0]), str) or not answer[keys[0]].strip():
            log.warning("decision agent %s: no usable JSON in the response; using templated text", task)
            self._fell_back("parse_error", f"{task}: no usable JSON in the response")
            return None
        text = " ".join(str(answer[k]) for k in keys if isinstance(answer.get(k), str))
        foreign = foreign_numbers(text, user)
        if foreign:
            log.warning("decision agent %s: response contains numbers not in the input %s; using templated text",
                        task, sorted(foreign))
            self._fell_back("guard_trip", f"{task}: numbers not in the input: {', '.join(sorted(foreign))}")
            return None
        return answer

    def _fell_back(self, reason: str, detail: str) -> None:
        if self.last_fallback is None:  # the first fallback of an explain() is the one reported
            self.last_fallback = {"reason": reason, "detail": detail}

    @staticmethod
    def _compose_rationale(answer: dict) -> str:
        parts = [answer["rationale"].strip()]
        if isinstance(answer.get("key_tradeoff"), str) and answer["key_tradeoff"].strip():
            parts.append(f"Key trade-off: {answer['key_tradeoff'].strip()}")
        if isinstance(answer.get("escalation_note"), str) and answer["escalation_note"].strip():
            parts.append(f"Escalation: {answer['escalation_note'].strip()}")
        return " ".join(parts)


# ---------------------------------------------------------------------- number guard (G1)
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


def allowed_numbers(input_json: str) -> set[Decimal]:
    """Every number the model was given: JSON numbers and digits inside JSON strings (reasons, diff lines)."""
    return {value for _, value, _ in numbers_in(input_json)}


def foreign_numbers(text: str, input_json: str) -> set[str]:
    """Tokens in `text` whose value is not in the input. Equal values match regardless of "," / ".00"
    formatting, and "97%" also matches an input 0.97 (same value as a fraction). Nothing else: a
    rounded, converted or computed number is exactly what this guard exists to catch (G1)."""
    allowed = allowed_numbers(input_json)
    bad: set[str] = set()
    for token, value, percent in numbers_in(text):
        candidates = {value, (value / 100).normalize()} if percent else {value}
        if candidates.isdisjoint(allowed):
            bad.add(token)
    return bad


# ---------------------------------------------------------------------- formatting helpers


def _money(value: Decimal) -> str:
    """"27,618.42": the model copies figures as written, so give it the readable form."""
    return f"{value:,.2f}"


def _pct(rate: float) -> float:
    return round(rate * 100, 1)


def _effective_price(vq: ValidatedQuote) -> Decimal:
    return vq.negotiated_offer.unit_price if vq.negotiated and vq.negotiated_offer else vq.unit_price


def _negotiated_line(vq: ValidatedQuote) -> str:
    offer = vq.negotiated_offer
    line = f"{vq.supplier_name} ({vq.supplier_id}): negotiated unit price {_money(vq.unit_price)} to {_money(offer.unit_price)}"
    if offer.lead_time_days != vq.lead_time_days:
        line += f", lead time {vq.lead_time_days} to {offer.lead_time_days} days"
    return line


def _card_figures(card: Scorecard) -> dict[str, Any]:
    return {
        "eligible": card.eligible,
        "ineligibility_reasons": card.ineligibility_reasons,
        "landed_cost": _money(card.landed_cost),
        "lead_time_days": card.lead_time_days,
        "total_score": round(card.total_score, 1),
    }


def _recommended_name(cards: list[Scorecard], names: dict[str, str]) -> str | None:
    top = next((c.supplier_id for c in cards if c.eligible), None)
    return names.get(top, top) if top else None
