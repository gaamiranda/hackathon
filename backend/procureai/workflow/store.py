"""In-memory run + event store (PLAN.md D5: Week 1 storage), optionally mirrored to a repository (T19).

The dicts stay the source of truth for reads; the repository (workflow/persistence.py) only receives
writes, inside the same lock, so what is on disk never runs ahead of or behind memory.
"""

import threading
from typing import TYPE_CHECKING

from procureai.domain.models import Run, WorkflowEvent
from procureai.workflow.errors import WorkflowError

if TYPE_CHECKING:
    from procureai.workflow.persistence import RunRepository


class RunStore:
    def __init__(self, repository: "RunRepository | None" = None) -> None:
        self.lock = threading.RLock()
        self.repository = repository
        self._runs: dict[str, Run] = {}
        self._events: dict[str, list[WorkflowEvent]] = {}

    @property
    def persisted(self) -> bool:
        return self.repository is not None

    def put(self, run: Run) -> Run:
        with self.lock:
            self._runs[run.run_id] = run
            self._events.setdefault(run.run_id, [])
            if self.repository is not None:
                self.repository.save_run(run)
        return run

    def get(self, run_id: str) -> Run:
        with self.lock:
            try:
                return self._runs[run_id]
            except KeyError:
                raise WorkflowError("run_not_found", f"unknown run_id {run_id!r}") from None

    def list_runs(self) -> list[Run]:
        with self.lock:
            return sorted(self._runs.values(), key=lambda r: r.created_at)

    def append_event(self, event: WorkflowEvent) -> None:
        with self.lock:
            self._events.setdefault(event.run_id, []).append(event)
            if self.repository is not None:
                self.repository.append_event(event)

    def events(self, run_id: str, after_seq: int = -1) -> list[WorkflowEvent]:
        with self.lock:
            return [e for e in self._events.get(run_id, []) if e.seq > after_seq]

    def next_seq(self, run_id: str) -> int:
        with self.lock:
            events = self._events.get(run_id, [])
            return events[-1].seq + 1 if events else 0

    def restore(self, run: Run, events: list[WorkflowEvent]) -> None:
        """Load a run and its event log that already exist in the repository (startup only): memory only,
        nothing is written back. next_seq continues from the last restored event."""
        with self.lock:
            self._runs[run.run_id] = run
            self._events[run.run_id] = sorted(events, key=lambda e: e.seq)
