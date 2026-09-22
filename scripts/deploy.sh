#!/usr/bin/env bash
# Deploy ProcureAI to the Lightsail box (PLAN.md D12, T10c): `just deploy` from any checkout.
#
#   1. build the frontend locally with VITE_API_URL=/api (npm ci first if node_modules is missing)
#   2. rsync the repo (no .git / node_modules / .venv / backend/out / .env) to /home/ubuntu/procureai
#   3. on the box: uv + nginx if missing, `uv sync`, install the systemd USER unit and the nginx site
#      from deploy/, copy frontend/dist to /var/www/procureai, sync the OpenClaw skills, restart the
#      backend, reload nginx, curl /health
#
# Idempotent: every step re-applies the same files. The only manual step, once, is backend/.env on the
# box (from backend/.env.example) — this script never touches it, so secrets stay off the laptop's repo
# and out of git. Restarting the backend clears the in-memory runs (docs/DEPLOY.md).
#
# Env overrides: PROCUREAI_HOST (ubuntu@47.129.120.76), PROCUREAI_SSH_KEY (~/.ssh/LightsailDefaultKey-ap-southeast-1.pem),
#                SKIP_FRONTEND=1 (reuse frontend/dist), SKIP_SYNC=1 (no `uv sync`, e.g. only a code change).
set -euo pipefail

HOST="${PROCUREAI_HOST:-ubuntu@47.129.120.76}"
KEY="${PROCUREAI_SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-ap-southeast-1.pem}"
REMOTE_DIR=/home/ubuntu/procureai
API_PREFIX=/api

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# --- 1. frontend build (local) ------------------------------------------------------------------
if [[ "${SKIP_FRONTEND:-0}" != 1 ]]; then
  log "frontend: build with VITE_API_URL=$API_PREFIX"
  ( cd "$ROOT/frontend"
    [[ -d node_modules ]] || npm ci
    VITE_API_URL="$API_PREFIX" npm run build )
fi
[[ -f "$ROOT/frontend/dist/index.html" ]] || { echo "frontend/dist missing; run without SKIP_FRONTEND" >&2; exit 1; }

# --- 2. rsync -----------------------------------------------------------------------------------
log "rsync → $HOST:$REMOTE_DIR"
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" \
  --exclude .git --exclude node_modules --exclude .venv --exclude backend/out --exclude '.env' \
  --exclude __pycache__ --exclude .pytest_cache --exclude 'Hackathon Planning Doc*' --exclude .DS_Store \
  --filter 'protect backend/.env' \
  "$ROOT/" "$HOST:$REMOTE_DIR/"

# --- 3. remote ----------------------------------------------------------------------------------
log "remote: sync, install units, restart"
"${SSH[@]}" "SKIP_SYNC=${SKIP_SYNC:-0} REMOTE_DIR=$REMOTE_DIR bash -s" <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$REMOTE_DIR"

[[ -f backend/.env ]] || { echo "!! $REMOTE_DIR/backend/.env is missing — create it from backend/.env.example (see docs/DEPLOY.md)"; exit 1; }

# tooling
if ! command -v uv >/dev/null; then
  echo "-- installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
if ! command -v nginx >/dev/null; then
  echo "-- installing nginx"; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx >/dev/null
fi

# backend deps (locked)
if [[ "$SKIP_SYNC" != 1 ]]; then
  echo "-- uv sync"; ( cd backend && uv sync --frozen --no-dev -q )
fi

# systemd USER unit (same pattern as openclaw-gateway.service; linger already enabled)
mkdir -p ~/.config/systemd/user
install -m 644 deploy/procureai-backend.service ~/.config/systemd/user/procureai-backend.service
systemctl --user daemon-reload
systemctl --user enable -q procureai-backend.service
systemctl --user restart procureai-backend.service

# nginx site (the only enabled one)
sudo install -m 644 deploy/nginx-procureai.conf /etc/nginx/sites-available/procureai
sudo ln -sfn /etc/nginx/sites-available/procureai /etc/nginx/sites-enabled/procureai
sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/procureai
sudo rsync -a --delete frontend/dist/ /var/www/procureai/
sudo nginx -t -q && sudo systemctl reload nginx && sudo systemctl enable -q nginx

# OpenClaw skills (hot-reloaded by the gateway)
if [[ -d ~/.openclaw/workspace/skills ]]; then
  rsync -a openclaw/skills/ ~/.openclaw/workspace/skills/
fi

# health: wait for uvicorn, then show what the box is running
for i in $(seq 1 20); do
  if out=$(curl -sS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null); then echo "-- backend /health: $out"; break; fi
  sleep 1
  [[ $i == 20 ]] && { echo "!! backend did not come up; journalctl --user -u procureai-backend -n 50"; exit 1; }
done
echo "-- public: $(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/) / (index), $(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/api/health) /api/health"
REMOTE

log "done → http://${HOST#*@}/"
