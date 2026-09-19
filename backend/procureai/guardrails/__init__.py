"""Guardrail judge (PLAN.md D20, T14): a second, calibrated opinion at each gate.

The judge answers typed yes/no questions with probabilities. It never generates text, never decides on its
own and never replaces the deterministic engine: the orchestrator only ever *lowers* a field's confidence or
*adds* a policy violation on its say-so. When the judge is unavailable the pipeline runs as if it were absent.
"""

from procureai.guardrails.base import (
    ExtractionVerdict,
    FieldVerdict,
    GuardrailJudge,
    InjectionVerdict,
    LeakVerdict,
)
from procureai.guardrails.factory import build_judge
from procureai.guardrails.mock import MockGuardrailJudge

__all__ = [
    "ExtractionVerdict",
    "FieldVerdict",
    "GuardrailJudge",
    "InjectionVerdict",
    "LeakVerdict",
    "MockGuardrailJudge",
    "build_judge",
]
