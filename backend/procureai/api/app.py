"""FastAPI application: REST + SSE over the workflow (PLAN.md §6)."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from procureai.api.deps import get_orchestrator, lifespan
from procureai.api.routes_runs import router as runs_router
from procureai.config.settings import Settings, get_settings
from procureai.llm.gateway import probe_gateway
from procureai.llm.openclaw import probe_openclaw
from procureai.llm.status import TRACKER, LlmStatus, ProbeResult, RouteTracker, llm_status
from procureai.workflow import WorkflowError

NOT_FOUND_CODES = {"run_not_found", "quote_not_found", "document_not_found", "po_not_found"}

app = FastAPI(title="ProcureAI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(runs_router)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(_: Request, exc: WorkflowError) -> JSONResponse:
    code = 404 if exc.code in NOT_FOUND_CODES else 409
    return JSONResponse(status_code=code, content={"code": exc.code, "message": exc.message})


def openclaw_status(s: Settings) -> str:
    """unconfigured (no token) | configured (token, but calls go to the gateway) | reachable | unreachable.
    The probe only runs when OpenClaw is the active route, so /health never stalls 2 s for an unused tunnel."""
    if not (s.OPENCLAW_URL and s.OPENCLAW_TOKEN):
        return "unconfigured"
    if s.LLM_BACKEND != "openclaw":
        return "configured"
    return probe_openclaw(s)


def gateway_status(s: Settings) -> str:
    """unconfigured | reachable | unreachable: the free GET /api/tags probe, live mode only (T17)."""
    if s.MODE != "live" or not (s.LLM_GATEWAY_URL and s.LLM_GATEWAY_API_KEY):
        return "unconfigured"
    return probe_gateway(s)


@dataclass
class HealthProbes:
    """The two route probes, run concurrently (each ≤ 2 s, so /health stays ≤ 2 s) and cached for TTL seconds
    so a page polling /health does not hammer the gateway. Fresh per process (app.state) — tests get a new one."""

    ttl_s: float = 10.0
    tracker: RouteTracker = TRACKER
    _cached: dict[str, object] | None = field(default=None, init=False)
    _at: float = field(default=0.0, init=False)

    def snapshot(self, s: Settings) -> dict[str, object]:
        now = time.monotonic()
        if self._cached is None or now - self._at > self.ttl_s:
            with ThreadPoolExecutor(max_workers=2) as pool:
                openclaw, gateway = pool.submit(openclaw_status, s), pool.submit(gateway_status, s)
                openclaw_state, gateway_state = openclaw.result(), gateway.result()
            self._cached = {"openclaw": openclaw_state, "gateway": gateway_state}
            self._at = now
        probes = self._cached
        status: LlmStatus = llm_status(s, self.tracker, openclaw=_probed(probes["openclaw"]), gateway=_probed(probes["gateway"]))
        return {**probes, "llm": status, "llm_routes": self.tracker.as_json()}

    def invalidate(self) -> None:
        self._cached = None


def _probed(state: object) -> ProbeResult:
    """unconfigured / configured mean the route was not probed this call."""
    return state if state in ("reachable", "unreachable") else None  # type: ignore[return-value]


def get_health_probes(request: Request) -> HealthProbes:
    probes = getattr(request.app.state, "health_probes", None)
    if probes is None:
        probes = request.app.state.health_probes = HealthProbes()
    return probes


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    """`llm` (T17, G6): ok | degraded (OpenClaw failed, direct gateway answers) | down (no route answers) — from the
    clients' last real attempts (`llm_routes`) plus the cached ≤ 2 s probes. Mock mode is always ok."""
    s = get_settings()
    probes = get_health_probes(request).snapshot(s)
    return {
        "mode": s.MODE,
        "llm_gateway": "configured" if s.LLM_GATEWAY_URL and s.LLM_GATEWAY_API_KEY else "unconfigured",
        "llm_backend": s.LLM_BACKEND,
        "openclaw": probes["openclaw"],
        "gateway": probes["gateway"],
        "llm": probes["llm"],
        "llm_routes": probes["llm_routes"],
        "openclaw_tasks": sorted(s.openclaw_tasks) if s.LLM_BACKEND == "openclaw" else [],
        "guardrail_judge": s.GUARDRAIL_JUDGE,
        "judge_model": s.TYPESAFE_MODEL if s.GUARDRAIL_JUDGE == "jev" else None,
        "runs": len(get_orchestrator(request).store.list_runs()),
        "runs_persisted": get_orchestrator(request).store.persisted,
    }
