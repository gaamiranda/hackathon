# ProcureAI frontend — War Room (Week 1)

Vite + React 18 + TypeScript + Tailwind v4. Hooks only, no state library. Two routes:
`/` (run list + new run) and `/runs/:id` (request & documents · live agent timeline · decision).

## Install & run

```sh
cd frontend
npm install
cp .env.example .env        # optional; defaults to http://localhost:8000
npm run dev                 # http://localhost:5173 (Vite picks the next free port if 5173 is busy —
                            #   the backend's CORS_ORIGINS must then include that port)
npm run build               # tsc + vite build → dist/
```

Env: `VITE_API_URL` — backend base URL (default `http://localhost:8000`).
Backend: `cd backend && uv run uvicorn procureai.api.app:app --reload` (mock mode needs no credentials).

## 60-second demo click path

1. Open `/`. The form is prefilled: **Product X · 2000 units · required in 14 days · budget 30000 USD**. Click **Create run**.
2. On the run page, drop (or click and pick) the three files from `data/synthetic/`:
   `supplier_a_apex.pdf`, `supplier_b_borealis.xlsx`, `supplier_c_cobalt.eml.txt`.
   The timeline shows the Document Agent extracting each one; state becomes **EXTRACTED**.
3. Click **Evaluate**. The engine validates, finds Apex's printed total (22,040.00) ≠ computed (22,800.00) and
   stops in **CALC_MISMATCH**; the human-review dialog appears.
4. Click **Use computed total**. Enrichment, scoring and the Decision Agent run; the Decision panel shows
   **Borealis Manufacturing AS 72.3**, Cobalt Industrial 62.9, Apex Components Ltd 58.6, with the rationale.

Kill the backend while the page is open: the stream indicator turns red ("disconnected – retrying") and the page
keeps working; the client reconnects with `since=<last seq>` every 2 s. (The Week 1 store is in-memory, so after
a restart the run is gone and the indicator says so.)

## Source of truth for types

`src/api/types.ts` is hand-written from `backend/procureai/domain/schema/*.json`. When a schema changes,
update the TS type by hand (no codegen step yet). Money fields are strings ("12.80") and are formatted, never parsed.

## Layout

```
src/
  api/client.ts         typed fetch wrappers for every backend route + SSE URL
  api/types.ts          Run, WorkflowEvent, Scorecard, Recommendation, PendingHuman, NormalizedQuote, …
  hooks/useEventStream  EventSource with replay-on-connect and reconnect(since=last seq)
  pages/RunListPage     GET /runs + POST /runs form
  pages/RunPage         3-column layout, run state, refetch rules, human gates
  components/           RequestPanel (upload, quotes, Evaluate), TimelinePanel, DecisionPanel, HumanGate, StateBadge, Panel
  format.ts             money(string) formatting without float parsing
```
