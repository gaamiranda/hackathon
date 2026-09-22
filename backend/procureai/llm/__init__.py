"""LLM access layer: gateway + OpenClaw clients with fallback, one record/replay cache, one mock (PLAN.md §2, D11)."""

from procureai.llm.base import (
    LLMCacheMiss,
    LLMClient,
    LLMError,
    LLMRequestTooLarge,
    LLMResult,
    LLMUnavailable,
)
from procureai.llm.cache import ReplayCache, cache_key
from procureai.llm.common import extract_json
from procureai.llm.factory import build_llm_client
from procureai.llm.fallback import FallbackLLMClient
from procureai.llm.gateway import GatewayLLMClient
from procureai.llm.mock import MockLLMClient
from procureai.llm.openclaw import OpenClawLLMClient

__all__ = [
    "FallbackLLMClient",
    "GatewayLLMClient",
    "LLMCacheMiss",
    "LLMClient",
    "LLMError",
    "LLMRequestTooLarge",
    "LLMResult",
    "LLMUnavailable",
    "MockLLMClient",
    "OpenClawLLMClient",
    "ReplayCache",
    "build_llm_client",
    "cache_key",
    "extract_json",
]
