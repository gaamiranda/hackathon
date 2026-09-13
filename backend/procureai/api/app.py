"""FastAPI application. Only /health for now; run routes arrive with later tasks."""

from fastapi import FastAPI

from procureai.config.settings import get_settings

app = FastAPI(title="ProcureAI", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"mode": settings.MODE, "llm_gateway": "unknown", "openclaw": "unknown"}
