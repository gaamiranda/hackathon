"""Pick an LLM client from settings.MODE (PLAN.md D3, D4). Live calls always go through the cache."""

from procureai.config.settings import Settings
from procureai.llm.base import LLMClient
from procureai.llm.cache import ReplayCache
from procureai.llm.gateway import GatewayLLMClient
from procureai.llm.mock import MockLLMClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.MODE != "live":
        return MockLLMClient()
    return ReplayCache(
        GatewayLLMClient(settings),
        model=settings.LLM_MODEL,
        mode=settings.LLM_CACHE_MODE,
    )
