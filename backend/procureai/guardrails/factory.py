"""Pick a judge from settings.GUARDRAIL_JUDGE (PLAN.md D20): mock (default, no network) or jev (cached)."""

from procureai.config.settings import Settings
from procureai.guardrails.base import GuardrailJudge
from procureai.guardrails.mock import MockGuardrailJudge


def build_judge(settings: Settings) -> GuardrailJudge:
    if settings.GUARDRAIL_JUDGE == "jev":
        from procureai.guardrails.jev import JevGuardrailJudge

        return JevGuardrailJudge(settings)
    return MockGuardrailJudge()
