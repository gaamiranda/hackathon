# PREFLIGHT — 30 minutes before the demo (T18)

`just preflight` runs every checkable line below and prints PASS/FAIL (exit 1 on any FAIL). Run it, fix the FAILs,
run it again, then do the MANUAL lines. Everything here targets the box **http://47.129.120.76/**
(`ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76`, alias `box` below).
Expected outputs were recorded on 2026-09-22 (docs/DRILLS.md has the drills and timings).

## Checklist

| # | Item | Command | Expected |
|---|------|---------|----------|
| 1 | Box reachable | `curl -s -o /dev/null -w '%{http_code}\n' http://47.129.120.76/` | `200` |
| 2 | ssh works | `ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76 true && echo ok` | `ok` |
| 3 | /health all green | `curl -s http://47.129.120.76/api/health` | `"mode":"live"`, `"llm_backend":"openclaw"`, `"openclaw":"reachable"`, `"gateway":"reachable"`, `"llm":"ok"`, `"guardrail_judge":"jev"`, `"judge_model":"jev-1.13.0"`, `"runs_persisted":true` |
| 4 | Units active | on the box: `systemctl --user is-active openclaw-gateway procureai-backend; systemctl is-active nginx` | three lines of `active` |
| 5 | Disk | on the box: `df -h /` | `Use%` < 80% (5% on 2026-09-22) |
| 6 | Memory | on the box: `free -m` | `available` > 500 MB (≈2,600 MB with OpenClaw idle) |
| 7 | Cache warm | `just seed recommended` | last lines `done in ≈0.5s; agent.finished via {"replay": 4, "-": 3}` then the run URL. Every decision/extraction moment `via replay` (or `via OpenClaw` if you rotated the cache for a live demo); no `gateway`, `template` or `FAILED` |
| 8 | Laptop backup (plan B step 3) | in a second terminal: `just run`; then `curl -s http://127.0.0.1:8000/health` | `{"mode":"mock",…}` and http://localhost:5173/ loads the run list ("mock agents") |
| 9 | Demo files on the desktop | `mkdir -p ~/Desktop/ProcureAI-demo && cp data/synthetic/{supplier_a_apex.pdf,supplier_b_borealis.xlsx,supplier_c_cobalt.eml.txt} ~/Desktop/ProcureAI-demo/` | Finder window with exactly the three files (preflight compares them byte for byte with `data/synthetic/`) |
| 10 | Run list clean | `just demo-reset` (asks `y`) | `N run(s) …` → deleted → `/health` printed → `just seed created` prints one run URL. The run list shows one CREATED run |
| 11 | Browser tab | open http://47.129.120.76/ (run list) in a tab; http://localhost:5173/ in the tab behind it | header reads `live agents · via openclaw (reachable) · judge Jev`, no banner under the header | MANUAL
| 12 | Second terminal on the box | `ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.129.120.76` left open, font size ≥ 18 pt | prompt on the box, ready for the Segment C commands | MANUAL |
| 13 | Manual-mode backend for beat 11 | third terminal: `just demo-manual`; fourth: `cd frontend && VITE_API_URL=http://localhost:8001 npm run dev -- --port 5174 --strictPort` | http://localhost:5174/ shows the red **AI unavailable — manual mode** banner | MANUAL |
| 14 | Script printed | docs/DEMO.md and this file's "Segment C commands" on paper | — | MANUAL |
| 15 | OpenClaw crons that spend credit | on the box: `export PATH=$HOME/.nvm/versions/node/v24.21.0/bin:$PATH; openclaw cron list` | `skill-collection-review…` rows show `disabled` (else `openclaw cron disable <id>`, ids in docs/DEPLOY.md) | MANUAL |

Replay vs live (D30): with the repo's cache on the box and `LLM_CACHE_MODE=replay_or_record` every demo LLM call
replays (chips say **via replay**, ≈2 s per moment, zero credit). To run the 8 calls live through OpenClaw
(chips **via OpenClaw**, ≈45 s total, gateway fallback still automatic) rotate the cache **after** item 7:
`ssh box 'mv ~/procureai/data/llm_cache ~/llm_cache.bak && mkdir -p ~/procureai/data/llm_cache'` — and put it back
afterwards (`rm -rf ~/procureai/data/llm_cache && mv ~/llm_cache.bak ~/procureai/data/llm_cache`). No restart needed:
the cache is read per call. Note the replay safety net only exists while the cache is in place; when it is rotated
away the safety net is the gateway fallback and then manual mode.

## Segment C commands (nothing typed from memory)

Open the box terminal (item 12) next to the War Room. Beat numbers are docs/DEMO.md's.

**Beat 10 — kill the agent runtime (≈50 s):**

```bash
# on the box
systemctl --user stop openclaw-gateway && systemctl --user is-active openclaw-gateway   # prints: inactive
```

In the War Room, within 15 s the header shows **AI route degraded: using fallback gateway** (amber). The current run
is finished (PO_GENERATED), so start one that misses the cache: run list → click the preset **Demo A: 2,000 Product X, 14 days, 30,000 USD** → set Quantity to
**2100** → Create run → drop the three files → Evaluate → Use computed total. Extraction still says *via replay*
(the documents are the same text); the Decision Agent's chip says **via gateway** and the rationale is real prose.
Then restore, still on the box:

```bash
systemctl --user start openclaw-gateway && sleep 3 && curl -s http://127.0.0.1:8000/health | grep -o '"openclaw":"[a-z]*"'   # "openclaw":"reachable"
```

The banner clears on the next /health poll (≤ 15 s; it needs one successful probe — the gateway takes ~3 s to listen).
If the run list is busy, the same thing works with `just seed recommended` from the laptop after editing nothing:
it replays entirely (chips *via replay*) — that is why the 2,100 quantity is needed to show the fallback.

**Beat 11 — kill the model (≈60 s):** on the laptop, tab http://localhost:5174/ (item 13). Create the demo run, drop
`supplier_a_apex.pdf` only → **AI unavailable · manual entry** form with the document text → type
`Apex Components Ltd`, unit price `11.20`, MOQ `500`, lead time `13`, quantity `2000`, shipping cost `400`, currency `USD`,
"Total printed on the document" left empty → **Save quote** → Evaluate → Compare tab shows landed 24,852.00 — the same number as on the box (24,416.00 means the
shipping cost was left out). Nothing to restore: this backend is a throwaway.

The box variant (drill b in docs/DRILLS.md) is: `sed -i 's|^LLM_GATEWAY_URL=.*|LLM_GATEWAY_URL=http://127.0.0.1:9|' ~/procureai/backend/.env && systemctl --user stop openclaw-gateway && systemctl --user restart procureai-backend`,
restore with `sed -i 's|^LLM_GATEWAY_URL=.*|LLM_GATEWAY_URL=https://api.softwaresystems.app|' ~/procureai/backend/.env && systemctl --user start openclaw-gateway && systemctl --user restart procureai-backend`.
Avoid it on stage unless the laptop tab is dead: it restarts the backend (runs are persisted, but the timeline stream reconnects).

**Beat 12 — OpenClaw as a colleague (≈40 s):** on the box:

```bash
export PATH=$HOME/.nvm/versions/node/v24.21.0/bin:$PATH
openclaw agent --agent main --timeout 60 -m "what is the status of the latest procurement run and is anything waiting for me?"
```

Answer in ≈25 s (docs/DEPLOY.md; the run-status skill lives on the full `main` agent, not on the tool-less `procureai` one). If it stalls past 45 s, say "the skill just reads our /runs API — here is the same
call" and run `curl -s http://127.0.0.1:8000/runs | tail -c 400`.

**After segment C:** `systemctl --user is-active openclaw-gateway procureai-backend` → `active` `active`;
War Room header without a banner.
