"""Pick agent implementations from settings.MODE (PLAN.md D3)."""

from procureai.agents.base import AgentSet
from procureai.agents.decision import LiveDecisionAgent
from procureai.agents.document import LiveDocumentAgent
from procureai.agents.mock import MockDecisionAgent, MockDocumentAgent, MockNegotiationAgent, MockSupplierIntelAgent
from procureai.config.settings import Settings
from procureai.llm.factory import build_llm_client


def build_agents(settings: Settings) -> AgentSet:
    """MODE=live: the Document and Decision Agents talk to the gateway (the rationale on LLM_MODEL,
    change explanations on LLM_MODEL_FAST, D18), supplier history stays a database lookup (never an
    LLM) and negotiation drafts stay templated until a later task replaces the Negotiation Agent."""
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
        negotiation=MockNegotiationAgent(),
    )
