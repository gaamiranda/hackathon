# ProcureAI backend

Python 3.12 · FastAPI · pydantic v2 · managed with [uv](https://docs.astral.sh/uv/).

Start with the [root README](../README.md), then [PLAN.md](../PLAN.md) — the plan of record — and
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the lifecycle, the event contract and the LLM
call path. The contracts in `procureai/domain/models.py` are frozen after T1.

## Setup

```sh
just setup              # from the repo root: uv sync + npm install
# or, backend only:
cd backend && uv sync   # creates .venv with runtime + dev deps
cp .env.example .env    # optional; defaults run in MODE=mock, no credentials, no network
```

## Everyday commands (from the repo root)

| Command | What it does |
|---|---|
| `just run` | backend on :8000 + War Room on :5173 |
| `just backend` | backend only, mock mode |
| `just test` | `uv run pytest` (271 tests, ~2 s) + the frontend build |
| `just regen` | re-export JSON schemas and regenerate the synthetic quotes |
| `just demo` / `demo-week2` / `demo-week3` | scripted runs over HTTP against a running backend |
| `just demo-negotiation` | full negotiated run in-process, prints the event log |
| `just demo-manual` | fail-safe rehearsal: :8001 with every LLM route pointed at a closed port |
| `just seed STAGE` | seed a fresh run straight to `created \| extracted \| mismatch \| recommended \| negotiated \| replanned \| po` |
| `just preflight` | the 15-check demo checklist against the box ([docs/PREFLIGHT.md](../docs/PREFLIGHT.md)) |
| `just extract-live` / `judge-live` / `probe-llm` | live-path tools; the first two replay the committed caches unless given `--record` |

Direct equivalents if you prefer: `uv run pytest`, `uv run uvicorn procureai.api.app:app --reload`,
`uv run python scripts/<name>.py`.

```sh
curl http://127.0.0.1:8000/health
# {"mode":"mock","llm_gateway":"unknown","openclaw":"unknown", ...}
curl -N localhost:8000/runs/<run_id>/events/stream      # live SSE event log (?follow=false just replays)
```

## Routes

See `procureai/api/routes_runs.py`:

- **Run**: `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/summary`,
  `GET /runs/{id}/comparison` (engine-only matrix, no AI text), `GET /runs/{id}/events[/stream]`.
- **Documents**: `POST /runs/{id}/documents` (multipart or `{email_text, filename}`),
  `POST /runs/{id}/documents/{doc_id}/replace`. Adding documents never auto-evaluates.
- **Human gates**: `POST /runs/{id}/quotes/{qid}/correct` (patch fields, or a whole quote from the
  manual form), `POST /runs/{id}/quotes/{qid}/confirm-math` (`use_computed=false` withdraws the
  quote). Both auto-resume the evaluation.
- **Evaluate**: `POST /runs/{id}/evaluate` → `RECOMMENDED` or `CALC_MISMATCH`.
- **Negotiation**: `POST /runs/{id}/negotiate`, `POST /runs/{id}/negotiation/{sid}/approve`
  (`{message}`, edits are re-filtered; a violation answers 422 with `violations`), `GET /runs/{id}/negotiations`.
- **Interrupt**: `POST /runs/{id}/interrupt` `{quantity?, budget?, required_by?, reason?}` → replan on
  the existing quotes, no re-extraction.
- **Purchase order (G5)**: `POST /runs/{id}/request-po` → `AWAITING_PO_APPROVAL` with `run.po_preview`,
  `POST /runs/{id}/approve-po` `{approved_by}` → `PO_GENERATED` (**the only path that constructs a
  `PurchaseOrder`**), `POST /runs/{id}/reject-po` `{reason}`, `GET /runs/{id}/po`,
  `GET /runs/{id}/po.pdf` (one-page PDF via `procureai/po/render.py`, written to `backend/out/`).
- **Health**: `GET /health` — mode, LLM route and `llm: ok | degraded | down`, OpenClaw probe, judge
  mode, run count, whether runs are persisted.

Errors: `WorkflowError` → 409 `{code, message}`; not found → 404; unsupported extension → 415;
unreadable document → 422; text over 6,000 chars → 413.

## Regenerate JSON schemas

After any change to `procureai/domain/models.py`:

```sh
uv run python scripts/export_schemas.py     # or: just regen
```

Writes one file per contract to `procureai/domain/schema/<ModelName>.json` (consumed by the frontend
and by OpenClaw tool definitions). A test fails if the committed schemas drift from the models.

## Layout

```
procureai/
  api/          FastAPI app: routes_runs.py (REST + SSE), extract_text.py (pdf/xlsx/txt → text), deps.py
  domain/       pydantic contracts + exported JSON schemas
  engine/       DETERMINISTIC costing (the §4 formula chain), scoring, negotiation policy, diff
  workflow/     orchestrator (state machine), event bus, run store, file persistence
  agents/       Document · Supplier Intel · Decision · Negotiation, live + mock, prompts/,
                number_guard.py (G1: no number the model was not given)
  guardrails/   GuardrailJudge: Jev or mock, questions, replay cache
  llm/          LLMClient, gateway + OpenClaw clients, fallback pair, replay cache, route status
  po/ sim/      purchase-order PDF renderer; scripted supplier personas
  config/       pydantic-settings (MODE, LLM gateway, OpenClaw, judge, RUN_STORE_DIR)
scripts/        demo_week1-3, demo_negotiation, seed_demo, export_schemas, generate_synthetic_quotes,
                probe_gateway, extract_live, record_extractions, judge_live, print_event_log
tests/          271 tests; conftest.py forces replay_only, so the suite never reaches the network
```
