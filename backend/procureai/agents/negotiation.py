"""LLM-backed Negotiation Agent (PLAN.md §3, D7, D17, D18, G2, G3, G4).

Two single-shot JSON calls per round and supplier: the buyer message (task "draft") and the verdict on
the reply (task "negotiation_verdict"). Nothing consequential is left to the model:

  - the target offer is computed by MockNegotiationAgent from the boundaries, handed to the model and
    returned unchanged, so prose can never move a price;
  - the verdict is first decided by the deterministic D17 rule; the model may only pick a verdict the
    rule allows for this round (never a third buyer turn, G2; never "counter" on an acceptable offer)
    and add the human-readable reason that the War Room shows;
  - every draft still goes through engine.policy + the guardrail judge and the human gate afterwards.

A number the model did not get, an unparseable answer, an inconsistent verdict or a dead gateway falls
back to the templated mock output with `last_fallback` set, which the orchestrator surfaces as
agent.failed (D26/G6). The demo keeps running either way.
"""

import json
import logging
from decimal import Decimal
from typing import Any

from procureai.agents.base import CounterVerdict, NegotiationDraft
from procureai.agents.decision import foreign_numbers
from procureai.agents.mock import MockNegotiationAgent
from procureai.agents.prompts.negotiation import (
    DRAFT_KEYS,
    DRAFT_SYSTEM,
    VERDICT_KEYS,
    VERDICT_SYSTEM,
    draft_user_message,
    verdict_user_message,
)
from procureai.config.settings import Settings
from procureai.domain.models import (
    NegotiationBoundaries,
    NegotiationOffer,
    NegotiationRole,
    NegotiationThread,
    NormalizedQuote,
    ProcurementRequest,
)
from procureai.engine.policy import buyer_turns, can_open_turn
from procureai.llm.base import LLMClient, LLMRequestTooLarge, LLMUnavailable

log = logging.getLogger(__name__)

DRAFT_TASK, DRAFT_MAX_TOKENS = "draft", 500
VERDICT_TASK, VERDICT_MAX_TOKENS = "negotiation_verdict", 200

BUYER = "ProcureAI Demo Buyer"  # the buyer the model writes as; never a real company
HISTORY_CHARS = 400  # per prior turn in the draft input
REPLY_CHARS = 800  # the supplier reply handed to the verdict task

VERDICTS: tuple[CounterVerdict, ...] = ("accept", "counter", "close")


class LiveNegotiationAgent:
    """Real Claude wording around a deterministic target offer and a deterministic accept rule."""

    def __init__(self, llm: LLMClient, settings: Settings, *, fallback: MockNegotiationAgent | None = None) -> None:
        self.llm = llm
        self.settings = settings
        self.fallback = fallback or MockNegotiationAgent()
        # Route that served the last call (LLMResult.backend, D11); "template" when it fell back entirely.
        self.last_backend: str | None = None
        # {"reason": guard_trip|parse_error|llm_unavailable|inconsistent_verdict, "detail"} for the last call,
        # or None when the model's answer was used. The orchestrator turns it into agent.failed (T16).
        self.last_fallback: dict[str, str] | None = None
        # The model's one-line reason for the last verdict, for the negotiation.round_completed payload.
        self.last_reason: str | None = None

    # ------------------------------------------------------------------ draft

    def draft(
        self,
        request: ProcurementRequest,
        quote: NormalizedQuote,
        thread: NegotiationThread,
        boundaries: NegotiationBoundaries,
    ) -> NegotiationDraft:
        """Same target offer as the mock (engine arithmetic), the message written by the model."""
        templated = self.fallback.draft(request, quote, thread, boundaries)
        target = templated["target_offer"]
        self.last_backend, self.last_fallback = "template", None

        user = draft_user_message(_compact(self.draft_input(request, quote, thread, boundaries, target)))
        answer = self._ask(DRAFT_TASK, DRAFT_SYSTEM, user, DRAFT_MAX_TOKENS, DRAFT_KEYS)
        if answer is None:
            return templated

        message = answer["message"].strip()
        foreign = foreign_numbers(message, user)
        if foreign:  # G1/G3: a figure nobody gave it is exactly what must never go out
            log.warning("negotiation draft for %s: numbers not in the input %s; using the templated draft",
                        thread.supplier_id, sorted(foreign))
            self._fell_back("guard_trip", f"draft: numbers not in the input: {', '.join(sorted(foreign))}")
            return templated

        draft = NegotiationDraft(message=message, target_offer=target)
        summary = answer.get("summary")
        # The summary is internal (the timeline row), so a bad one is dropped rather than costing us the message.
        if isinstance(summary, str) and summary.strip() and not foreign_numbers(summary, user):
            draft["summary"] = summary.strip()
        return draft

    # ------------------------------------------------------------------ verdict

    def evaluate_counter(
        self,
        thread: NegotiationThread,
        counter: NegotiationOffer | None,
        boundaries: NegotiationBoundaries,
    ) -> CounterVerdict:
        """The D17 rule decides what is possible; the model picks inside that set and says why."""
        self.last_backend, self.last_fallback, self.last_reason = "template", None, None
        rule_verdict = self.fallback.evaluate_counter(thread, counter, boundaries)
        allowed = allowed_verdicts(thread, counter, boundaries)

        payload = self.verdict_input(thread, counter, boundaries)
        user = verdict_user_message(_compact(payload), _last_reply(thread))
        answer = self._ask(VERDICT_TASK, VERDICT_SYSTEM, user, VERDICT_MAX_TOKENS, VERDICT_KEYS)
        if answer is None:
            return rule_verdict

        verdict = answer["verdict"].strip().lower()
        if verdict not in allowed:
            log.warning("negotiation verdict for %s: %r is not allowed here (%s); using the rule verdict %r",
                        thread.supplier_id, verdict, ", ".join(sorted(allowed)), rule_verdict)
            self._fell_back("inconsistent_verdict",
                            f"negotiation_verdict: {verdict!r} not in {sorted(allowed)}; rule says {rule_verdict!r}")
            return rule_verdict

        reason = answer.get("reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        foreign = foreign_numbers(reason, user)
        if foreign:  # the whole answer is discarded, as for the drafts and the Decision Agent
            log.warning("negotiation verdict for %s: reason contains numbers not in the input %s; using the rule verdict",
                        thread.supplier_id, sorted(foreign))
            self._fell_back("guard_trip", f"negotiation_verdict: numbers not in the input: {', '.join(sorted(foreign))}")
            return rule_verdict

        self.last_reason = reason or None
        return verdict  # type: ignore[return-value]  # checked against VERDICTS via `allowed`

    # ------------------------------------------------------------------ inputs (this supplier only, G3)

    @staticmethod
    def draft_input(
        request: ProcurementRequest,
        quote: NormalizedQuote,
        thread: NegotiationThread,
        boundaries: NegotiationBoundaries,
        target: NegotiationOffer,
    ) -> dict[str, Any]:
        """What the model writes from: this supplier's own figures, the target the engine computed and
        the turns of this thread. No other supplier, no score, no landed cost, no date (cache stability)."""
        current = thread.current_offer or thread.original_offer or target
        return {
            "buyer": BUYER,
            "supplier_name": quote.supplier_name,
            "product": request.product,
            "quantity": f"{request.quantity:,}",
            "current_offer": {
                "unit_price": _money(current.unit_price),
                "lead_time_days": current.lead_time_days,
                "currency": request.currency,
            },
            "target_offer": {"unit_price": _money(target.unit_price), "lead_time_days": target.lead_time_days},
            "round": buyer_turns(thread) + 1,
            "max_rounds": boundaries.max_rounds,
            "boundaries": _boundaries(boundaries),
            "thread_history": [
                {"role": turn.role.value, "message": turn.message[:HISTORY_CHARS]} for turn in thread.turns
            ],
        }

    @staticmethod
    def verdict_input(
        thread: NegotiationThread,
        counter: NegotiationOffer | None,
        boundaries: NegotiationBoundaries,
    ) -> dict[str, Any]:
        """Offers and round state. The reply text travels outside this JSON, between delimiters (G4)."""
        ask, _ = MockNegotiationAgent._last_ask(thread)
        return {
            "ask": _offer(ask),
            "counter": _offer(counter),
            "round": buyer_turns(thread),
            "can_open_turn": can_open_turn(thread, boundaries),
            "boundaries": _boundaries(boundaries),
        }

    # ------------------------------------------------------------------ one guarded call

    def _ask(self, task: str, system: str, user: str, max_tokens: int, keys: tuple[str, ...]) -> dict | None:
        try:
            result = self.llm.complete(task, system, user, max_tokens=max_tokens, json_mode=True)
        except (LLMUnavailable, LLMRequestTooLarge) as exc:  # G6: the templated agent keeps the run alive
            log.warning("negotiation agent %s: gateway unavailable (%s); using the templated output", task, exc)
            self._fell_back("llm_unavailable", f"{task}: {exc}")
            return None
        self.last_backend = result.backend
        answer = result.parsed_json
        if not isinstance(answer, dict) or not isinstance(answer.get(keys[0]), str) or not answer[keys[0]].strip():
            log.warning("negotiation agent %s: no usable JSON in the response; using the templated output", task)
            self._fell_back("parse_error", f"{task}: no usable JSON in the response")
            return None
        return answer

    def _fell_back(self, reason: str, detail: str) -> None:
        if self.last_fallback is None:  # the first fallback of a call is the one reported
            self.last_fallback = {"reason": reason, "detail": detail}


# ---------------------------------------------------------------------- the rule's allowed set (D17, G2)


def allowed_verdicts(
    thread: NegotiationThread, counter: NegotiationOffer | None, boundaries: NegotiationBoundaries
) -> set[str]:
    """Which verdicts the deterministic rule leaves open for this round.

    The model may pick any of them; anything else is discarded in favour of the rule's own verdict.
    "counter" is impossible once the round limit is reached (G2) or once the counter already meets the
    acceptance rule, and "accept" is impossible when the supplier made no offer at all.
    """
    allowed = set(VERDICTS)
    if not can_open_turn(thread, boundaries) or MockNegotiationAgent.meets_acceptance_rule(thread, counter):
        allowed.discard("counter")
    if counter is None:
        allowed.discard("accept")
    return allowed


# ---------------------------------------------------------------------- helpers


def _compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _offer(offer: NegotiationOffer | None) -> dict[str, Any] | None:
    return None if offer is None else {"unit_price": _money(offer.unit_price), "lead_time_days": offer.lead_time_days}


def _money(value: Decimal) -> str:
    """"12.80", not "12.8": the model copies prices as written and a supplier email says two decimals."""
    return f"{value:.2f}"


def _boundaries(boundaries: NegotiationBoundaries) -> dict[str, Any]:
    """Only what bounds this ask; the buyer's own strategy knobs stay out of the prompt."""
    return {
        "max_discount_ask_pct": str(boundaries.max_discount_ask_pct),
        "min_lead_time_days": boundaries.min_lead_time_days,
        "max_rounds": boundaries.max_rounds,
    }


def _last_reply(thread: NegotiationThread) -> str:
    """The supplier's latest message, untrusted (G4) and truncated; "" when they never wrote one."""
    for turn in reversed(thread.turns):
        if turn.role == NegotiationRole.SUPPLIER:
            return turn.message[:REPLY_CHARS]
    return ""
