"""The demo request must produce byte-identical LLM prompts on any day (T18, D30 plan B step 2).

Every LLM call in the demo replays from data/llm_cache/ only if its prompt is identical to the recorded one. The
request created by the UI preset / the seed script says "required by today + 14 days", so the prompt inputs are
rebuilt here for two different "today"s, far apart, and compared byte for byte — and every one of them must
already be in the cache. Extraction prompts are the document text only: doc ids, filenames and upload dates
must not reach them.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from procureai.agents.decision import CHANGE_TASK, LiveDecisionAgent, RATIONALE_TASK
from procureai.agents.document import LiveDocumentAgent, TASK as EXTRACT_TASK
from procureai.agents.factory import build_agents
from procureai.agents.prompts.decision import payload_of
from procureai.config.settings import Settings
from procureai.domain.models import ProcurementRequest, RawDocument, WorkflowState as S
from procureai.llm.cache import CACHE_DIR, ReplayCache, cache_key
from procureai.workflow import Orchestrator, RunStore
from tests.test_decision_agent import _NoNetwork, _Spy, replay_client
from tests.test_document_agent import SYNTHETIC, as_document, recorded_model
from tests.test_interrupt import approve_all
from tests.test_negotiation import DOCS, config, request_  # noqa: F401

TODAYS = (date(2026, 9, 22), date(2027, 3, 1))  # the recording day and one on the far side of a DST change
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class _Recorder:
    """A replay client that also keeps every (task, system, user, max_tokens, json_mode) it was asked for."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def complete(self, task, system, user, **kw):
        self.calls.append((task, system, user, kw.get("max_tokens", 1024), kw.get("json_mode", False)))
        return self.inner.complete(task, system, user, **kw)


def demo_request(today: date, base: ProcurementRequest) -> ProcurementRequest:
    """What the UI preset and scripts/seed_demo.py send: today + 14 days, created_at stamped by the API in UTC."""
    created = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7, minutes=3)
    return base.model_copy(update={"required_by": today + timedelta(days=14), "created_at": created})


def demo_prompts(today: date, request_, config) -> list[tuple]:
    """Runs the whole demo (mock extraction, replayed Decision Agent) with the clock at `today` and returns
    every decision-agent prompt in order: rationale, re-score explanation + rationale, replan explanation + rationale."""
    explain, explain_diff = _Recorder(replay_client(RATIONALE_TASK)), _Recorder(replay_client(CHANGE_TASK))
    agents = build_agents(Settings(MODE="mock"))
    agents["decision"] = LiveDecisionAgent(explain, Settings(MODE="mock"), llm_fast=explain_diff)
    clock = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7, minutes=3)
    orch = Orchestrator(agents, RunStore(), now=lambda: clock)

    run = orch.create_run(demo_request(today, request_), config)
    orch.add_documents(run.run_id, DOCS)
    run = orch.run_evaluation(run.run_id)
    assert run.state == S.CALC_MISMATCH
    run = orch.confirm_quote_math(run.run_id, run.pending_human.quote_ids[0], use_computed=True)
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_b"
    orch.start_negotiation(run.run_id)
    run = approve_all(orch, run.run_id)
    assert run.state == S.RECOMMENDED
    run = orch.interrupt(run.run_id, quantity=5000, budget=D("75000"), reason="Customer order upsized")
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_c"
    assert orch.agents["decision"].last_fallback is None, "every call answered from the cache"
    return sorted(explain.calls + explain_diff.calls, key=lambda c: (c[0], c[2]))


def test_decision_prompts_are_identical_on_any_day(request_, config):
    prompts = {today: demo_prompts(today, request_, config) for today in TODAYS}
    a, b = (prompts[t] for t in TODAYS)
    assert len(a) == 5, [t for t, *_ in a]  # 3 rationales + 2 change explanations
    assert a == b, "the demo request's prompts depend on the calendar"
    for task, system, user, max_tokens, json_mode in a:
        assert not ISO_DATE.search(user), f"a date leaked into the {task} input: {user}"
        assert "required_by" not in user and "created_at" not in user and "validity" not in user
        assert json.loads(payload_of(user))  # still the compact JSON the model was recorded with
        key = cache_key(recorded_model(task), task, system, user, max_tokens, json_mode)
        assert (CACHE_DIR / task / f"{key}.json").exists(), f"{task} prompt not in the demo cache"


@pytest.mark.parametrize("today", TODAYS)
def test_delivery_window_is_14_days_from_the_preset_on_any_day(today, request_, config):
    prompts = demo_prompts(today, request_, config)
    rationales = [json.loads(payload_of(user)) for task, _, user, *_ in prompts if task == RATIONALE_TASK]
    assert [p["request"]["delivery_window_days"] for p in rationales] == [14, 14, 14]


def test_extraction_prompt_is_the_document_text_only():
    """Same text → same cache key, whatever the doc id, filename or day it was uploaded."""
    recorder = _Recorder(ReplayCache(_NoNetwork(), model=recorded_model(), mode="replay_only", cache_dir=CACHE_DIR))
    agent = LiveDocumentAgent(recorder)
    keys = []
    for name in DOCS_ON_DISK:
        doc = as_document(SYNTHETIC / name)
        for doc_id, filename in (("doc-aaaaaaaa", name), ("doc-12345678", f"renamed-{name}")):
            twin = RawDocument(doc_id=doc_id, filename=filename, source=doc.source, text=doc.text)
            agent.extract(twin)
            task, system, user, max_tokens, json_mode = recorder.calls[-1]
            assert task == EXTRACT_TASK and user.count(doc.text) == 1
            assert doc_id not in user and filename not in user and not ISO_DATE.search(user.replace(doc.text, ""))
            keys.append(cache_key(recorded_model(), task, system, user, max_tokens, json_mode))
        assert keys[-1] == keys[-2], f"{name}: the extraction prompt depends on more than the text"
        assert (CACHE_DIR / EXTRACT_TASK / f"{keys[-1]}.json").exists()


DOCS_ON_DISK = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
