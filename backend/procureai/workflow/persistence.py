"""File-backed run persistence (PLAN.md D5: JSON files behind a repository interface; T19).

Layout, one directory per run under RUN_STORE_DIR:

    <dir>/<run_id>/run.json       the full Run (pydantic dump), rewritten atomically on every change
    <dir>/<run_id>/events.jsonl   one WorkflowEvent per line, append-only, fsync'd per event

Only RunStore (via `RunRepository`) writes here; the orchestrator never touches files. Both files are
written inside the store's lock, in the order events.jsonl → run.json, so after any event the run.json
on disk reflects at least that event's state_after.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from procureai.domain.models import EventActor, Run, WorkflowEvent, WorkflowState as S
from procureai.workflow.events import EventBus
from procureai.workflow.store import RunStore

log = logging.getLogger(__name__)

RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"


class RunRepository(Protocol):
    def save_run(self, run: Run) -> None: ...

    def append_event(self, event: WorkflowEvent) -> None: ...


class FileRunRepository:
    """JSON files under `root`. save_run is atomic (temp file + os.replace); append_event is durable
    (flush + fsync). load() validates everything through the pydantic models and skips what does not parse."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    # ---------------------------------------------------------------- writes

    def save_run(self, run: Run) -> None:
        directory = self.run_dir(run.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / RUN_FILE
        tmp = directory / f".{RUN_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(run.model_dump_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    def append_event(self, event: WorkflowEvent) -> None:
        directory = self.run_dir(event.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ---------------------------------------------------------------- reads

    def load(self) -> list[tuple[Run, list[WorkflowEvent]]]:
        """Every run directory that holds a valid run.json, with its events sorted by seq.
        A directory whose run.json is missing or invalid is logged and skipped; an event line that does
        not parse (e.g. a write cut short by a crash) is logged and dropped, the rest of the log is kept."""
        loaded: list[tuple[Run, list[WorkflowEvent]]] = []
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            run_file = directory / RUN_FILE
            try:
                run = Run.model_validate_json(run_file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 — any corrupt directory must not stop startup
                log.warning("skipping run directory %s: %s: %s", directory.name, type(exc).__name__, str(exc)[:200])
                continue
            if run.run_id != directory.name:
                log.warning("skipping run directory %s: run.json is for %r", directory.name, run.run_id)
                continue
            loaded.append((run, self._load_events(directory / EVENTS_FILE, run.run_id)))
        return loaded

    def _load_events(self, path: Path, run_id: str) -> list[WorkflowEvent]:
        events: dict[int, WorkflowEvent] = {}
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    event = WorkflowEvent.model_validate_json(line)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: dropping events.jsonl line %d: %s", run_id, lineno, type(exc).__name__)
                    continue
                if event.run_id != run_id:
                    log.warning("%s: dropping events.jsonl line %d: event belongs to %r", run_id, lineno, event.run_id)
                    continue
                events.setdefault(event.seq, event)  # a duplicate seq keeps the first copy
        return [events[seq] for seq in sorted(events)]


# ---------------------------------------------------------------- startup recovery

# States a run can legitimately sit in between API calls. Loaded as-is; a waiting state keeps its
# pending_human (and po_preview) and is immediately usable.
RESTING_STATES = frozenset({S.CREATED, S.EXTRACTED, S.RECOMMENDED, S.PO_GENERATED})
WAITING_STATES = frozenset({S.NEEDS_HUMAN_EXTRACTION, S.CALC_MISMATCH, S.AWAITING_NEGOTIATION_APPROVAL, S.AWAITING_PO_APPROVAL})
# States that exist only inside one orchestrator call. Found at load time they mean the process died
# mid-transition (the call's in-memory context — agents' partial output, the _Replan snapshot — is gone).
TRANSIENT_STATES = frozenset(S) - RESTING_STATES - WAITING_STATES


def recovery_target(run: Run) -> S:
    """Nearest safe resting state for a run interrupted in a transient state:

        scorecards present (SCORING/RE_SCORING/REPLANNING/NEGOTIATING… after a first evaluation) → RECOMMENDED
        quotes present, no scorecards (EXTRACTING/VALIDATING/ENRICHING on a first pass)             → EXTRACTED
        nothing extracted yet (EXTRACTING of the first documents)                                    → CREATED

    RECOMMENDED keeps whatever scorecards/recommendation were last completed, which may predate an interrupted
    replan or negotiation round (request_po then refuses with stale_recommendation; an interrupt re-plans).
    EXTRACTED lets the human call POST /runs/{id}/evaluate again; the partially extracted quotes of an
    EXTRACTING run are kept, the documents whose extraction never finished can be re-added or replaced."""
    if run.scorecards:
        return S.RECOMMENDED
    if run.quotes:
        return S.EXTRACTED
    return S.CREATED


def restore_runs(store: RunStore, events: EventBus, repository: FileRunRepository,
                 now: Callable[[], datetime] | None = None) -> int:
    """Load every persisted run into `store` (rebuilding the per-run seq counters from the last event) and
    recover the ones caught in a transient state with a `run.recovered` event. Returns the number loaded."""
    now = now or (lambda: datetime.now(timezone.utc))
    loaded = repository.load()
    with store.lock:
        for run, run_events in loaded:
            store.restore(run, run_events)
            if run.state in TRANSIENT_STATES:
                interrupted = run.state
                target = recovery_target(run)
                run.state = target
                run.pending_human = None
                run.po_preview = None
                run.updated_at = now()
                events.emit(run, EventActor.ENGINE, "run.recovered",
                            f"backend restarted during {interrupted}; human should re-trigger evaluation",
                            {"interrupted_state": interrupted, "recovered_state": target},
                            state_before=interrupted, state_after=target)
                log.warning("%s: recovered from %s to %s", run.run_id, interrupted, target)
    return len(loaded)
