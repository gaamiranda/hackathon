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

# Regenerate JSON schemas and synthetic quotes
regen:
    cd backend && uv run python scripts/export_schemas.py
    cd backend && uv run python scripts/generate_synthetic_quotes.py

# Week 1 demo over HTTP against a running backend
demo:
    cd backend && uv run python scripts/demo_week1.py
