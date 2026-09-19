"""Live judge backed by TypeSafe's Jev (System One model) through the pinned `typesafe-sdk` (PLAN.md D20).

One `system_one` call per method: the text is the state, every question is a Noul (P(yes)); code applies
the thresholds. Jev never writes anything back — no text, no decision — and any failure (SDK, network,
budget, cache miss in replay_only) becomes a "not evaluated" verdict that the orchestrator treats as
"no judge". Nothing here raises into the workflow.
"""

import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from procureai.config.settings import Settings
from procureai.domain.models import NormalizedQuote
from procureai.guardrails import questions as q
from procureai.guardrails.base import ExtractionVerdict, FieldVerdict, InjectionVerdict, LeakVerdict
from procureai.guardrails.cache import CACHE_DIR, JudgeReplayCache, JudgeResponse, JudgeUsage, SystemOneCaller

log = logging.getLogger(__name__)


class TypeSafeCaller:
    """The only place that imports the SDK. Sync client, questions rebuilt from plain dicts so the replay
    cache stores exactly what was sent."""

    def __init__(self, settings: Settings) -> None:
        from typesafe_sdk import TypeSafeClient

        self.client = TypeSafeClient(
            api_key=settings.TYPESAFE_API_KEY or None,  # None → the SDK reads TYPESAFE_API_KEY from the environment
            model=settings.TYPESAFE_MODEL,
            timeout=settings.JUDGE_TIMEOUT_S,
        )

    def __call__(self, state: Any, questions: dict[str, dict[str, Any]]) -> JudgeResponse:
        from typesafe_sdk import Noul

        built = {
            qid: Noul(instructions=spec["instructions"], criteria=spec.get("criteria"))
            for qid, spec in questions.items()
        }
        response = self.client.system_one(state=state, questions=built)
        return JudgeResponse(
            model=response.model,
            answers={qid: float(answer.noul) for qid, answer in response.nouls.items()},
            usage=JudgeUsage(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens),
        )


class JevGuardrailJudge:
    def __init__(self, settings: Settings, *, caller: SystemOneCaller | None = None,
                 cache: JudgeReplayCache | None = None) -> None:
        self.support_threshold = settings.JUDGE_SUPPORT_THRESHOLD
        self.leak_threshold = settings.JUDGE_LEAK_THRESHOLD
        self.injection_threshold = settings.JUDGE_INJECTION_THRESHOLD
        self.cache = cache or JudgeReplayCache(
            caller or _lazy_caller(settings),
            model=settings.TYPESAFE_MODEL,
            mode=settings.JUDGE_CACHE_MODE,
            cache_dir=CACHE_DIR,
            max_live_calls=settings.JUDGE_MAX_LIVE_CALLS,
        )

    # ------------------------------------------------------------------ methods

    def verify_extraction(self, doc_text: str, quote: NormalizedQuote) -> ExtractionVerdict:
        questions = q.extraction_questions(quote)
        answers = self._ask("verify_extraction", q.extraction_state(doc_text), questions)
        if answers is None:
            return ExtractionVerdict(evaluated=False, error=self._last_error)
        fields = {
            field: FieldVerdict(supported=p >= self.support_threshold, probability=p)
            for field, p in ((f, answers.get(f)) for f in questions)
            if p is not None
        }
        return ExtractionVerdict(fields=fields)

    def check_outbound(
        self,
        message: str,
        own_supplier_name: str,
        other_supplier_names: list[str],
        *,
        allowed_amounts: Iterable[Decimal] = (),  # the regex filter's business; Jev judges the text semantically
    ) -> LeakVerdict:
        answers = self._ask("check_outbound", q.outbound_state(message, own_supplier_name, other_supplier_names),
                            q.outbound_questions(own_supplier_name))
        if answers is None:
            return LeakVerdict(evaluated=False, error=self._last_error)
        flagged = [qid for qid, p in answers.items() if p >= self.leak_threshold]
        return LeakVerdict(
            leaks=bool(flagged),
            probability=max(answers.values()) if answers else None,
            reasons=[f"{q.LEAK_REASONS.get(qid, qid)} (p={answers[qid]:.2f})" for qid in flagged],
        )

    def detect_injection(self, text: str) -> InjectionVerdict:
        answers = self._ask("detect_injection", q.injection_state(text), q.injection_questions())
        if answers is None:
            return InjectionVerdict(evaluated=False, error=self._last_error)
        p = answers.get("addressed_to_ai")
        if p is None:
            return InjectionVerdict(evaluated=False, error="no answer for addressed_to_ai")
        return InjectionVerdict(injection=p >= self.injection_threshold, probability=p)

    # ------------------------------------------------------------------ plumbing

    _last_error: str | None = None

    def _ask(self, method: str, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, float] | None:
        """P(yes) per question id, or None when the judge could not answer (logged, never raised)."""
        try:
            return self.cache.ask(method, state, questions).answers
        except Exception as exc:  # any SDK / network / cache / budget failure → judge absent for this gate
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("guardrail judge unavailable for %s (%s); continuing without it", method, self._last_error)
            return None

    @property
    def live_calls(self) -> int:
        return self.cache.live_calls

    @property
    def live_input_tokens(self) -> int:
        return self.cache.live_input_tokens


def _lazy_caller(settings: Settings) -> SystemOneCaller:
    """Build the SDK client on first live use, so replay-only runs never need credentials or the SDK import."""
    holder: dict[str, TypeSafeCaller] = {}

    def call(state: Any, questions: dict[str, dict[str, Any]]) -> JudgeResponse:
        if "caller" not in holder:
            holder["caller"] = TypeSafeCaller(settings)
        return holder["caller"](state, questions)

    return call
