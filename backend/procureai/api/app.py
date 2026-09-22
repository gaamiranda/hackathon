"""FastAPI application: REST + SSE over the workflow (PLAN.md §6)."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from procureai.api.deps import get_orchestrator, lifespan
from procureai.api.routes_runs import router as runs_router
from procureai.config.settings import Settings, get_settings
from procureai.llm.openclaw import probe_openclaw
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


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    s = get_settings()
    return {
        "mode": s.MODE,
        "llm_gateway": "configured" if s.LLM_GATEWAY_URL and s.LLM_GATEWAY_API_KEY else "unconfigured",
        "llm_backend": s.LLM_BACKEND,
        "openclaw": openclaw_status(s),
        "openclaw_tasks": sorted(s.openclaw_tasks) if s.LLM_BACKEND == "openclaw" else [],
        "guardrail_judge": s.GUARDRAIL_JUDGE,
        "judge_model": s.TYPESAFE_MODEL if s.GUARDRAIL_JUDGE == "jev" else None,
        "runs": len(get_orchestrator(request).store.list_runs()),
    }
