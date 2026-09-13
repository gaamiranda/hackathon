"""Process-wide orchestrator (one uvicorn worker, in-memory store; PLAN.md D5)."""

import asyncio
import signal
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from procureai.agents.factory import build_agents
from procureai.config.settings import get_settings
from procureai.workflow import EventBus, Orchestrator, RunStore

EXIT_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def build_orchestrator() -> Orchestrator:
    store = RunStore()
    return Orchestrator(build_agents(get_settings()), store, events=EventBus(store))


@asynccontextmanager
async def lifespan(app: FastAPI):
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
