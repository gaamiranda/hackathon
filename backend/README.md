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

## Week 1 demo over HTTP

With the API running (mock mode, no network):

```sh
uv run python scripts/demo_week1.py          # create run → upload 3 docs → evaluate → confirm mismatch → recommendation
curl -N localhost:8000/runs/<run_id>/events/stream            # live SSE event log (add ?follow=false to just replay)
```

Routes: see `procureai/api/routes_runs.py` (`POST /runs`, `POST /runs/{id}/documents`, `POST /runs/{id}/evaluate`,
`POST /runs/{id}/quotes/{qid}/correct|confirm-math`, `POST /runs/{id}/negotiate`, `POST /runs/{id}/negotiation/{sid}/approve`,
`POST /runs/{id}/interrupt` (Week 3: {quantity?, budget?, required_by?, reason?} → replan on the existing quotes),
`GET /runs/{id}`, `GET /runs/{id}/events[/stream]`).

Later weeks over HTTP: `uv run python scripts/demo_week2.py` (negotiation loop) and `scripts/demo_week3.py` (then the
interrupt 2,000 → 5,000 / budget 75,000: prints the replan events, the impact table and the change explanation).

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
  api/        FastAPI app: routes_runs.py (REST + SSE), extract_text.py (pdf/xlsx/txt → text), deps.py
  agents/     agent protocols + mock implementations
  engine/     deterministic costing / scoring / policy / diff
  workflow/   orchestrator (state machine), run store, event bus
  config/     pydantic-settings (MODE, LLM gateway, OpenClaw)
  domain/     contracts + exported JSON schemas
scripts/      export_schemas.py
tests/        contract tests against ../data/fixtures/
```
