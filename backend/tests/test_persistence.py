"""Run persistence (T19): JSON files under RUN_STORE_DIR survive a backend restart. Mock mode, no network."""

import json
import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procureai.agents.factory import build_agents
from procureai.config.settings import Settings, get_settings
from procureai.domain.models import (
    PendingHumanKind,
    ProcurementConfig,
    ProcurementRequest,
    QuoteSource,
    RawDocument,
    Run,
    WorkflowState as S,
)
from procureai.workflow import EventBus, FileRunRepository, Orchestrator, RunStore, restore_runs
from procureai.workflow.persistence import EVENTS_FILE, RUN_FILE, TRANSIENT_STATES, recovery_target

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND.parent / "data" / "fixtures"
sys.path.insert(0, str(BACKEND / "scripts"))
from print_event_log import run_mismatch_demo  # noqa: E402

DOCS = [
    RawDocument(doc_id="doc-a", filename="supplier_a_apex.pdf", source=QuoteSource.PDF, text="..."),
    RawDocument(doc_id="doc-b", filename="supplier_b_borealis.xlsx", source=QuoteSource.XLSX, text="..."),
    RawDocument(doc_id="doc-c", filename="supplier_c_cobalt.eml.txt", source=QuoteSource.EMAIL, text="..."),
]


def make_orch(store_dir: Path | None) -> Orchestrator:
    """What api/deps.build_orchestrator does, without settings: optional repository + startup restore."""
    repository = FileRunRepository(store_dir) if store_dir is not None else None
    store = RunStore(repository)
    events = EventBus(store)
    if repository is not None:
        restore_runs(store, events, repository)
    return Orchestrator(build_agents(Settings(MODE="mock")), store, events=events)


@pytest.fixture
def request_() -> ProcurementRequest:
    return ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())


@pytest.fixture
def config() -> ProcurementConfig:
    return ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())


def types_and_seqs(orch: Orchestrator, run_id: str) -> list[tuple[int, str]]:
    return [(e.seq, e.type) for e in orch.store.events(run_id)]


def sse_replay(store_dir: Path, run_id: str) -> list[dict]:
    """GET /runs/{id}/events/stream?follow=false against an app booted from `store_dir`; returns the data frames."""
    from procureai.api.app import app

    os.environ["RUN_STORE_DIR"] = str(store_dir)
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/health").json()["runs_persisted"] is True
            r = client.get(f"/runs/{run_id}/events/stream", params={"follow": "false"})
            assert r.status_code == 200
            frames = [json.loads(line.removeprefix("data: ")) for line in r.text.splitlines() if line.startswith("data: ")]
            ids = [int(line.removeprefix("id: ")) for line in r.text.splitlines() if line.startswith("id: ")]
            assert ids == [f["seq"] for f in frames]
            return frames
    finally:
        os.environ.pop("RUN_STORE_DIR", None)
        get_settings.cache_clear()


# --------------------------------------------------------------------------- (a) round trip


def test_full_run_reloads_identically(tmp_path):
    orch = make_orch(tmp_path)
    run_id = run_mismatch_demo(orch)  # CALC_MISMATCH resolved → RECOMMENDED, ~40 events
    original = orch.store.get(run_id)
    assert original.state == S.RECOMMENDED
    run_dir = tmp_path / run_id
    assert (run_dir / RUN_FILE).exists() and (run_dir / EVENTS_FILE).exists()
    assert not list(run_dir.glob(".*.tmp")), "temp file left behind by save_run"
    assert Run.model_validate_json((run_dir / RUN_FILE).read_text()) == original  # disk == memory after the last call

    reloaded = make_orch(tmp_path)
    assert [r.run_id for r in reloaded.store.list_runs()] == [run_id]
    assert reloaded.store.get(run_id) == original
    assert types_and_seqs(reloaded, run_id) == types_and_seqs(orch, run_id)
    assert reloaded.store.events(run_id) == orch.store.events(run_id)

    frames = sse_replay(tmp_path, run_id)
    assert [(f["seq"], f["type"]) for f in frames] == types_and_seqs(orch, run_id)
    assert frames[-1]["state_after"] == "RECOMMENDED"


# --------------------------------------------------------------------------- (b) waiting state continues


def test_awaiting_negotiation_approval_continues_after_reload(tmp_path, request_, config):
    orch = make_orch(tmp_path)
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, DOCS)
    run = orch.run_evaluation(run.run_id)
    run = orch.confirm_quote_math(run.run_id, run.pending_human.quote_ids[0], use_computed=True)
    run = orch.start_negotiation(run.run_id)
    assert run.state == S.AWAITING_NEGOTIATION_APPROVAL
    pending = run.pending_human
    n_events = len(orch.store.events(run.run_id))

    reloaded = make_orch(tmp_path)
    run2 = reloaded.store.get(run.run_id)
    assert run2.state == S.AWAITING_NEGOTIATION_APPROVAL
    assert run2.pending_human == pending and run2.pending_human.kind == PendingHumanKind.NEGOTIATION_APPROVAL
    assert not any(e.type == "run.recovered" for e in reloaded.store.events(run.run_id))

    while run2.state == S.AWAITING_NEGOTIATION_APPROVAL:
        run2 = reloaded.approve_negotiation(run.run_id, run2.pending_human.details["supplier_id"])
    assert run2.state == S.RECOMMENDED and run2.recommendation.change_explanation
    assert all(t.status.value != "open" for t in run2.negotiations.values())
    run2 = reloaded.request_po(run.run_id)
    run2 = reloaded.approve_po(run.run_id, "Alice")
    assert run2.state == S.PO_GENERATED and run2.purchase_order.po_number

    # the continuation was persisted too: a third boot sees the finished run and a gap-free log
    third = make_orch(tmp_path)
    assert third.store.get(run.run_id) == run2
    seqs = [e.seq for e in third.store.events(run.run_id)]
    assert seqs == list(range(len(seqs))) and len(seqs) > n_events


# --------------------------------------------------------------------------- (c) corrupt directory skipped


def test_corrupt_run_is_skipped_and_logged(tmp_path, request_, config, caplog):
    orch = make_orch(tmp_path)
    good = orch.create_run(request_, config)
    bad = orch.create_run(request_, config)
    orch.add_documents(good.run_id, DOCS[1:])
    (tmp_path / bad.run_id / RUN_FILE).write_text('{"run_id": "%s", "state": "NOT_A_STATE"' % bad.run_id)  # truncated + invalid
    (tmp_path / "not-a-run").mkdir()  # no run.json at all
    (tmp_path / "stray.txt").write_text("ignored")

    with caplog.at_level(logging.WARNING, logger="procureai.workflow.persistence"):
        reloaded = make_orch(tmp_path)
    assert [r.run_id for r in reloaded.store.list_runs()] == [good.run_id]
    assert reloaded.store.get(good.run_id) == orch.store.get(good.run_id)
    skipped = [rec.message for rec in caplog.records if "skipping run directory" in rec.message]
    assert any(bad.run_id in m for m in skipped) and any("not-a-run" in m for m in skipped)


def test_truncated_event_line_is_dropped_not_fatal(tmp_path, request_, config, caplog):
    orch = make_orch(tmp_path)
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, DOCS[1:])
    events_file = tmp_path / run.run_id / EVENTS_FILE
    full = events_file.read_text()
    events_file.write_text(full + full.splitlines()[-1][:40])  # a crash mid-append leaves a partial last line

    with caplog.at_level(logging.WARNING, logger="procureai.workflow.persistence"):
        reloaded = make_orch(tmp_path)
    assert reloaded.store.events(run.run_id) == orch.store.events(run.run_id)
    assert any("dropping events.jsonl line" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- (d) transient-state recovery


def _persist_in_state(tmp_path: Path, orch: Orchestrator, run_id: str, state: S) -> None:
    """Simulate a process that died mid-transition: run.json on disk shows the transient state."""
    run = orch.store.get(run_id).model_copy(update={"state": state})
    (tmp_path / run_id / RUN_FILE).write_text(run.model_dump_json())


@pytest.mark.parametrize("state", sorted(TRANSIENT_STATES))
def test_transient_state_is_recovered(tmp_path, state):
    orch = make_orch(tmp_path)
    run_id = run_mismatch_demo(orch)  # has quotes and scorecards
    last_seq = orch.store.events(run_id)[-1].seq
    _persist_in_state(tmp_path, orch, run_id, state)

    reloaded = make_orch(tmp_path)
    run = reloaded.store.get(run_id)
    assert run.state == S.RECOMMENDED and run.pending_human is None
    recovered = reloaded.store.events(run_id)[-1]
    assert recovered.type == "run.recovered" and recovered.actor.value == "engine" and recovered.seq == last_seq + 1
    assert recovered.state_before == state and recovered.state_after == S.RECOMMENDED
    assert recovered.summary == f"backend restarted during {state}; human should re-trigger evaluation"
    assert recovered.payload == {"interrupted_state": state, "recovered_state": "RECOMMENDED"}

    # the recovery is itself persisted: a second boot does not recover again
    again = make_orch(tmp_path)
    assert again.store.get(run_id) == run
    assert [e.type for e in again.store.events(run_id)].count("run.recovered") == 1
    assert again.request_po(run_id).state == S.AWAITING_PO_APPROVAL  # usable straight away


def test_recovery_mapping_without_scorecards(tmp_path, request_, config):
    orch = make_orch(tmp_path)
    run = orch.create_run(request_, config)
    _persist_in_state(tmp_path, orch, run.run_id, S.EXTRACTING)
    assert make_orch(tmp_path).store.get(run.run_id).state == S.CREATED

    orch.add_documents(run.run_id, DOCS[1:])
    _persist_in_state(tmp_path, orch, run.run_id, S.VALIDATING)
    reloaded = make_orch(tmp_path)
    run2 = reloaded.store.get(run.run_id)
    assert run2.state == S.EXTRACTED and len(run2.quotes) == 2
    assert reloaded.run_evaluation(run.run_id).state == S.RECOMMENDED  # human re-triggers evaluation


def test_recovery_target_mapping(request_, config):
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc)
    run = Run(run_id="r", request=request_, config=config, created_at=ts, updated_at=ts)
    assert recovery_target(run) == S.CREATED
    assert TRANSIENT_STATES == {S.EXTRACTING, S.VALIDATING, S.ENRICHING, S.SCORING, S.NEGOTIATION_DRAFTED,
                                S.NEGOTIATING, S.COUNTER_RECEIVED, S.RE_SCORING, S.REPLANNING}


def test_waiting_states_are_left_alone(tmp_path, request_, config):
    orch = make_orch(tmp_path)
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, DOCS)
    run = orch.run_evaluation(run.run_id)
    assert run.state == S.CALC_MISMATCH
    reloaded = make_orch(tmp_path)
    run2 = reloaded.store.get(run.run_id)
    assert run2.state == S.CALC_MISMATCH and run2.pending_human == run.pending_human
    assert reloaded.confirm_quote_math(run.run_id, run2.pending_human.quote_ids[0], use_computed=True).state == S.RECOMMENDED


# --------------------------------------------------------------------------- (e) seq continues


def test_seq_continues_after_reload(tmp_path, request_, config):
    orch = make_orch(tmp_path)
    run = orch.create_run(request_, config)
    orch.add_documents(run.run_id, DOCS[1:])
    persisted = [e.seq for e in orch.store.events(run.run_id)]
    assert persisted == list(range(len(persisted)))

    reloaded = make_orch(tmp_path)
    assert reloaded.store.next_seq(run.run_id) == len(persisted)
    reloaded.run_evaluation(run.run_id)
    seqs = [e.seq for e in reloaded.store.events(run.run_id)]
    assert seqs == list(range(len(seqs))) and len(seqs) > len(persisted)
    on_disk = [json.loads(line)["seq"] for line in (tmp_path / run.run_id / EVENTS_FILE).read_text().splitlines()]
    assert on_disk == seqs  # no duplicates, no gaps in the file either


# --------------------------------------------------------------------------- (f) unset → nothing written


def test_in_memory_store_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch = make_orch(None)
    assert orch.store.persisted is False
    run_mismatch_demo(orch)
    assert list(tmp_path.iterdir()) == []


def test_settings_default_is_in_memory():
    get_settings.cache_clear()
    assert Settings().RUN_STORE_DIR == ""
