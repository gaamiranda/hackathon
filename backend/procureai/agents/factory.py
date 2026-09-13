"""Pick agent implementations from settings.MODE (PLAN.md D3)."""

from procureai.agents.base import AgentSet
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockSupplierIntelAgent
from procureai.config.settings import Settings


def build_agents(settings: Settings) -> AgentSet:
    if settings.MODE == "mock":
        return AgentSet(
            document=MockDocumentAgent(),
            supplier_intel=MockSupplierIntelAgent(),
            decision=MockDecisionAgent(),
        )
    raise NotImplementedError("MODE=live agents arrive with the gateway client task (T7)")
