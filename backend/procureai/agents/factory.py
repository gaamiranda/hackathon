"""Pick agent implementations from settings.MODE (PLAN.md D3)."""

from procureai.agents.base import AgentSet
from procureai.agents.document import LiveDocumentAgent
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockNegotiationAgent, MockSupplierIntelAgent
from procureai.config.settings import Settings
from procureai.llm.factory import build_llm_client


def build_agents(settings: Settings) -> AgentSet:
    """MODE=live currently means "live extraction": the Document Agent talks to the gateway, while
    supplier history stays a database lookup (never an LLM) and the rationale and negotiation
    drafts stay templated until later tasks replace the Decision and Negotiation Agents."""
    if settings.MODE == "mock":
        return AgentSet(
            document=MockDocumentAgent(),
            supplier_intel=MockSupplierIntelAgent(),
            decision=MockDecisionAgent(),
            negotiation=MockNegotiationAgent(),
        )
    return AgentSet(
        document=LiveDocumentAgent(build_llm_client(settings)),
        supplier_intel=MockSupplierIntelAgent(),
        decision=MockDecisionAgent(),
        negotiation=MockNegotiationAgent(),
    )
