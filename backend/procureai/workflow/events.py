"""Append-only event log + fan-out (PLAN.md §4 WorkflowEvent; feeds the War Room)."""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from procureai.domain.models import EventActor, Run, WorkflowEvent, WorkflowState
from procureai.workflow.store import RunStore

Subscriber = Callable[[WorkflowEvent], None]


class EventBus:
    def __init__(self, store: RunStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._subs: dict[str, list[Subscriber]] = defaultdict(list)
        self._global: list[Subscriber] = []

    def emit(
        self,
        run: Run,
        actor: EventActor,
        type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        state_before: WorkflowState | None = None,
        state_after: WorkflowState | None = None,
    ) -> WorkflowEvent:
        """Append one event with the next seq for the run and notify subscribers (sync, in order).

        The run is re-put after the event: the orchestrator mutates it in place before emitting, so this
        is what keeps run.json in step with events.jsonl when the store is persisted (T19)."""
        with self.store.lock:
            event = WorkflowEvent(
                run_id=run.run_id,
                seq=self.store.next_seq(run.run_id),
                ts=self.now(),
                actor=actor,
                type=type,
                state_before=state_before,
                state_after=state_after,
                payload=payload or {},
                summary=summary,
            )
            self.store.append_event(event)
            self.store.put(run)
            listeners = list(self._subs.get(run.run_id, [])) + list(self._global)
        for cb in listeners:
            cb(event)
        return event

    def subscribe(self, callback: Subscriber, run_id: str | None = None) -> Callable[[], None]:
        """Register a callback for one run (or all runs); returns an unsubscribe function."""
        target = self._global if run_id is None else self._subs[run_id]
        target.append(callback)

        def unsubscribe() -> None:
            if callback in target:
                target.remove(callback)

        return unsubscribe

    def queue(self, run_id: str, loop: asyncio.AbstractEventLoop | None = None) -> tuple["asyncio.Queue[WorkflowEvent]", Callable[[], None]]:
        """asyncio.Queue fed with this run's events (for an SSE endpoint). Thread-safe via the loop."""
        loop = loop or asyncio.get_event_loop()
        q: asyncio.Queue[WorkflowEvent] = asyncio.Queue()
        unsubscribe = self.subscribe(lambda e: loop.call_soon_threadsafe(q.put_nowait, e), run_id)
        return q, unsubscribe
