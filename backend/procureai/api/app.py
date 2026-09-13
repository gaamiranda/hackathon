"""FastAPI application: REST + SSE over the workflow (PLAN.md §6)."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from procureai.api.deps import get_orchestrator, lifespan
from procureai.api.routes_runs import router as runs_router
from procureai.config.settings import get_settings
from procureai.workflow import WorkflowError

NOT_FOUND_CODES = {"run_not_found", "quote_not_found", "document_not_found"}

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


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    s = get_settings()
    return {
        "mode": s.MODE,
        "llm_gateway": "configured" if s.LLM_GATEWAY_URL and s.LLM_GATEWAY_API_KEY else "unconfigured",
        "openclaw": "configured" if s.OPENCLAW_GATEWAY_URL and s.OPENCLAW_TOKEN else "unconfigured",
        "runs": len(get_orchestrator(request).store.list_runs()),
    }
