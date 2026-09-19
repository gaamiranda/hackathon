"""LLM access layer: one gateway client, one record/replay cache, one mock (PLAN.md §2)."""

from procureai.llm.base import (
    LLMCacheMiss,
    LLMClient,
    LLMError,
    LLMRequestTooLarge,
    LLMResult,
    LLMUnavailable,
)
from procureai.llm.cache import ReplayCache, cache_key
from procureai.llm.factory import build_llm_client
from procureai.llm.gateway import GatewayLLMClient, extract_json
from procureai.llm.mock import MockLLMClient

__all__ = [
    "GatewayLLMClient",
    "LLMCacheMiss",
    "LLMClient",
    "LLMError",
    "LLMRequestTooLarge",
    "LLMResult",
    "LLMUnavailable",
    "MockLLMClient",
    "ReplayCache",
    "build_llm_client",
    "cache_key",
    "extract_json",
]
