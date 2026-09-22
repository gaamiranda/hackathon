"""Live Negotiation Agent, replayed from data/llm_cache/ — real Claude drafts, no network (T20).

The four demo drafts and the four verdicts are the responses the gateway actually returned through
OpenClaw on 2026-09-22; the guard, leakage and fallback paths use stub clients, because no real
response produces them. Every consequential number still comes from the engine, so the replayed loop
must end on exactly the mock agent's ranking and landed costs.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from procureai.agents.factory import build_agents
from procureai.agents.mock import MockNegotiationAgent
from procureai.agents.negotiation import (
    DRAFT_TASK,
    VERDICT_TASK,
    LiveNegotiationAgent,
    allowed_verdicts,
)
from procureai.agents.prompts.negotiation import (
    DRAFT_RULES_REMINDER,
    REPLY_CLOSE,
    REPLY_OPEN,
    VERDICT_RULES_REMINDER,
    payload_of,
)
from procureai.config.settings import Settings
from procureai.domain.models import (
    NegotiationBoundaries,
    NegotiationOffer,
    NegotiationRole,
    NegotiationStatus,
    NegotiationThread,
    NegotiationTurn,
    PendingHumanKind,
    WorkflowState as S,
)
from procureai.llm.base import LLMUnavailable
from procureai.llm.cache import CACHE_DIR, ReplayCache
from procureai.llm.mock import MockLLMClient
from procureai.workflow import Orchestrator, RunStore, WorkflowError
from tests.test_decision_agent import _NoNetwork, _Spy
from tests.test_document_agent import recorded_model
from tests.test_negotiation import (  # noqa: F401
    INJECTION,
    NAMES,
    PRICES,
    approve_all,
    assert_no_cross_supplier_leak,
    config,
    offers,
    recommended_run,
    request_,
)

SETTINGS = Settings(MODE="mock")

# What each recorded draft must ask for: the engine's target price, and the supplier it is addressed to.
DEMO_DRAFTS = [("sup_b", 1, "11.78"), ("sup_b", 2, "11.55"), ("sup_c", 1, "12.33"), ("sup_c", 2, "12.06")]


def replay_client() -> ReplayCache:
    """One client for both tasks: they were recorded against the same model, keyed per task on disk."""
    model = recorded_model(DRAFT_TASK)
    assert recorded_model(VERDICT_TASK) == model, "the two negotiation tasks must share a model"
    return ReplayCache(_NoNetwork(), model=model, mode="replay_only", cache_dir=CACHE_DIR)


@pytest.fixture
def spy() -> _Spy:
    return _Spy(replay_client())


@pytest.fixture
def orch(spy) -> Orchestrator:
    """Mock pipeline with the live Negotiation Agent replaying the recorded demo calls."""
    agents = build_agents(SETTINGS)
    agents["negotiation"] = LiveNegotiationAgent(spy, SETTINGS)
    return Orchestrator(agents, RunStore())


def stub_orch(llm) -> Orchestrator:
    agents = build_agents(SETTINGS)
    agents["negotiation"] = LiveNegotiationAgent(llm, SETTINGS)
    return Orchestrator(agents, RunStore())


def drafts_of(orch: Orchestrator, run_id: str) -> list[tuple[str, int, str]]:
    return [(e.payload["supplier_id"], e.payload["round"], e.payload["draft"])
            for e in orch.store.events(run_id) if e.type == "negotiation.drafted"]


def failures(orch: Orchestrator, run_id: str) -> list[dict]:
    return [e.payload for e in orch.store.events(run_id) if e.type == "agent.failed"]


# --- the recorded demo drafts ----------------------------------------------------------------------


def test_recorded_drafts_ask_for_the_target_and_name_no_other_supplier(orch, request_, config):
    """G3 by grep: each draft carries its own target price and nothing belonging to anyone else."""
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)
    assert run.state == S.RECOMMENDED

    written = drafts_of(orch, run_id)
    assert [(sid, rnd) for sid, rnd, _ in written] == [(sid, rnd) for sid, rnd, _ in DEMO_DRAFTS]
    for (sid, rnd, target), (_, _, draft) in zip(DEMO_DRAFTS, written):
        assert draft.startswith(f"Dear {NAMES[sid]}"), draft[:60]
        assert target in draft, f"{sid} round {rnd} does not ask for {target}: {draft}"
        assert "\n" in draft and 40 <= len(draft.split()) <= 160, "reads as an email body, not a paragraph fragment"
        for other in set(NAMES) - {sid}:
            assert NAMES[other] not in draft, f"{sid} round {rnd} names {NAMES[other]}"
            for price in PRICES[other] - PRICES[sid]:
                assert price not in draft, f"{sid} round {rnd} leaks {other}'s price {price}"
    assert not failures(orch, run_id), "every recorded answer is used as answered"
    assert assert_no_cross_supplier_leak(orch, run_id) > 20


def test_the_model_only_ever_sees_one_supplier_and_delimited_reply_text(orch, spy, request_, config):
    """No other supplier, no score, no landed cost, no date in a prompt; the reply stays data (G4)."""
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)
    supplier_names = {q.supplier_id: q.supplier_name for q in run.quotes}

    assert [task for task, _ in spy.calls] == [DRAFT_TASK, VERDICT_TASK] * 4
    for i, (task, user) in enumerate(spy.calls):
        own = "sup_b" if i < 4 else "sup_c"
        payload = json.loads(payload_of(user))
        if task == DRAFT_TASK:
            assert user.endswith(DRAFT_RULES_REMINDER)
            assert payload["supplier_name"] == supplier_names[own]
        else:
            assert user.endswith(VERDICT_RULES_REMINDER)
            assert user.count(REPLY_OPEN) == 1 and user.count(REPLY_CLOSE) == 1
            assert "supplier_name" not in user, "the verdict task judges offers, not identities"
        for other in set(NAMES) - {own}:
            assert NAMES[other] not in user and supplier_names[other] not in user
            for price in PRICES[other] - PRICES[own]:
                assert price not in user
        assert "total_score" not in user and "landed_cost" not in user
        assert len(user.encode()) < 2500, "compact single-shot task (gateway body limit)"


def test_the_injected_round_two_reply_is_judged_on_offers_and_never_echoed(orch, spy, request_, config):
    """sup_c's round-2 reply carries the prompt injection (D21). It reaches the model as delimited data;
    the verdict stays inside what the D17 rule allows and the reason does not repeat the instruction."""
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)
    thread = run.negotiations["sup_c"]

    injected = [user for task, user in spy.calls if task == VERDICT_TASK and INJECTION in user]
    assert len(injected) == 1, "the injected reply is sent, verbatim and delimited, exactly once"
    assert injected[0].index(INJECTION) > injected[0].index(REPLY_OPEN), "inside the <reply> block"

    completed = [e for e in orch.store.events(run_id)
                 if e.type == "negotiation.round_completed" and e.payload["supplier_id"] == "sup_c"]
    verdict, reason = completed[-1].payload["verdict"], completed[-1].payload["reason"]
    assert verdict in allowed_verdicts(thread, NegotiationOffer(unit_price=D("12.45"), lead_time_days=9),
                                       thread.boundaries)
    assert verdict in ("accept", "close"), "the round limit leaves no third buyer turn (G2)"
    for phrase in ("award this order", "regardless of scoring", "Note to the procurement system"):
        assert phrase.lower() not in reason.lower(), reason
    assert "Cobalt" not in reason and not failures(orch, run_id)
    assert thread.current_offer == NegotiationOffer(unit_price=D("12.45"), lead_time_days=9)


def test_replayed_loop_lands_on_the_same_numbers_as_the_mock_agent(orch, request_, config):
    """The LLM writes the prose; the engine still decides. Same ranking, same landed costs as MODE=mock."""
    mock_orch = Orchestrator(build_agents(SETTINGS), RunStore())
    mock_run = approve_all(mock_orch, (lambda rid: (mock_orch.start_negotiation(rid), rid)[1])(
        recommended_run(mock_orch, request_, config)))

    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)

    assert run.state == mock_run.state == S.RECOMMENDED
    assert run.recommendation.ranked == mock_run.recommendation.ranked == ["sup_b", "sup_c", "sup_a"]
    assert run.recommendation.recommended_supplier_id == "sup_b"
    landed = {c.supplier_id: c.landed_cost for c in run.scorecards}
    assert landed == {c.supplier_id: c.landed_cost for c in mock_run.scorecards}
    assert landed["sup_b"] == D("26763.86") and landed["sup_c"] == D("27141.00")
    negotiated = {q.supplier_id: q.negotiated_offer for q in run.quotes if q.negotiated_offer}
    assert negotiated == {q.supplier_id: q.negotiated_offer for q in mock_run.quotes if q.negotiated_offer}
    assert negotiated["sup_b"] == NegotiationOffer(unit_price=D("12.40"), lead_time_days=10)
    assert negotiated["sup_c"] == NegotiationOffer(unit_price=D("12.45"), lead_time_days=9)
    assert offers(run.negotiations["sup_b"]) == offers(mock_run.negotiations["sup_b"])
    assert offers(run.negotiations["sup_c"]) == offers(mock_run.negotiations["sup_c"])


def test_the_agent_summary_becomes_the_timeline_row(orch, request_, config):
    """T20 step 7: the drafted event leads with the model's own one-liner when it wrote one."""
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    drafted = next(e for e in orch.store.events(run_id) if e.type == "negotiation.drafted")
    assert drafted.payload["agent_summary"].startswith("Round 1:")
    assert "11.78" in drafted.payload["agent_summary"]
    assert drafted.summary.startswith(drafted.payload["agent_summary"])
    assert drafted.summary.endswith("draft passed the outbound policy filter")


# --- outbound guardrails: nothing leaky reaches the human gate (G3) ---------------------------------


LEAK_WITH_A_FOREIGN_NUMBER = (
    "Dear Borealis Manufacturing AS,\n\nApex offered 11.20 per unit, so we need 11.78 from you.\n\nRegards")
LEAK_WITH_ONLY_ALLOWED_NUMBERS = (
    "Dear Borealis Manufacturing AS,\n\nApex has already offered us 11.78 per unit; can you match their "
    "terms?\n\nRegards")


def test_a_draft_naming_a_competitor_and_its_price_never_reaches_the_gate(request_, config):
    """The number guard fires first here: 11.20 is not in this supplier's input, so the whole draft is
    discarded before the policy filter ever sees it, and the human gate shows the templated message."""
    llm = MockLLMClient(parsed_json={"message": LEAK_WITH_A_FOREIGN_NUMBER, "summary": "leaked"})
    orch = stub_orch(llm)
    run_id = recommended_run(orch, request_, config)
    run = orch.start_negotiation(run_id)

    assert run.state == S.AWAITING_NEGOTIATION_APPROVAL
    draft = run.pending_human.details["draft"]
    assert "Apex" not in draft and "11.20" not in draft
    assert draft == MockNegotiationAgent().draft(
        run.request, next(q for q in run.quotes if q.supplier_id == "sup_b"),
        run.negotiations["sup_b"], run.config.negotiation)["message"]
    failed = failures(orch, run_id)
    assert len(failed) == 1 and failed[0]["agent"] == "negotiation"
    assert failed[0]["reason"] == "guard_trip" and "11.20" in failed[0]["detail"]
    assert not any(e.type == "negotiation.policy_blocked" for e in orch.store.events(run_id))


def test_a_draft_naming_a_competitor_with_allowed_numbers_is_blocked_by_the_policy_filter(request_, config):
    """Same leak without a foreign figure: the guard passes it, the deterministic outbound filter stops
    it (competitor name + "match their"), the thread escalates and no draft is ever offered to a human."""
    llm = MockLLMClient(parsed_json={"message": LEAK_WITH_ONLY_ALLOWED_NUMBERS, "summary": "leaked"})
    orch = stub_orch(llm)
    run_id = recommended_run(orch, request_, config)
    with pytest.raises(WorkflowError) as exc:
        orch.start_negotiation(run_id)
    assert exc.value.code == "policy_violation"

    run = orch.store.get(run_id)
    assert run.pending_human is None and run.state == S.RECOMMENDED
    assert run.negotiations["sup_b"].status == NegotiationStatus.ESCALATED
    blocked = next(e for e in orch.store.events(run_id) if e.type == "negotiation.policy_blocked")
    assert blocked.payload["source"] == "agent_draft"
    assert any("Apex" in v for v in blocked.payload["violations"])
    assert not failures(orch, run_id), "the model answered; it was the policy filter that objected"


@pytest.mark.parametrize("message, bad", [
    ("Dear Borealis Manufacturing AS, at 11.78 per unit this is a 5% saving for us.", "5%"),
    ("Dear Borealis Manufacturing AS, we can commit to 11.78 per unit across 3 further orders.", "3"),
    ("Dear Borealis Manufacturing AS, please confirm 11.79 per unit within 8 days.", "11.79"),
], ids=["invented-saving", "invented-commitment", "altered-price"])
def test_the_number_guard_discards_a_draft_that_invents_a_figure(message, bad, request_, config):
    llm = MockLLMClient(parsed_json={"message": message, "summary": "asked"})
    orch = stub_orch(llm)
    run_id = recommended_run(orch, request_, config)
    run = orch.start_negotiation(run_id)

    failed = failures(orch, run_id)
    assert len(failed) == 1 and failed[0]["reason"] == "guard_trip"
    assert bad in failed[0]["detail"]
    assert failed[0]["detail"].startswith("draft: numbers not in the input")
    assert bad not in run.pending_human.details["draft"]
    finished = [e for e in orch.store.events(run_id)
                if e.type == "agent.finished" and e.payload.get("agent") == "negotiation"]
    assert finished[-1].payload["fallback"] is True


# --- the verdict stays inside what the deterministic rule allows (D17, G2) --------------------------


def test_allowed_verdicts_mirror_the_rule():
    """The set the model may pick from is exactly the rule's own room for manoeuvre."""
    b = NegotiationBoundaries()
    original = NegotiationOffer(unit_price=D("12.80"), lead_time_days=10)
    ask = NegotiationOffer(unit_price=D("11.78"), lead_time_days=8)
    now = datetime.now(timezone.utc)

    def thread(turns: int) -> NegotiationThread:
        t = NegotiationThread(run_id="r", supplier_id="sup_b", boundaries=b, original_offer=original,
                              current_offer=original)
        for _ in range(turns):
            t.turns.append(NegotiationTurn(role=NegotiationRole.BUYER, message="ask", offer=ask, ts=now))
            t.turns.append(NegotiationTurn(role=NegotiationRole.SUPPLIER, message="reply",
                                           offer=NegotiationOffer(unit_price=D("12.55"), lead_time_days=10), ts=now))
        return t

    poor = NegotiationOffer(unit_price=D("12.55"), lead_time_days=10)   # less than half the ask
    good = NegotiationOffer(unit_price=D("12.00"), lead_time_days=10)   # more than half the ask
    assert allowed_verdicts(thread(1), poor, b) == {"accept", "counter", "close"}
    assert allowed_verdicts(thread(1), good, b) == {"accept", "close"}, "a counter that meets the ask is taken"
    assert allowed_verdicts(thread(1), None, b) == {"counter", "close"}, "nothing to accept"
    assert allowed_verdicts(thread(2), poor, b) == {"accept", "close"}, "no third buyer turn (G2)"
    assert allowed_verdicts(thread(2), None, b) == {"close"}


def test_a_verdict_the_rule_forbids_is_replaced_by_the_rule(request_, config):
    """"counter" at the round limit is not the agent's to give: the rule's verdict stands, the run says so."""
    always_counter = MockLLMClient(parsed_json={"verdict": "counter", "reason": "One more push should work."})
    llm = MockLLMClient(by_task={
        DRAFT_TASK: (json.dumps({"message": "Dear Borealis Manufacturing AS, 11.78 per unit within 8 days please.",
                                 "summary": "asked"}), None),
        VERDICT_TASK: (always_counter.text, None),
    })
    orch = stub_orch(llm)
    run_id = recommended_run(orch, request_, config)
    orch.start_negotiation(run_id)
    run = approve_all(orch, run_id)

    verdicts = [(e.payload["supplier_id"], e.payload["round"], e.payload["verdict"])
                for e in orch.store.events(run_id) if e.type == "negotiation.round_completed"]
    assert verdicts == [("sup_b", 1, "counter"), ("sup_b", 2, "accept"),
                        ("sup_c", 1, "counter"), ("sup_c", 2, "accept")]
    overridden = [f for f in failures(orch, run_id) if f["reason"] == "inconsistent_verdict"]
    assert len(overridden) == 2, "once per supplier, on the round-limit reply"
    assert "'counter' not in ['accept', 'close']" in overridden[0]["detail"]
    assert "rule says 'accept'" in overridden[0]["detail"]
    reasons = {(e.payload["supplier_id"], e.payload["round"]): e.payload.get("reason")
               for e in orch.store.events(run_id) if e.type == "negotiation.round_completed"}
    assert reasons[("sup_b", 1)] == "One more push should work.", "round 1 may counter, so the answer stands"
    assert reasons[("sup_b", 2)] is None and reasons[("sup_c", 2)] is None, "a discarded answer gives no reason"
    # G2 still holds and the numbers are the mock's
    for sid in ("sup_b", "sup_c"):
        assert len([t for t in run.negotiations[sid].turns if t.approved_by_human]) == 2
    assert {c.supplier_id: c.landed_cost for c in run.scorecards}["sup_b"] == D("26763.86")


class _Down:
    def complete(self, *args, **kwargs):
        raise LLMUnavailable("gateway returned 429: quota exhausted", status=429)


@pytest.mark.parametrize("llm, reason, summary", [
    (MockLLMClient("Sorry, I cannot help with that."), "parse_error",
     "LLM returned no usable JSON; using the templated message"),
    (MockLLMClient('{"unexpected": "shape"}'), "parse_error",
     "LLM returned no usable JSON; using the templated message"),
    (_Down(), "llm_unavailable", "LLM gateway unavailable; using the templated message"),
], ids=["garbage", "wrong-keys", "unavailable"])
def test_an_unusable_answer_falls_back_to_the_templated_agent(llm, reason, summary, request_, config):
    """G6: the demo survives a bad answer or a dead gateway on exactly the mock agent's numbers."""
    orch = stub_orch(llm)
    mock_orch = Orchestrator(build_agents(SETTINGS), RunStore())
    run_id, mock_id = recommended_run(orch, request_, config), recommended_run(mock_orch, request_, config)
    orch.start_negotiation(run_id)
    mock_orch.start_negotiation(mock_id)
    run, mock_run = approve_all(orch, run_id), approve_all(mock_orch, mock_id)

    assert run.state == S.RECOMMENDED
    assert drafts_of(orch, run_id) == drafts_of(mock_orch, mock_id)
    assert run.recommendation.ranked == mock_run.recommendation.ranked
    assert [c.landed_cost for c in run.scorecards] == [c.landed_cost for c in mock_run.scorecards]

    failed = failures(orch, run_id)
    assert len(failed) == 8 and {f["reason"] for f in failed} == {reason}
    first = next(e for e in orch.store.events(run_id) if e.type == "agent.failed")
    assert first.actor == "agent" and first.summary == summary
    following = orch.store.events(run_id)[orch.store.events(run_id).index(first) + 1]
    assert following.type == "agent.finished" and following.payload["fallback"] is True


def test_the_pending_gate_still_carries_the_engine_target(orch, request_, config):
    """Whatever the model wrote, the offer on the gate is the engine's (D17), and the human still approves."""
    run_id = recommended_run(orch, request_, config)
    run = orch.start_negotiation(run_id)
    pending = run.pending_human
    assert pending.kind == PendingHumanKind.NEGOTIATION_APPROVAL
    assert pending.details["target_offer"] == {"unit_price": "11.78", "lead_time_days": 8}
    assert run.negotiations["sup_b"].turns == [], "nothing is sent before a human approves (G5)"


def test_live_agent_is_selected_in_live_mode_only():
    assert isinstance(build_agents(Settings(MODE="mock"))["negotiation"], MockNegotiationAgent)
    live = build_agents(Settings(MODE="live", LLM_GATEWAY_URL="http://gateway.invalid",
                                 LLM_GATEWAY_API_KEY="x"))["negotiation"]
    assert isinstance(live, LiveNegotiationAgent)
    assert isinstance(live.fallback, MockNegotiationAgent), "the templated agent stays as the fallback (G6)"
