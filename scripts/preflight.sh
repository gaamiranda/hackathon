#!/usr/bin/env bash
# Pre-flight checklist for the demo (T18, docs/PREFLIGHT.md): `just preflight`, 30 minutes before going on stage.
# Every automatable item prints one PASS/FAIL line; the exit code is non-zero when anything FAILed. Items that
# cannot be checked from a script (browser tab, printed script) are listed as MANUAL at the end.
#
# Env overrides: PROCUREAI_HOST (ubuntu@47.129.120.76), PROCUREAI_SSH_KEY (~/.ssh/LightsailDefaultKey-ap-southeast-1.pem),
#                PROCUREAI_URL (http://47.129.120.76), DEMO_FILES_DIR (~/Desktop/ProcureAI-demo),
#                SKIP_SEED=1 (skip the cache-warm seed; saves ~2 s and one run in the list).
set -uo pipefail

HOST="${PROCUREAI_HOST:-ubuntu@47.129.120.76}"
KEY="${PROCUREAI_SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-ap-southeast-1.pem}"
URL="${PROCUREAI_URL:-http://47.129.120.76}"
FILES_DIR="${DEMO_FILES_DIR:-$HOME/Desktop/ProcureAI-demo}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$HOST")
DOCS=(supplier_a_apex.pdf supplier_b_borealis.xlsx supplier_c_cobalt.eml.txt)

fails=0
pass() { printf '\033[1;32mPASS\033[0m  %-34s %s\n' "$1" "${2:-}"; }
fail() { printf '\033[1;31mFAIL\033[0m  %-34s %s\n' "$1" "${2:-}"; fails=$((fails + 1)); }
manual() { printf '\033[1;33mMANUAL\033[0m %-33s %s\n' "$1" "${2:-}"; }
check() { local name=$1 detail=$2; shift 2; if "$@" >/dev/null 2>&1; then pass "$name" "$detail"; else fail "$name" "$detail"; fi; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }

echo "ProcureAI pre-flight — $URL — $(date '+%Y-%m-%d %H:%M %Z')"
echo

# --- 1. box reachable ---------------------------------------------------------------------------
if code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL/"); [[ "$code" == 200 ]]; then
  pass "box: http GET /" "$code"
else
  fail "box: http GET /" "HTTP $code (expected 200)"
fi
if "${SSH[@]}" true 2>/dev/null; then pass "box: ssh" "$HOST"; else fail "box: ssh" "cannot ssh to $HOST with $KEY"; fi

# --- 2. /health ---------------------------------------------------------------------------------
health=$(curl -s --max-time 8 "$URL/api/health" || true)
if [[ -z "$health" ]]; then
  fail "/health" "no answer from $URL/api/health"
else
  for want in "mode:live" "llm_backend:openclaw" "openclaw:reachable" "guardrail_judge:jev" "llm:ok" "runs_persisted:True"; do
    k=${want%%:*}; v=${want#*:}
    got=$(printf '%s' "$health" | json "['$k']")
    if [[ "$got" == "$v" ]]; then pass "/health $k" "$got"; else fail "/health $k" "got '${got:-missing}', want $v"; fi
  done
  gw=$(printf '%s' "$health" | json "['gateway']")
  [[ "$gw" == reachable ]] && pass "/health gateway" "$gw" || fail "/health gateway" "got '${gw:-missing}' (direct gateway fallback would not work)"
  echo "      $health"
fi

# --- 3. units -----------------------------------------------------------------------------------
units=$("${SSH[@]}" 'systemctl --user is-active openclaw-gateway procureai-backend; systemctl is-active nginx' 2>/dev/null | tr '\n' ' ')
read -r oc be ng <<<"$units"
[[ "$oc" == active ]] && pass "unit openclaw-gateway (user)" active || fail "unit openclaw-gateway (user)" "${oc:-unknown}"
[[ "$be" == active ]] && pass "unit procureai-backend (user)" active || fail "unit procureai-backend (user)" "${be:-unknown}"
[[ "$ng" == active ]] && pass "unit nginx" active || fail "unit nginx" "${ng:-unknown}"

# --- 4. disk and memory -------------------------------------------------------------------------
res=$("${SSH[@]}" "df -P / | awk 'NR==2{print \$5}' | tr -d %; free -m | awk 'NR==2{print \$7}'" 2>/dev/null | tr '\n' ' ')
read -r disk_pct mem_avail <<<"$res"
[[ -n "${disk_pct:-}" && "$disk_pct" -lt 80 ]] && pass "disk / used" "${disk_pct}% (< 80%)" || fail "disk / used" "${disk_pct:-?}% (want < 80%)"
[[ -n "${mem_avail:-}" && "$mem_avail" -gt 500 ]] && pass "memory available" "${mem_avail} MB (> 500 MB)" || fail "memory available" "${mem_avail:-?} MB (want > 500 MB)"

# --- 5. cache warm: seed to recommended, every agent.finished via replay/openclaw, no agent.failed --
if [[ "${SKIP_SEED:-0}" == 1 ]]; then
  manual "cache warm (SKIP_SEED=1)" "just seed recommended → agent.finished via replay/openclaw"
else
  seed_out=$(cd "$ROOT/backend" && uv run python scripts/seed_demo.py recommended --base "$URL/api" 2>&1)
  routes=$(printf '%s' "$seed_out" | sed -n 's/.*agent.finished via //p')
  url=$(printf '%s\n' "$seed_out" | tail -1)
  if [[ -z "$routes" ]]; then
    fail "cache warm: seed recommended" "$(printf '%s' "$seed_out" | tail -3 | tr '\n' ' ')"
  elif printf '%s' "$routes" | grep -Eq '"(FAILED|gateway|template)"'; then
    fail "cache warm: seed recommended" "$routes ($url)"
  else
    pass "cache warm: seed recommended" "$routes ($url)"
  fi
fi

# --- 6. laptop backup: mock-mode backend + frontend (`just run`) ---------------------------------
local_health=$(curl -s --max-time 2 http://127.0.0.1:8000/health || true)
if [[ "$(printf '%s' "$local_health" | json "['mode']")" == mock ]]; then
  pass "laptop backup: backend :8000" "mode mock"
else
  fail "laptop backup: backend :8000" "not answering in mock mode — start \`just run\` in another terminal"
fi
if curl -s -o /dev/null --max-time 2 http://localhost:5173/; then pass "laptop backup: frontend :5173" "up"; else fail "laptop backup: frontend :5173" "not answering — \`just run\`"; fi

# --- 7. the three files on the desktop ----------------------------------------------------------
missing=()
for f in "${DOCS[@]}"; do
  [[ -f "$FILES_DIR/$f" ]] && cmp -s "$FILES_DIR/$f" "$ROOT/data/synthetic/$f" || missing+=("$f")
done
if [[ ${#missing[@]} -eq 0 ]]; then
  pass "demo files on the desktop" "$FILES_DIR"
else
  fail "demo files on the desktop" "missing/stale in $FILES_DIR: ${missing[*]} — mkdir -p '$FILES_DIR' && cp data/synthetic/{supplier_a_apex.pdf,supplier_b_borealis.xlsx,supplier_c_cobalt.eml.txt} '$FILES_DIR/'"
fi

# --- 8. cannot be checked from here -------------------------------------------------------------
echo
manual "browser tab" "$URL/ open on the run list (and the laptop tab http://localhost:5173/ behind it)"
manual "printed script" "docs/DEMO.md + docs/PREFLIGHT.md 'Segment C commands' on paper"
manual "second terminal" "ssh session open on the box: ssh -i $KEY $HOST"

echo
if [[ $fails -eq 0 ]]; then echo "ALL PASS"; else echo "$fails FAIL"; fi
exit $(( fails > 0 ))
