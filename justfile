# ProcureAI dev tasks. `just` lists recipes; `just run` starts backend + frontend.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install backend (uv) and frontend (npm) dependencies
setup:
    cd backend && uv sync
    cd frontend && npm install

# Start backend (:8000) and frontend (:5173) together; Ctrl-C stops both
run:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    (cd backend && exec uv run uvicorn procureai.api.app:app --reload) & BE=$!
    # --strictPort: fail if 5173 is taken instead of drifting to 5174 (which the backend's CORS_ORIGINS rejects)
    (cd frontend && exec npm run dev -- --strictPort) & FE=$!
    trap 'kill $BE $FE 2>/dev/null; wait $BE $FE 2>/dev/null' INT TERM EXIT
    wait $BE $FE

# Backend only (mock mode, no credentials needed)
backend:
    cd backend && uv run uvicorn procureai.api.app:app --reload

# Frontend only
frontend:
    cd frontend && npm run dev -- --strictPort

# Backend tests + frontend build
test:
    cd backend && uv run pytest
    cd frontend && npm run build

# Pair it with a second frontend: cd frontend && VITE_API_URL=http://localhost:8001 npm run dev -- --port 5174 --strictPort
# (frontend/README.md "Manual mode"). Every LLM route points at a closed port, so each document goes to the manual form.
# Fail-safe rehearsal (T17, G6): backend on :8001 in MODE=live with the AI unreachable, next to the normal `just run`
demo-manual:
    cd backend && MODE=live LLM_BACKEND=openclaw OPENCLAW_URL=http://127.0.0.1:9 OPENCLAW_TOKEN=closed-port \
      LLM_GATEWAY_URL=http://127.0.0.1:9 LLM_GATEWAY_API_KEY=closed-port LLM_CACHE_MODE=record \
      GUARDRAIL_JUDGE=mock RUN_STORE_DIR="" CORS_ORIGINS='["http://localhost:5173","http://localhost:5174"]' \
      uv run uvicorn procureai.api.app:app --port 8001

# Regenerate JSON schemas and synthetic quotes
regen:
    cd backend && uv run python scripts/export_schemas.py
    cd backend && uv run python scripts/generate_synthetic_quotes.py

# Probe the organiser LLM gateway (LIVE: ~2 calls on the shared credit). Findings: docs/INFRA.md
probe-llm:
    cd backend && uv run python scripts/probe_gateway.py

# Replay the three recorded quote extractions from data/llm_cache/ (free); --record re-runs them live
extractions *ARGS:
    cd backend && uv run python scripts/record_extractions.py {{ ARGS }}

# Extract synthetic documents with the live Document Agent (replays data/llm_cache/; --record re-runs live)
extract-live *ARGS:
    cd backend && uv run python scripts/extract_live.py {{ ARGS }}

# Guardrail judge (TypeSafe Jev) over the demo fixtures; replays data/judge_cache/ (free), --record re-runs live (≤ 40 calls)
judge-live *ARGS:
    cd backend && uv run python scripts/judge_live.py {{ ARGS }}

# SSH tunnel to the OpenClaw gateway on the Lightsail box: local :18789 → box 127.0.0.1:18789 (keep it running; Ctrl-C stops it)
tunnel:
    ssh -N -L 18789:127.0.0.1:18789 -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76

# Reverse tunnel for the OpenClaw skills: box 127.0.0.1:8000 → your local backend on :8000 (stop the deployed backend on the box first: systemctl --user stop procureai-backend)
tunnel-reverse:
    ssh -N -R 8000:127.0.0.1:8000 -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76

# Deploy to the Lightsail box (frontend build + rsync + uv sync + units + restart); docs/DEPLOY.md
deploy *ARGS:
    scripts/deploy.sh {{ ARGS }}

# Delete every persisted run on the box (RUN_STORE_DIR) and restart the backend, e.g. right before the demo; asks first
clear-runs:
    #!/usr/bin/env bash
    set -euo pipefail
    host=ubuntu@47.129.120.76; key=~/.ssh/LightsailDefaultKey-ap-southeast-1.pem; dir=/home/ubuntu/procureai/data/runs
    n=$(ssh -i "$key" "$host" "ls -1 $dir 2>/dev/null | wc -l" | tr -d ' ')
    echo "$n run(s) in $host:$dir"
    [[ "$n" == "0" ]] && exit 0
    read -r -p "Delete them all and restart procureai-backend? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }
    ssh -i "$key" "$host" "rm -rf $dir/* && systemctl --user restart procureai-backend && sleep 2 && curl -s http://127.0.0.1:8000/health"
    echo

# Week 1 demo over HTTP against a running backend
demo:
    cd backend && uv run python scripts/demo_week1.py

# Week 2 demo over HTTP against a running backend: Week 1 path, then negotiate and auto-approve every draft
demo-week2:
    cd backend && uv run python scripts/demo_week2.py

# Week 3 demo over HTTP against a running backend: Week 2 path, the interrupt 2,000 → 5,000 / budget 75,000, then request + approve the PO (PDF in backend/out/)
demo-week3:
    cd backend && uv run python scripts/demo_week3.py

# Full negotiated run in-process (mock mode): mismatch → negotiation with top-2 → re-score; prints the event log
demo-negotiation:
    cd backend && uv run python scripts/demo_negotiation.py
