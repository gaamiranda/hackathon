"""Process-wide orchestrator (one uvicorn worker, in-memory store; PLAN.md D5)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from procureai.agents.factory import build_agents
from procureai.config.settings import get_settings
from procureai.workflow import EventBus, Orchestrator, RunStore


def build_orchestrator() -> Orchestrator:
    store = RunStore()
    return Orchestrator(build_agents(get_settings()), store, events=EventBus(store))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.orchestrator = build_orchestrator()
    yield


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator
