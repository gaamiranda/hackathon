"""Process-wide orchestrator (one uvicorn worker; in-memory store, mirrored to RUN_STORE_DIR when set; PLAN.md D5)."""

import asyncio
import logging
import signal
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from procureai.agents.factory import build_agents
from procureai.config.settings import get_settings
from procureai.guardrails import build_judge
from procureai.workflow import EventBus, FileRunRepository, Orchestrator, RunStore, restore_runs

EXIT_SIGNALS = (signal.SIGINT, signal.SIGTERM)
log = logging.getLogger(__name__)


def build_orchestrator() -> Orchestrator:
    """With RUN_STORE_DIR set, the directory is created, every persisted run is loaded (runs caught in a
    transient state get a run.recovered event, see workflow/persistence.py) and new writes go there (T19)."""
    settings = get_settings()
    repository = FileRunRepository(settings.RUN_STORE_DIR) if settings.RUN_STORE_DIR else None
    store = RunStore(repository)
    events = EventBus(store)
    if repository is not None:
        count = restore_runs(store, events, repository)
        log.info("loaded %d runs from %s", count, repository.root)
    return Orchestrator(build_agents(settings), store, events=events, judge=build_judge(settings))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn configures only its own loggers; without this the app's INFO lines ("loaded N runs", LLM
    # fallbacks) never reach the journal. No-op when the root logger already has handlers.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app.state.orchestrator = build_orchestrator()
    # Open SSE streams block uvicorn's graceful shutdown: it waits for every connection to close
    # before sending the lifespan shutdown event. So set the event from the exit signal itself
    # (chaining uvicorn's handler) and let the stream generators return on their own.
    app.state.shutdown = shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}
    if threading.current_thread() is threading.main_thread():  # signals are main-thread only (TestClient runs elsewhere)
        for sig in EXIT_SIGNALS:
            previous[sig] = signal.getsignal(sig)

            def handler(signum, frame, _prev=previous[sig]):
                loop.call_soon_threadsafe(shutdown.set)
                if callable(_prev):
                    _prev(signum, frame)

            signal.signal(sig, handler)
    try:
        yield
    finally:
        shutdown.set()
        for sig, prev in previous.items():
            signal.signal(sig, prev)


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_shutdown_event(request: Request) -> asyncio.Event:
    return request.app.state.shutdown
