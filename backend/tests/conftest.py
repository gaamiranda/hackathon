"""Test config: never spend tokens, never touch the network (PLAN.md risk 2b).

The suite must give the same result on a laptop with live credentials in .env and in CI without them,
so the gateway is explicitly blanked here and replay_only turns any un-recorded call into a visible
LLMCacheMiss instead of a silent live request.
"""

import os

os.environ.setdefault("MODE", "mock")
os.environ["LLM_CACHE_MODE"] = "replay_only"
os.environ["JUDGE_CACHE_MODE"] = "replay_only"  # Jev verdicts replay from data/judge_cache/ (T14)
os.environ.setdefault("GUARDRAIL_JUDGE", "mock")
os.environ["LLM_GATEWAY_URL"] = ""
os.environ["LLM_GATEWAY_API_KEY"] = ""
