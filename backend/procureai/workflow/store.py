"""In-memory run + event store (PLAN.md D5: Week 1 storage). Repository shape hides the backend."""

import threading

from procureai.domain.models import Run, WorkflowEvent
from procureai.workflow.errors import WorkflowError


class RunStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._runs: dict[str, Run] = {}
        self._events: dict[str, list[WorkflowEvent]] = {}

    def put(self, run: Run) -> Run:
        with self.lock:
            self._runs[run.run_id] = run
            self._events.setdefault(run.run_id, [])
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

    def events(self, run_id: str, after_seq: int = -1) -> list[WorkflowEvent]:
        with self.lock:
            return [e for e in self._events.get(run_id, []) if e.seq > after_seq]

    def next_seq(self, run_id: str) -> int:
        with self.lock:
            events = self._events.get(run_id, [])
            return events[-1].seq + 1 if events else 0
