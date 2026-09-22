# DEPLOY — the War Room on Lightsail (T10c, 2026-09-22)

Public URL: **http://47.129.120.76/** (plain HTTP on the IP; no domain, so no TLS — see "TLS" below).
One box runs everything: nginx (:80) → the Vite build + `/api/` → the FastAPI backend (127.0.0.1:8000)
→ OpenClaw (127.0.0.1:18789) → the organiser LLM gateway. Only 22/80/443 are open in the Lightsail
firewall; 8000 and 18789 are loopback-only and are never proxied.

## Box facts

| Item | Value |
|---|---|
| Instance | `procureai-warroom`, ap-southeast-1a, Ubuntu 24.04, 2 vCPU / 3.8 GB / 80 GB, no swap |
| SSH | `ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76` |
| Repo on the box | `/home/ubuntu/procureai` (rsync of the local checkout; not a git clone) |
| Backend venv | `/home/ubuntu/procureai/backend/.venv` (`uv sync --frozen --no-dev`, uv in `~/.local/bin`) |
| Secrets | `/home/ubuntu/procureai/backend/.env` only (mode 600). Never in the repo, never in this file. |
| Frontend | `/var/www/procureai` (copy of `frontend/dist`, built with `VITE_API_URL=/api`) |
| nginx site | `/etc/nginx/sites-available/procureai` ← `deploy/nginx-procureai.conf` (the only enabled site) |
| Backend unit | `procureai-backend.service` — systemd **user** unit, `~/.config/systemd/user/` ← `deploy/procureai-backend.service` |
| OpenClaw unit | `openclaw-gateway.service` — systemd **user** unit (docs/OPENCLAW.md) |
| Skills | `~/.openclaw/workspace/skills/` ← `openclaw/skills/` (synced on every deploy, hot-reloaded) |
| LLM cache on the box | `/home/ubuntu/procureai/data/llm_cache/`, `data/judge_cache/` (rsynced from the repo; new entries are written there) |
| Persisted runs | `/home/ubuntu/procureai/data/runs/<run_id>/{run.json,events.jsonl}` (`RUN_STORE_DIR`; box-only, never rsynced or deleted by a deploy) |

Both units are user units because OpenClaw's installer made its own one that way; `loginctl enable-linger ubuntu`
is on, so they survive logout and reboot. `sudo systemctl … procureai-backend` does **not** work — always `--user`.

## Day-to-day

```bash
# logs
ssh ubuntu@47.129.120.76 'journalctl --user -u procureai-backend -n 100 -f'
ssh ubuntu@47.129.120.76 'journalctl --user -u openclaw-gateway -n 100'
ssh ubuntu@47.129.120.76 'sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log'

# status / restart
systemctl --user status procureai-backend       # on the box
systemctl --user restart procureai-backend      # ~2 s; runs are reloaded from data/runs (see below)
sudo systemctl reload nginx

# health (what the header of the War Room shows)
curl -s http://127.0.0.1:8000/health            # on the box
curl -s http://47.129.120.76/api/health         # from anywhere
# → {"mode":"live","llm_gateway":"configured","llm_backend":"openclaw","openclaw":"reachable",
#    "openclaw_tasks":["draft","explain","explain_diff","extract","negotiation_verdict"],
#    "guardrail_judge":"jev","judge_model":"jev-1.13.0","runs":N,"runs_persisted":true}
```

## Redeploy: `just deploy`

`scripts/deploy.sh` (called by `just deploy`) is the whole procedure and is idempotent:

1. `frontend`: `npm ci` if `node_modules` is missing, then `VITE_API_URL=/api npm run build`
2. `rsync` the checkout to `/home/ubuntu/procureai` — excluding `.git`, `node_modules`, `.venv`, `backend/out`,
   `.env` and `data/runs` (both protected: the box's copies are never overwritten or deleted), caches and the planning PDFs
3. on the box: install `uv`/`nginx` if missing → `uv sync --frozen --no-dev` → install the unit and the nginx site
   from `deploy/` → copy `frontend/dist` to `/var/www/procureai` → sync the OpenClaw skills → restart the backend →
   reload nginx → print `/health`

Flags: `SKIP_FRONTEND=1 just deploy` (reuse `frontend/dist`), `SKIP_SYNC=1 just deploy` (no `uv sync`; backend code
changes only). Overrides: `PROCUREAI_HOST`, `PROCUREAI_SSH_KEY`. First-time only: create `backend/.env` on the box
(`.env.example` + the values from your local `.env`, plus the deploy-specific ones below); the script refuses to run
without it. A deploy from a clean checkout takes ~1 min (uv sync dominates the first time, ~5 s afterwards).

The box's `.env` differs from a laptop's in: `MODE=live`, `LLM_BACKEND=openclaw`, `OPENCLAW_URL=http://127.0.0.1:18789`
(loopback, no tunnel), `GUARDRAIL_JUDGE=jev`, `LLM_CACHE_MODE=replay_or_record`, `JUDGE_CACHE_MODE=replay_or_record`,
`CORS_ORIGINS=["http://47.129.120.76"]` (same-origin through nginx, so CORS is belt and braces),
`RUN_STORE_DIR=/home/ubuntu/procureai/data/runs` (see next section).

## Runs survive a restart: `RUN_STORE_DIR`

`RunStore` is in memory and per process (PLAN.md D5), but with `RUN_STORE_DIR` set every write is mirrored to disk
(T19, `backend/procureai/workflow/persistence.py`) and loaded back on startup, so a restart, a redeploy, a crash or a
reboot keeps the in-progress runs and an open War Room page simply reconnects its stream. Unset (the default, tests and
local dev) nothing is written and a restart clears the runs as before. `/health` reports `runs_persisted`.

- **Files:** `<RUN_STORE_DIR>/<run_id>/run.json` (the full `Run`, rewritten atomically — temp file + rename — after
  every event) and `events.jsonl` (one `WorkflowEvent` per line, append-only, fsync'd per event). Both are written
  inside the store lock, so they cannot diverge. Measured on the box (ext4): ~7 ms per event (two fsyncs), ~0.6 s over a
  full 79-event run, i.e. invisible next to one ~3 s LLM call. Only `RunStore` writes them.
- **Startup:** the directory is scanned, every `run.json` + `events.jsonl` is validated through the pydantic models and
  the log says `loaded N runs from …` (`journalctl --user -u procureai-backend`). A directory that does not parse is
  logged as `skipping run directory …` and ignored — it never stops the backend; a partial last event line (a crash
  mid-append) is dropped, the rest of the log is kept. Seq numbers continue from the last persisted event.
- **Runs caught mid-transition:** a run whose `run.json` shows a transient state (EXTRACTING, VALIDATING, ENRICHING,
  SCORING, NEGOTIATION_DRAFTED, NEGOTIATING, COUNTER_RECEIVED, RE_SCORING, REPLANNING — the process died inside one
  orchestrator call) gets an amber `run.recovered` event and is moved to the nearest resting state: **RECOMMENDED** if
  it has scorecards, else **EXTRACTED** if it has quotes, else **CREATED**; the human then re-triggers (evaluate /
  negotiate / interrupt). Waiting states (NEEDS_HUMAN_EXTRACTION, CALC_MISMATCH, AWAITING_*) and resting states are
  loaded as they were, pending gate included, and are immediately usable.
- **Clear old runs before the demo:** `just clear-runs` — counts the runs on the box, asks for confirmation, then
  deletes the directory contents over ssh and restarts the backend so the run list starts empty. By hand:
  `ssh ubuntu@47.129.120.76 'rm -rf ~/procureai/data/runs/* && systemctl --user restart procureai-backend'`.
- **Copy a run home** (for a bug report or to replay its timeline): `rsync -az ubuntu@47.129.120.76:procureai/data/runs/<run_id>/ /tmp/<run_id>/`.

`--workers 1` stays: a second worker would have its own run list and write the same files. PO PDFs are still rendered
on request from the stored PO, so the run directories and the LLM/judge caches are the only things the backend writes.

## The LLM cache on the box

`LLM_CACHE_MODE=replay_or_record`: a prompt already in `data/llm_cache/` (the three demo documents, the three
rationales, the two change explanations — all recorded through OpenClaw in T10c) replays for free and shows
**"via replay"** in the timeline; anything new (an edited document, a different quantity) goes live through OpenClaw
(**"via OpenClaw"**, or **"via gateway"** if OpenClaw was down and the fallback answered) and is recorded on the box.

- **Rotate / force live calls for the demo (D30):** either move the cache aside on the box —
  `mv ~/procureai/data/llm_cache ~/llm_cache.$(date +%s) && mkdir ~/procureai/data/llm_cache` — or set
  `LLM_CACHE_MODE=record` in `.env` and restart. Every demo run then spends ~8 calls (~45 s total). Put the cache back
  (or `just deploy`, which rsyncs the repo's cache over) to return to replay.
- **Bring recordings home:** `rsync -az ubuntu@47.129.120.76:procureai/data/llm_cache/ data/llm_cache/` then review
  and commit. Note `just deploy` uses `--delete`, so entries that exist only on the box are removed by the next deploy
  unless copied back first.
- The judge cache (`data/judge_cache/`) behaves the same way under `JUDGE_CACHE_MODE`.

## Emergency switches (edit `backend/.env` on the box, then `systemctl --user restart procureai-backend`)

| Symptom | Switch |
|---|---|
| A persisted run keeps breaking startup or the UI | `just clear-runs`, or delete just that run's directory under `data/runs/` and restart. A run that fails validation is skipped automatically. |
| OpenClaw down / slow / restarting | nothing to do: the fallback answers from the direct gateway per call ("via gateway"). To stop trying OpenClaw at all: `LLM_BACKEND=gateway`. |
| One task's prose keeps tripping the number guard through OpenClaw (templated text in the timeline) | `OPENCLAW_TASKS=extract,explain_diff,draft` (drop the task; it goes to the direct gateway) |
| Gateway 429 / credit exhausted / no network | `MODE=mock` — every agent answers from fixtures; the whole demo still runs (PLAN.md D3). |
| Jev unavailable | `GUARDRAIL_JUDGE=mock` (or leave it: a judge failure yields "not evaluated" and the workflow continues) |
| Backend wedged | `systemctl --user restart procureai-backend` (runs are reloaded from `RUN_STORE_DIR`, see above) |
| nginx | `sudo nginx -t && sudo systemctl reload nginx` |

`/health` (and the War Room header) confirm the active mode, route and judge after any change.

## nginx / SSE notes

The timeline is one long-lived `GET /api/runs/{id}/events/stream` (SSE). Through nginx that needs, in the `/api/`
location: `proxy_buffering off`, `proxy_cache off`, `gzip off`, `proxy_http_version 1.1` with `Connection ""`, and
`proxy_read_timeout 600s` (a run can sit at a human gate for minutes; the backend sends `: keepalive` every 15 s
anyway). The backend also sets `X-Accel-Buffering: no` on the stream. Verified from a laptop: one connection covered
a whole demo run (upload → PO, 74 KB of events, zero `events?since=` polling requests in the access log).
`client_max_body_size 20m` covers uploads. Vite's hashed `/assets/` are cached for a year, `index.html` is not.

## TLS

Not set up: there is no domain, and a certificate cannot be issued for a bare IP by Let's Encrypt. The site is plain
`http://47.129.120.76/`. If a domain is pointed at the box: `sudo apt install certbot python3-certbot-nginx`,
`sudo certbot --nginx -d <domain>`, then add `https://<domain>` to `CORS_ORIGINS` and restart the backend — the
nginx site file gets the 443 block added by certbot; re-copy it into `deploy/nginx-procureai.conf` afterwards or
the next deploy overwrites it.

## Resource check (after a full demo run, 2026-09-22)

```
$ free -m            total   used   free  buff/cache  available
Mem:                  3832   1144    774        2220       2688     Swap: 0
backend (uvicorn)  RSS  95 MB
openclaw gateway   RSS 568 MB   (idle; 650–740 MB seen in T10a)
nginx              a few MB
```

2.7 GB available with OpenClaw + backend + nginx running: comfortably above the 500 MB line. No swap; if that
ever changes (e.g. a second OpenClaw agent), `sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile &&
sudo mkswap /swapfile && sudo swapon /swapfile`.

## OpenClaw housekeeping

- The skills call `http://127.0.0.1:8000` directly now (the T10b reverse tunnel is no longer needed;
  `just tunnel-reverse` remains for local testing). `procureai-run-status` re-tested on the box against the deployed
  backend after the demo run: correct state, ranking, rationale and "nothing is waiting" (1 tool turn, ~25 s,
  ~77 k prompt tokens because it runs on the full `main` agent).
- **Disable the auto-created crons** — they fire agent turns on the shared credit: `openclaw cron list`, then
  `openclaw cron disable f616455f-3b5e-4f17-9c72-53531ed0d18f` (skill review, `procureai` agent) and
  `openclaw cron disable ced7cad2-2f02-4991-824c-97f7b8a7534d` (skill review, `main`). Left enabled on purpose so
  the developer decides (`openclaw` is on PATH only via nvm: `export PATH=$HOME/.nvm/versions/node/v24.21.0/bin:$PATH`).
- Each stateless `/v1/chat/completions` call leaves a session row; `openclaw sessions list` grows with every live
  demo call. Harmless; prune occasionally.
