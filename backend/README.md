# ProcureAI backend

Python 3.12 · FastAPI · pydantic v2 · managed with [uv](https://docs.astral.sh/uv/).
Read `../PLAN.md` first; the contracts in `procureai/domain/models.py` are frozen after T1.

## Setup

```sh
cd backend
uv sync                 # creates .venv with runtime + dev deps
cp .env.example .env    # optional; defaults run in MODE=mock
```

## Run tests

```sh
uv run pytest
```

## Run the API

```sh
uv run uvicorn procureai.api.app:app --reload
curl http://127.0.0.1:8000/health
# {"mode":"mock","llm_gateway":"unknown","openclaw":"unknown"}
```

## Regenerate JSON schemas

After any change to `procureai/domain/models.py`:

```sh
uv run python scripts/export_schemas.py
```

Writes one file per contract to `procureai/domain/schema/<ModelName>.json`
(consumed by the frontend and OpenClaw tool definitions). A test fails if the
committed schemas drift from the models.

## Layout

```
procureai/
  api/        FastAPI app (only /health for now)
  config/     pydantic-settings (MODE, LLM gateway, OpenClaw)
  domain/     contracts + exported JSON schemas
scripts/      export_schemas.py
tests/        contract tests against ../data/fixtures/
```
