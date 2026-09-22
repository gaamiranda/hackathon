"""Pick an LLM client from settings.MODE (PLAN.md D3, D4, D11). Live calls always go through the cache."""

from procureai.config.settings import Settings
from procureai.llm.base import LLMClient
from procureai.llm.cache import ReplayCache
from procureai.llm.fallback import FallbackLLMClient
from procureai.llm.gateway import GatewayLLMClient
from procureai.llm.mock import MockLLMClient
from procureai.llm.openclaw import OpenClawLLMClient


def build_llm_client(settings: Settings, model: str | None = None) -> LLMClient:
    """`model` overrides settings.LLM_MODEL (e.g. settings.LLM_MODEL_FAST, D18); the cache is keyed per model.

    LLM_BACKEND=openclaw wraps OpenClaw (primary) and the direct gateway (fallback) in one client; only the
    tasks in OPENCLAW_TASKS take the primary (D29). The cache sits outside and is keyed on the provider model
    only, so a task recorded on either route replays on both.
    """
    if settings.MODE != "live":
        return MockLLMClient()
    model = model or settings.LLM_MODEL
    gateway = GatewayLLMClient(settings, model=model)
    inner: LLMClient = gateway
    if settings.LLM_BACKEND == "openclaw":
        inner = FallbackLLMClient(primary=OpenClawLLMClient(settings), fallback=gateway, tasks=settings.openclaw_tasks)
    return ReplayCache(inner, model=model, mode=settings.LLM_CACHE_MODE)
