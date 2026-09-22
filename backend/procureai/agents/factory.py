"""Pick agent implementations from settings.MODE (PLAN.md D3)."""

from procureai.agents.base import AgentSet
from procureai.agents.decision import LiveDecisionAgent
from procureai.agents.document import LiveDocumentAgent
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockNegotiationAgent, MockSupplierIntelAgent
from procureai.agents.negotiation import LiveNegotiationAgent
from procureai.config.settings import Settings
from procureai.llm.factory import build_llm_client


def build_agents(settings: Settings) -> AgentSet:
    """MODE=live: the Document, Decision and Negotiation Agents talk to the gateway (the rationale on
    LLM_MODEL, change explanations on LLM_MODEL_FAST, D18); supplier history stays a database lookup,
    never an LLM. Every live agent keeps its mock as the deterministic fallback (G6)."""
    if settings.MODE == "mock":
        return AgentSet(
            document=MockDocumentAgent(),
            supplier_intel=MockSupplierIntelAgent(),
            decision=MockDecisionAgent(),
            negotiation=MockNegotiationAgent(),
        )
    llm = build_llm_client(settings)
    return AgentSet(
        document=LiveDocumentAgent(llm),
        supplier_intel=MockSupplierIntelAgent(),
        decision=LiveDecisionAgent(llm, settings, llm_fast=build_llm_client(settings, model=settings.LLM_MODEL_FAST)),
        negotiation=LiveNegotiationAgent(llm, settings),
    )
