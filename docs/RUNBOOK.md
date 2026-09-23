# ProcureAI — Presenter's Runbook

The one document to follow on demo day. It ties together docs/DEMO.md (what to say), docs/PREFLIGHT.md (what to check) and docs/DRILLS.md (what breaks and how it recovers). There are no slides: the README, the three documents, the War Room and the code are the presentation. If something here disagrees with those files, those files win; fix this one.

Slot: 30 minutes. Target: 18 minutes of presentation, the rest Q&A.

---

## 1. Read this, in this order (about 90 minutes, once, by every presenter)

| # | File | Why | Time |
|---|---|---|---|
| 1 | README.md | The pitch, the three-layer rule, the guardrails and what they caught. If you can retell this from memory you can answer half the Q&A. | 15 min |
| 2 | docs/DEMO.md | The script: segments, beats, spoken lines, likely questions. Read it twice. | 20 min |
| 3 | docs/PREFLIGHT.md | The checklist and, above all, the "Segment C commands" section. Nothing in segment C is typed from memory. | 10 min |
| 4 | docs/DRILLS.md, "Summary" table and drills a, b, g | What happens when OpenClaw or the model dies, with real timings. This is your confidence for segment C. | 10 min |
| 5 | docs/ARCHITECTURE.md, "Likely questions" and "show me the X" sections | One-paragraph answers and the file to open for each. | 15 min |
| 6 | PLAN.md §9 (completed tasks) and §13 (decisions) | Skim. Every "why did you…" question has its answer in a D-number. | 10 min |

Things you will show on screen instead of slides, in order of appearance. Practise the scrolls once:

- Segment A: README.md rendered (GitHub, or a local markdown preview; the mermaid diagram must render), then the three files from ~/Desktop/ProcureAI-demo opened in their native viewers.
- Segment D: the finished run with the "Moments" filter; README "Guardrails, and what they caught"; the three code files below; a terminal running `just test`; docs/DRILLS.md summary table; PLAN.md §13 for one second.

Code walk files:

1. `backend/procureai/engine/costing.py`, function `cost_chain` — five formula lines, one screen.
2. `backend/procureai/agents/number_guard.py` — the docstring and `numbers_the_model_was_given`.
3. `backend/procureai/engine/policy.py`, `can_open_turn` and the G2 banner above it.

---

## 2. Live or replay? Decide once, before the last rehearsal

Two ways to run the core demo (PLAN.md D30):

- **Replay** (cache in place): every LLM moment answers in ~2 s, zero network risk, chips read "via replay". Fast and safe, but a judge who reads the chip may think the run is canned.
- **Live** (cache rotated away, PREFLIGHT.md explains the two commands): every LLM moment takes 4–8 s through OpenClaw, chips read "via OpenClaw", the 8 calls cost a few cents, and the gateway fallback plus manual mode remain as safety nets. Drills a, b and g passed against exactly this setup.

**Recommendation: go live for the core run.** The waiting is 5 s per moment and you have lines to say during each one (the "What the judges should notice" column). It makes the "via OpenClaw" story literally visible, and if the number guard trips on a live rationale you get an extra moment card ("LLM output rejected by the number guard; using deterministic text") that proves the point better than any slide. Rehearse in replay to save time; do at least one full rehearsal live.

Whatever you choose, the Jev judge cache stays in place (judge probabilities are shown either way).

---

## 3. Physical setup

Screen: laptop mirrored to the projector at 1440×900 or 1280×800 (both verified). Browser zoom 100%. Dark room friendly: the UI is dark already.

Windows and tabs, left to right:

| Window | Content | Used in |
|---|---|---|
| Browser tab 1 | http://47.129.120.76/ (run list) | segments B, C |
| Browser tab 2 | http://localhost:5174/ (manual-mode frontend, red banner) | beat 11 |
| Browser tab 3 | http://localhost:5173/ (laptop mock backup) | plan B only |
| Terminal 1 | ssh session on the box, font ≥ 18 pt | beats 10, 12 |
| Terminal 2 | repo root on the laptop (`just seed`, `just preflight`) | pre-flight, Q&A scenario B |
| Terminal 3 | `just demo-manual` running (backend on :8001) | beat 11 |
| Terminal 4 | manual-mode frontend dev server on :5174 | beat 11 |
| Terminal 5 | `just run` (laptop mock backup) | plan B only |
| Finder | ~/Desktop/ProcureAI-demo with the three files | beat 1 |
| Editor | the three code files from section 1 open in tabs, font ≥ 16 pt | segment D |
| Browser tab 0 | README.md rendered (repo on GitHub, or local preview) with the mermaid diagram visible | segment A, D |
| Preview windows | the three demo documents opened (PDF viewer, Excel/Numbers, text editor) | segment A |

Paper: docs/DEMO.md beats table and PREFLIGHT.md "Segment C commands" printed.

---

## 4. Countdown

**The day before**
- `git status` clean on main; `just test` green; `just deploy` if anything changed; `just preflight` ALL PASS.
- Full rehearsal in the chosen mode (section 2), timed. Fix nothing new; note anything odd.
- Charge the laptop. Confirm the README renders with its diagram in the tab you will use (GitHub is fine if the repo is pushed; otherwise a local preview).

**T minus 30 minutes** (docs/PREFLIGHT.md, items 1–15)
- `just preflight` → ALL PASS. If a FAIL: fix, run again. Do not start with a FAIL.
- Manual items: tabs open, box terminal open, manual-mode backend and frontend running, files on the desktop, script on paper.
- If going live: rotate the cache now (PREFLIGHT.md, the `mv … llm_cache …` line). Confirm `/health` still all green.
- `just demo-reset` → run list shows exactly one CREATED run. Delete it or ignore it; you create a fresh one on stage.

**T minus 5 minutes**
- Reload tab 1. Header reads "live agents · via openclaw (reachable) · judge Jev", no banner.
- Terminal 1: `systemctl --user is-active openclaw-gateway procureai-backend` → `active` `active`.
- Tab 0 on the README top. Breathe.

---

## 5. The show

Times are cumulative targets, not limits. The spoken lines are in docs/DEMO.md; this section is the click path and the recovery per beat.

### Segment A — README + the documents (0:00–3:00)
Tab 0: read the pitch paragraph in your own words, scroll to "What happens in a run" (six lines), the three-layer table, the diagram, the agents table. Open the three documents from the desktop: "a PDF with a wrong total, a spreadsheet, an email that tries to instruct the AI". Name the four pre-mortem failures: math hallucination, infinite negotiation, price leakage, prompt injection. Switch to tab 1.

### Segment B — the core run (3:00–13:00)

| Beat | Click path | Watch for | If it goes wrong |
|---|---|---|---|
| 1 New run | Run list → New run → the Demo preset is prefilled (2,000 × Product X, 14 days, 30,000). Create. Drag the three files from the Finder onto the drop zone. | Document lane pulses; Judge lane appears. | Upload rejected: check the Finder has exactly the three files from preflight item 9. |
| 2 Extraction | Wait. Click "Moments" filter. | Three extractions, "verified" judge events, and the **injection moment** on Cobalt's email (p≈0.94). Expand one `quote.extracted` payload to show per-field confidence. | Extraction goes to the manual form (AI down): the red banner is showing; type the values from the document text (Apex 11.20/2000/13/USD) and carry on; this is beat 11 early. |
| 3 Math gate | Evaluate quotes → **Calculation mismatch** modal: stated 22,040.00 vs computed 22,800.00. Say the line. Click **Use computed total**. | State badge goes VALIDATING → … → Recommendation ready. | Modal not shown: the run is already past it; scroll the timeline to the `calc.mismatch` moment and narrate from there. |
| 4 Decision | Decision panel: Borealis 72.3, Cobalt 62.9, Apex 58.6. Point at the score bars, then the rationale. | Chip "via OpenClaw" (or "via replay"). | Chip says "via gateway": OpenClaw fell back; say so, it is a feature. Rationale is templated with an agent.failed moment: say "the number guard rejected the model's prose; the deterministic text stands". |
| 5 Negotiation gate | Start negotiation → modal with the Borealis draft. Click into the textarea, append ` Apex offered 11.20.` → **Approve & Send** → red violations (regex + judge p≈0.96). Say the line. **Reset to draft** → **Approve & Send**. | `negotiation.policy_blocked` moment in the timeline. | Violation not shown: you typed a name the filter does not know; use exactly "Apex offered 11.20". |
| 6 Counters | Approve the next three drafts as they come (Borealis round 2, Cobalt rounds 1 and 2). Pause on Cobalt's round-2 reply: the **injection moment** again. | "What changed" block: Borealis 27,618.42 → 26,763.86, Cobalt → 27,141.00, Borealis still first. | A verdict says "close" rather than "accept" at the round cap: correct, read its reason aloud. |
| 7 Interrupt | Request panel → **Demo: 2,000 → 5,000 units, budget 75,000** preset → **Inject change**. | `requirement.changed` moment, then **Recommendation changed: Borealis → Cobalt** banner and the impact table (Borealis ineligible: quantity 5000 exceeds capacity 4000). | Inject button disabled: a gate is pending; use the header's WAITING pill, resolve it, then inject. |
| 8 PO | **Request purchase order** → PO gate → approver name "demo-user" → **Approve Final Supplier & Generate PO** → PO card → **Download PO (PDF)**. | PO number PO-YYYYMMDD-xxxxxxxx; PDF opens. | PDF blocked by the browser: show the PO card; the PDF is a bonus. |

Leave tab 1 on the finished run; the summary strip shows PO_GENERATED.

### Segment C — break it on purpose (13:00–17:00)
Commands are in docs/PREFLIGHT.md "Segment C commands". Do them in this order; beat 12 needs OpenClaw back up.

| Beat | Do | Watch for | If it goes wrong |
|---|---|---|---|
| 10 Kill OpenClaw | Terminal 1: the `stop openclaw-gateway` line. Tab 1: run list → new run with quantity **2100** (a cache miss), drop the files, Evaluate, Use computed total. | Amber **AI route degraded** banner within 15 s; Decision chip **via gateway**; real prose. Then Terminal 1: the `start openclaw-gateway` line; banner clears within ~20 s. | Banner does not clear: wait one more poll (15 s). Still amber: say "the probe needs one success" and move on; check after beat 12. |
| 11 Kill the model | Tab 2 (localhost:5174, red banner). New run, drop **only** `supplier_a_apex.pdf` → manual form with the document text on the left. Type Apex Components Ltd / 11.20 / 2000 / 13 / USD → submit → Evaluate → **Compare** tab. | Landed 24,852.00, identical to the box. Lanes show agent.failed on Document and Decision: "never silent". | Tab 2 dead: skip; say the sentence and show docs/DRILLS.md drill b on the projector instead. |
| 12 Ask OpenClaw | Terminal 1: the `openclaw agent --agent main …` line. | ~25 s later: the run status in prose matching the screen. Say it cannot approve anything without explicit confirmation. | Past 45 s: run the `curl …/runs` line from PREFLIGHT.md and say "the skill reads this API". |

After: Terminal 1 `is-active` check → active active; tab 1 header without banner.

### Segment D — evidence and code (17:00–20:00)
Tab 1, "Moments" filter on the finished run: scroll top to bottom, one sentence per card (mismatch caught, injection caught twice, leak blocked, requirement changed, recommendation changed, PO after approval). Tab 0, README "Guardrails, and what they caught": the measured numbers. Editor: costing.py `cost_chain` (30 s), number_guard.py docstring (20 s), policy.py G2 banner (10 s). Terminal 2: `just test` (313 passed, no network). docs/DRILLS.md summary table: "we broke it seven ways on purpose". PLAN.md §13 for one second. Then: "What we would do next: real email and ERP connectors, supplier history in DynamoDB, multi-product requests, negotiation boundaries learned from history." Open the floor.

### Segment E — Q&A (20:00–30:00)
Answers in docs/DEMO.md and docs/ARCHITECTURE.md. Two prepared demonstrations if asked:
- "Does it generalise?" → Terminal 2: `just seed recommended --scenario b` (about 1 s), open the run URL: four quotes, Delta blacklisted, Eiger's letter-style PDF, Granite's transposed total. Then `just seed po --scenario b` for the budget-cut flip to Fjord.
- "What does the audit trail look like?" → tab 1, "All" filter, expand any payload; or `curl http://47.129.120.76/api/runs/<id>/events | head`.

---

## 6. Plan B ladder (memorise the order)

1. OpenClaw down → nothing to do; the gateway fallback is automatic (amber banner). Continue.
2. Gateway down too → red banner; manual form for extraction; templated prose; identical numbers. Continue and say so.
3. Box unreachable → tab 3 (localhost:5173, laptop mock mode). Same click path; chips say "via replay"; say "this is the offline mode we ship for exactly this reason".
4. Browser tab dies → reopen the run URL; run and timeline are persisted.
5. Projector dies → docs/screenshots/ on the laptop screen; narrate from them.

---

## 7. After the demo

- If the cache was rotated: put it back (PREFLIGHT.md restore line).
- `just demo-reset` to clear the runs, or leave the demo run for judges who want to click.
- Terminal 1: `systemctl --user is-active openclaw-gateway procureai-backend` → active active.

---

## 8. Who says what (fill in by Sep 30, OQ6)

| Segment | Presenter | Backup |
|---|---|---|
| A README + documents | | |
| B core run (driver + narrator can be two people) | | |
| C break it | | |
| D moments + README evidence + code | | |
| E Q&A: architecture / guardrails / OpenClaw + infra / business | | |
