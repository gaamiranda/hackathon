"""Live Decision Agent, replayed from data/llm_cache/ — real Claude prose, no network (T7).

The three narrative moments of the demo (initial ranking, after negotiation, after the interrupt)
replay the responses the gateway actually returned; the number guard and the fallback paths use
stub clients, plus one real recorded sentence that the guard rejected during T7.
"""

import json
from decimal import Decimal as D

import pytest

from procureai.agents.decision import LiveDecisionAgent, foreign_numbers, numbers_in
from procureai.agents.factory import build_agents
from procureai.agents.mock import MockDecisionAgent
from procureai.agents.prompts.decision import RULES_REMINDER, payload_of
from procureai.config.settings import Settings
from procureai.domain.models import WorkflowState as S
from procureai.llm.base import LLMUnavailable
from procureai.llm.cache import CACHE_DIR, ReplayCache
from procureai.llm.mock import MockLLMClient
from procureai.workflow import Orchestrator, RunStore
from tests.test_document_agent import recorded_model
from tests.test_interrupt import negotiated_run
from tests.test_negotiation import INJECTION, config, recommended_run, request_  # noqa: F401

SETTINGS = Settings(MODE="mock")
NAMES = ("Borealis", "Cobalt", "Apex")


class _NoNetwork:
    def complete(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("replay_only must never reach the gateway")


class _Spy:
    """Records every (task, user payload) the agent sends, so tests can check the guard's input."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def complete(self, task, system, user, **kw):
        self.calls.append((task, user))
        return self.inner.complete(task, system, user, **kw)


def replay_client(task: str) -> ReplayCache:
    return ReplayCache(_NoNetwork(), model=recorded_model(task), mode="replay_only", cache_dir=CACHE_DIR)


@pytest.fixture
def spy() -> _Spy:
    return _Spy(replay_client("explain"))


@pytest.fixture
def orch(spy) -> Orchestrator:
    """Mock pipeline with the live Decision Agent replaying recorded responses."""
    agents = build_agents(SETTINGS)
    agents["decision"] = LiveDecisionAgent(spy, SETTINGS, llm_fast=_Spy(replay_client("explain_diff")))
    return Orchestrator(agents, RunStore())


def assert_grounded(text: str, user_json: str) -> None:
    assert numbers_in(text), "the prose should quote engine figures"
    assert foreign_numbers(text, user_json) == set(), text


# --- the recorded demo moments --------------------------------------------------------------------


def test_initial_rationale_names_borealis_first_and_quotes_only_input_numbers(orch, spy, request_, config):
    run_id = recommended_run(orch, request_, config)
    rationale = orch.store.get(run_id).recommendation.rationale

    positions = {n: rationale.find(n) for n in NAMES}
    assert positions["Borealis"] == 0, rationale[:80]
    assert all(positions[n] >= 0 for n in NAMES), "every supplier is discussed"
    assert "Key trade-off:" in rationale
    assert [t for t, _ in spy.calls] == ["explain"], "no change explanation without diff lines"
    assert_grounded(rationale, spy.calls[0][1])


def test_post_interrupt_explanation_says_recommendation_changed_to_cobalt(orch, spy, request_, config):
    run_id = negotiated_run(orch, request_, config)
    fast = orch.agents["decision"].llm_fast
    run = orch.interrupt(run_id, quantity=5000, budget=D("75000"), reason="Customer order upsized")

    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_c"
    change = run.recommendation.change_explanation
    assert change.startswith("The recommended supplier changed from Borealis"), change
    assert "Cobalt Industrial" in change and "capacity" in change and "5,000" in change
    assert run.recommendation.rationale.startswith("Cobalt Industrial")
    assert "ineligible" in run.recommendation.rationale

    assert [t for t, _ in fast.calls] == ["explain_diff", "explain_diff"]  # after negotiation, after the interrupt
    assert_grounded(change, fast.calls[-1][1])
    assert_grounded(run.recommendation.rationale, spy.calls[-1][1])
    assert "Request changed" in json.loads(payload_of(fast.calls[-1][1]))["what_changed"][0]


def test_post_negotiation_explanation_keeps_borealis(orch, request_, config):
    run_id = negotiated_run(orch, request_, config)
    rec = orch.store.get(run_id).recommendation
    fast = orch.agents["decision"].llm_fast

    assert rec.change_explanation.startswith("The recommended supplier remains Borealis"), rec.change_explanation
    assert "12.40" in rec.change_explanation and "12.45" in rec.change_explanation
    assert_grounded(rec.change_explanation, fast.calls[0][1])
    assert json.loads(payload_of(fast.calls[0][1]))["what_changed"][0].startswith("Borealis Manufacturing AS (sup_b): negotiated unit price 12.80 to 12.40")


def test_only_engine_output_reaches_the_model(orch, spy, request_, config):
    """Scorecards, validated figures, history and diff lines — never document or supplier reply text (G4)."""
    run_id = negotiated_run(orch, request_, config)
    run = orch.store.get(run_id)
    fast = orch.agents["decision"].llm_fast
    reply_texts = [t.message for th in run.negotiations.values() for t in th.turns]
    for _, user in spy.calls + fast.calls:
        assert user.endswith(RULES_REMINDER), "the numbers rule closes every user message (D29)"
        payload = json.loads(payload_of(user))
        assert INJECTION not in user and "SYSTEM NOTE" not in user
        assert not any(q.raw_excerpt and q.raw_excerpt in user for q in run.quotes)
        assert not any(text in user for text in reply_texts)
        assert len(user.encode()) < 2500, "compact single-shot task (gateway body limit)"
        if "suppliers" in payload:
            assert {s["name"] for s in payload["suppliers"]} == {q.supplier_name for q in run.quotes}
            assert payload["request"]["delivery_window_days"] == 14 and "required_by" not in payload["request"]


# --- number guard (G1) -----------------------------------------------------------------------------


INPUT = json.dumps({
    "request": {"quantity": 2000, "budget": "30,000.00"},
    "score_scale": 100,
    "suppliers": [
        {"name": "Borealis", "landed_cost": "27,618.42", "unit_price": "12.80", "on_time_pct": 97.0, "total_score": 72.3,
         "ineligibility_reasons": ["quantity 5000 exceeds capacity 4000"]},
        {"name": "Apex", "landed_cost": "24,852.00", "defect_pct": 4.5, "lead_time_days": 13, "on_time_rate": 0.82},
    ],
})


def test_guard_rejects_a_fabricated_saving():
    text = "Borealis at 27,618.42 is recommended; choosing it over Apex saves $1,200 and keeps 97% on-time delivery."
    assert foreign_numbers(text, INPUT) == {"1,200"}


@pytest.mark.parametrize("text", [
    "landed cost 27618.42 versus 24852",             # separators and ".00" dropped
    "USD 27,618.42, budget 30,000.00, 2,000 units",  # separators kept
    "97% on-time, 4.5% defects, 82% on-time",        # percent sign; "82%" matches the fraction 0.82
    "score 72.3/100 with 13-day delivery; capacity 4,000 for 5,000 units",  # numbers inside input strings
    "12.8 per unit",                                 # trailing zero dropped
])
def test_guard_accepts_input_numbers_in_any_common_format(text):
    assert foreign_numbers(text, INPUT) == set()


@pytest.mark.parametrize(("text", "bad"), [
    ("scored 72 out of 100", {"72"}),                # rounding is inventing
    ("about 27,600 landed", {"27,600"}),
    ("a 5.2 point lead", {"5.2"}),
    ("delivery in 2 weeks", {"2"}),
    ("0.97 reliability", {"0.97"}),                  # 97.0 is in the input, 0.97 is a conversion
])
def test_guard_rejects_rounded_or_derived_numbers(text, bad):
    assert foreign_numbers(text, INPUT) == bad


def test_guard_caught_a_real_computed_difference(request_, config):
    """Verbatim from a Sonnet response recorded on 2026-09-19 (first prompt draft): the model
    subtracted two landed costs. The guard discards the whole answer and the run gets templated text."""
    computed = ("The choice prioritizes reliability and delivery consistency over lowest price, "
                "accepting 1911.86 higher cost than the cheapest option.")
    llm = MockLLMClient(parsed_json={"rationale": "Borealis Manufacturing AS is recommended with a total score of 66.3.",
                                     "key_tradeoff": computed, "escalation_note": None})
    agents = build_agents(SETTINGS)
    agents["decision"] = LiveDecisionAgent(llm, SETTINGS)
    orch = Orchestrator(agents, RunStore())
    run_id = negotiated_run(orch, request_, config)
    run = orch.store.get(run_id)

    post_negotiation_input = llm.calls[-2]["user"]  # explain, then explain_diff, on the re-score
    assert foreign_numbers(computed, post_negotiation_input) == {"1911.86"}
    templated = MockDecisionAgent().explain(run.request, run.scorecards, run.validated, None)["rationale"]
    assert run.recommendation.rationale == templated
    assert "1911.86" not in run.recommendation.rationale


# --- fallback (G6): the demo must survive a bad answer or a dead gateway ---------------------------


class _Down:
    def complete(self, *args, **kwargs):
        raise LLMUnavailable("gateway returned 429: quota exhausted", status=429)


@pytest.mark.parametrize("llm", [
    MockLLMClient("Sorry, I cannot help with that."),      # no JSON at all
    MockLLMClient('{"unexpected": "shape"}'),               # JSON without the keys
    MockLLMClient(parsed_json={"rationale": "   "}),        # empty rationale
    _Down(),                                                # LLMUnavailable
], ids=["garbage", "wrong-keys", "blank", "unavailable"])
def test_unusable_or_absent_answer_falls_back_to_templated_text(llm, request_, config):
    agents = build_agents(SETTINGS)
    agents["decision"] = LiveDecisionAgent(llm, SETTINGS)
    orch = Orchestrator(agents, RunStore())
    run_id = negotiated_run(orch, request_, config)  # covers the initial rationale and the re-score diff
    run = orch.store.get(run_id)

    finished = [e for e in orch.store.events(run_id) if e.type == "agent.finished" and e.payload.get("agent") == "decision"]
    diff = finished[-1].payload["diff"]
    templated = MockDecisionAgent().explain(run.request, run.scorecards, run.validated, diff)
    assert run.recommendation.rationale == templated["rationale"]
    assert run.recommendation.change_explanation == templated["change_explanation"]
    assert run.state == S.RECOMMENDED and run.recommendation.recommended_supplier_id == "sup_b"


@pytest.mark.parametrize("llm, reason, summary", [
    (MockLLMClient("Sorry, I cannot help with that."), "parse_error", "LLM returned no usable JSON; using deterministic text"),
    (MockLLMClient(parsed_json={"rationale": "Borealis saves you 999,999.00 overall."}), "guard_trip",
     "LLM output rejected by the number guard; using deterministic text"),
    (_Down(), "llm_unavailable", "LLM gateway unavailable; using deterministic text"),
], ids=["garbage", "guard", "unavailable"])
def test_fallback_is_surfaced_as_agent_failed_then_finished_with_fallback(llm, reason, summary, request_, config):
    """T16 / D26: every fallback shows up in the War Room as agent.failed {agent, reason, detail} immediately
    followed by the usual agent.finished with fallback=true; the recommendation itself is unaffected."""
    agents = build_agents(SETTINGS)
    agents["decision"] = LiveDecisionAgent(llm, SETTINGS)
    orch = Orchestrator(agents, RunStore())
    run_id = recommended_run(orch, request_, config)

    events = orch.store.events(run_id)
    failed = [e for e in events if e.type == "agent.failed"]
    assert len(failed) == 1 and failed[0].actor == "agent"
    assert failed[0].payload["agent"] == "decision" and failed[0].payload["reason"] == reason
    assert failed[0].payload["detail"].startswith("explain: ")
    assert failed[0].summary == summary
    following = events[events.index(failed[0]) + 1]
    assert following.type == "agent.finished" and following.payload["agent"] == "decision"
    assert following.payload["fallback"] is True
    if reason == "guard_trip":
        assert "999,999" in failed[0].payload["detail"] and following.payload["backend"] == "mock"
    else:
        assert following.payload["backend"] == ("mock" if reason == "parse_error" else "template")


def test_replayed_rationale_carries_no_fallback(orch, request_, config):
    run_id = recommended_run(orch, request_, config)
    events = orch.store.events(run_id)
    assert not [e for e in events if e.type == "agent.failed"]
    assert all("fallback" not in e.payload for e in events if e.type == "agent.finished")


def test_live_agent_is_selected_in_live_mode_only():
    assert isinstance(build_agents(Settings(MODE="mock"))["decision"], MockDecisionAgent)
    live = build_agents(Settings(MODE="live", LLM_GATEWAY_URL="http://gateway.invalid", LLM_GATEWAY_API_KEY="x"))["decision"]
    assert isinstance(live, LiveDecisionAgent)
    assert live.llm is not live.llm_fast, "change explanations get their own (LLM_MODEL_FAST) client"
