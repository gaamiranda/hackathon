"""Pick an LLM client from settings.MODE (PLAN.md D3, D4). Live calls always go through the cache."""

from procureai.config.settings import Settings
from procureai.llm.base import LLMClient
from procureai.llm.cache import ReplayCache
from procureai.llm.gateway import GatewayLLMClient
from procureai.llm.mock import MockLLMClient


def build_llm_client(settings: Settings, model: str | None = None) -> LLMClient:
    """`model` overrides settings.LLM_MODEL (e.g. settings.LLM_MODEL_FAST, D18); the cache is keyed per model."""
    if settings.MODE != "live":
        return MockLLMClient()
    model = model or settings.LLM_MODEL
    return ReplayCache(
        GatewayLLMClient(settings, model=model),
        model=model,
        mode=settings.LLM_CACHE_MODE,
    )
