# DRILLS — failure rehearsals and demo timings (T18, 2026-09-22)

Every drill below was executed for real against the box (http://47.129.120.76/, ap-southeast-1) on 2026-09-22,
between 18:45 and 19:05 +08. "Recovery" is wall clock from the restoring command to `/health` reporting
`"llm":"ok"` (or to the feature working again). The pre-flight checklist is docs/PREFLIGHT.md; the commands for
the three on-stage break-it beats are under its "Segment C commands" heading.

## Summary

| # | Drill | Outcome | Recovery |
|---|-------|---------|----------|
| a | Stop `openclaw-gateway` during a run | PASS (defect found and fixed) | 21–29 s |
| b | `LLM_GATEWAY_URL` at a closed port + OpenClaw stopped | PASS | 23 s |
| c | `systemctl --user restart procureai-backend` mid-negotiation | PASS | 1.9 s |
| d | Corrupt PDF and 25 MB upload | PASS | none needed |
| e | Reboot the Lightsail instance | PASS | 49 s to all-green |
| f | Interrupt while a negotiation draft is pending | PASS | none needed |
| g | DEMO.md segment C beats 10–12 as scripted | PASS (beat 12 needs OpenClaw up — see below) | 16 s |

## a. Stop the OpenClaw gateway during a run

```bash
ssh box 'systemctl --user stop openclaw-gateway'      # then evaluate a run whose prompt is not in the cache
```

A cache **miss** is required to see the fallback: the demo request replays, so nothing goes out on the wire.
Quantity 2,100 (instead of 2,000) gives a new decision prompt while the three documents still replay.

- `/health` went `openclaw: unreachable, gateway: reachable, llm: degraded` within one poll (≤ 15 s); the War Room
  showed the amber **"AI route degraded: using fallback gateway"** banner and the header `via OpenClaw (unreachable)`.
- The run continued: the Decision Agent's `agent.finished` carried `backend: gateway` ("via gateway" chip), real
  prose, no `agent.failed`, Borealis still recommended. Total cost of the outage on the beat: ~5 s of extra latency.
- **Defect found:** after `systemctl --user start openclaw-gateway`, `/health` stayed `degraded` indefinitely. The
  route tracker kept the failed attempt until the *next real LLM call*, and a replay demo never makes one, so the
  amber banner would have stayed on screen for the rest of the demo.
  **Fixed** (`llm/status.py` `RouteTracker.forget_failure_before` + `api/app.py`): a fresh probe that finds a route
  reachable clears a failure recorded before that probe. Re-run after the fix: `llm` back to `ok` 21 s after `start`
  (≈18 s of it is OpenClaw's own boot), banner gone without a page reload. Covered by
  `tests/test_manual_mode.py::test_fresh_reachable_probe_clears_a_stale_route_failure`.

## b. Gateway pointed at a closed port, OpenClaw stopped (plan B step 2 → manual mode)

```bash
ssh box "sed -i 's|^LLM_GATEWAY_URL=.*|LLM_GATEWAY_URL=http://127.0.0.1:9|' ~/procureai/backend/.env \
  && systemctl --user stop openclaw-gateway && systemctl --user restart procureai-backend"
```

- `/health`: `openclaw: unreachable, gateway: unreachable, llm: down`; red **"AI unavailable — manual mode"** banner.
- Uploading a document whose text is not in the cache (the Cobalt email plus one extra line) took 7.1 s — three
  gateway attempts with 2 s + 4 s backoff (`llm/gateway.py RETRY_DELAYS_S`) after OpenClaw refused — and ended in
  `NEEDS_HUMAN_EXTRACTION` with `agent.failed reason=llm_unavailable`, never a 500. The UI showed the manual
  extraction form with the full document text beside the fields (screenshot path reproduced in docs/screenshots).
- The judge still ran (injection detected, critical fields unsupported): Jev is a separate service and was unaffected.
- **Restore** (same `sed` back to `https://api.softwaresystems.app`, `systemctl --user start openclaw-gateway`,
  restart the backend): all green after **23 s**, with a ~10 s window of `llm: down` while OpenClaw booted.
- On stage prefer the laptop's `just demo-manual` on :8001 (beat 11) — it needs no restart of the box.

## c. Restart the backend mid-negotiation

```bash
ssh box 'systemctl --user restart procureai-backend'   # run sitting at AWAITING_NEGOTIATION_APPROVAL, sup_b round 1
```

- The API answered again **1.9 s** after the command, having logged `loaded 14 runs from /home/ubuntu/procureai/data/runs`.
- The run was still `AWAITING_NEGOTIATION_APPROVAL` for `sup_b` round 1, the pending draft **byte-identical**, 41 events
  present, last seq unchanged (40), no `run.recovered` event (a waiting state is loaded as-is, T19).
- Approving after the restart worked and the workflow carried on (seq 40 → 49). An open War Room tab reconnects its
  SSE stream by itself; nothing to click.

## d. Corrupt PDF and oversized uploads

| Input | Result | Time |
|---|---|---|
| `corrupt.pdf` (PDF header, garbage body) | **422** `could not read corrupt.pdf: Stream has ended unexpectedly`, shown in the upload panel in red | 19 ms |
| 25 MB `.pdf` | **413** from nginx (`client_max_body_size 20m`), UI shows "413 Request Entity Too Large" | 17 ms (2.8 s from the browser, upload included) |
| 25 MB `.txt` | **413** from nginx, same message | 20 ms |
| 15 MB `.txt` (under the nginx limit) | **413** from the backend: `big15mb.txt: extracted text is 15000000 chars; limit is 6000` | 1.7 s |

The run stayed in `CREATED` with 0 documents and a single `run.created` event after all four; uploading the three
real quotes into the same run afterwards worked normally (`EXTRACTED`, 3 quotes). Nothing to recover.

*Known cosmetic issue:* an nginx 413 is an HTML page, so the UI prints the generic `413 Request Entity Too Large`
rather than a sentence naming the file. The backend's own 413 (>6,000 chars of text) is readable. Not worth changing
before the demo — workaround: say "nginx rejected it before it reached us; here is what the backend says" and drop
the 15 MB text file instead.

## e. Reboot the instance

```bash
ssh box 'sudo reboot'
```

| Milestone | Wall clock |
|---|---|
| ssh answers again | 20 s |
| `/api/health` answers, backend + nginx up, **all 14 runs reloaded** | 24 s |
| OpenClaw up, `llm: ok`, everything green | **49 s** |

`loginctl enable-linger ubuntu` does its job: both user units came back without a login, nginx with the system, and
`journalctl --user -u procureai-backend -b` showed `loaded 14 runs from /home/ubuntu/procureai/data/runs`. Well inside
the 2-minute expectation. If it ever exceeds ~90 s on stage, switch to the laptop (plan B step 3) and keep talking.

## f. Interrupt with a negotiation draft pending

Seeded to `recommended`, started the negotiation, approved sup_b round 1, then interrupted with 5,000 / 75,000 while
sup_b's round-2 draft was pending:

- `negotiation.discarded`: *"Unsent round 2 draft to sup_b discarded by the requirement change; keeping the 12.55/unit…"*
- `replan.started` → `replan.completed`: *"Replan v1 → v2 complete. Recommendation changed: sup_b → sup_c; sup_b no
  longer eligible"*, `pending_human` cleared, state `RECOMMENDED`, Cobalt recommended. **12.5 s**, of which ~12 s was
  two live OpenClaw calls: a mid-negotiation replan is *not* in the demo cache (the demo interrupts after the
  negotiation settles, which is cached). No `agent.failed`.
- On stage this combination does not occur — beat 7 comes after beat 6 finishes. If it happens by accident, say
  "the draft was never sent, and the agents are revisiting the quotes we already have" and let the 12 s run.

## g. DEMO.md segment C, exactly as scripted

Beats 10 and 11 are drills (a) and (b) above, performed through the UI; beat 12 three times in a row:

```bash
ssh box 'export PATH=$HOME/.nvm/versions/node/v24.21.0/bin:$PATH
  openclaw agent --agent main --timeout 90 -m "what is the status of the latest procurement run and is anything waiting for me?"'
```

23.1 s (cold session) then 9.7 s and 9.6 s. Every answer named the latest run, its state, and the two runs waiting for
a human (`AWAITING_NEGOTIATION_APPROVAL`, `NEEDS_HUMAN_EXTRACTION`) — matching `/runs` exactly.

**Ordering constraint found:** beat 12 needs `openclaw-gateway` running, so the restart at the end of beat 10 must
happen *before* beat 12 (attempted in between, the CLI fails with `Gateway not reachable at ws://127.0.0.1:18789`
after ~3 s). The restart is written into the beat-10 block of docs/PREFLIGHT.md for that reason. After a restart,
allow ~20 s before beat 12 or the first call is the slow cold one.

## Timings — segments B and C, three live runs (cache warm)

Machine time only: the seconds between a click and the screen settling, measured over the API against the box, with
no narration. Beat numbers are docs/DEMO.md's; beat 2 (extraction moments) is inside beat 1, beats 4 and 9 are
reading, not waiting.

| Beat | What | Run 1 | Run 2 | Run 3 | Notes |
|---|---|---|---|---|---|
| 1 | Create run + drop the 3 files (extraction + judge) | 0.2 s | 0.2 s | 0.2 s | all three "via replay"; 3 Jev calls replayed |
| 3 | Evaluate → mismatch gate → *Use computed total* | 0.2 s | 0.2 s | 0.2 s | includes the rationale (via replay) |
| 5 | Start negotiation, edited draft blocked, approve | 0.2 s | 0.2 s | 0.2 s | leak edit → 422 `policy_violation` |
| 6 | Approve the remaining drafts (3) + re-score | 0.3 s | 0.3 s | 0.3 s | change explanation via replay |
| 7 | Interrupt 5,000 / 75,000 → replan | 0.1 s | 0.1 s | 0.1 s | B out on capacity, C in |
| 8 | Request PO → approve → PDF | 0.1 s | 0.1 s | 0.1 s | 2 KB PDF |
| **Segment B total (machine)** | | **1.0 s** | **1.0 s** | **1.1 s** | narration is the whole budget |
| 10 | Stop OpenClaw, second run, restart it | 1.2 s | 1.1 s | 1.1 s | with a cache **miss** (qty 2,100): ~5 s, "via gateway" |
| 11 | Laptop :8001 manual form → evaluate → compare | 12.1 s | 12.1 s | 12.1 s | 12 s is the retry backoff, not typing |
| 12 | OpenClaw chat: run status | 4.3 s* | 3.0 s* | 2.8 s* | *gateway was down in those runs; measured separately: 23.1 / 9.7 / 9.6 s |
| 10r | `/health` back to `ok` after restarting OpenClaw | 6.1 s | 10.3 s | 10.3 s | banner clears by itself |
| **Segment C total (machine)** | | **23.7 s** | **26.5 s** | **26.2 s** | + beat 12's ~10–23 s when OpenClaw is up |

Also measured, end to end through the real UI (clicking every beat of segment B in Chrome): upload → PO in **under
90 seconds** including reading each panel — comfortably inside the 10-minute slot; the constraint is speech, not the
software. Segment C fits in 4 minutes with ~40 s of slack.

### Beats with more than 60 s of pure waiting

None. The longest single wait is **beat 11's 12 s** (three gateway attempts with 2 s + 4 s backoff before the manual
form appears) and, if the cache is rotated for a live demo (D30), **~5 s per LLM moment**. Two fillers to have ready:

- **Beat 11, 12 s:** "It is retrying — twice, with backoff — because a network blip must not send a buyer to a manual
  form. Watch the lane: the Document Agent will report `agent.failed`, not a silent success." Then read the document
  text panel aloud while the form appears.
- **Beat 10 with a cache miss, ~5 s:** "OpenClaw is down, so this call went straight to the gateway; the chip will say
  *via gateway* instead of *via OpenClaw*. Same prompt, same guard, same number — only the route changed."
- **Beat 12, up to 23 s cold:** run one throwaway `openclaw agent --agent main -m "ready?"` during the pre-flight so
  the session is warm; then it answers in ~10 s. If it stalls, fall back to the `curl /runs` line in PREFLIGHT.md.

## Cache re-recording (Part 1 step 3)

The whole demo was re-recorded live through OpenClaw once, with the box's cache moved aside
(`mv ~/procureai/data/llm_cache ~/llm_cache.pre_t18 && mkdir ~/procureai/data/llm_cache`):

- **8 live calls** (3 `extract`, 3 `explain`, 2 `explain_diff`), 54.8 s for the whole run to PO — inside the ≤ 10
  budget and matching D30's "~45 s". Every one went `via OpenClaw`, none fell back to the gateway.
- **Every recorded key was byte-identical to the committed one.** That is the proof that the Part 1 fix holds: the
  same prompts, recorded on a different day, hash to the same eight files.
- The committed recordings were therefore kept (the re-recorded `explain_diff` prose is thinner — it says
  "negotiated lower unit prices" instead of naming 12.80 → 12.40 and 13.40 → 12.45, which beat 6 points at) and
  rsynced back over the box's cache. The re-recorded copies are not committed.
- **Verification:** a fresh run created from the UI preset (click *Create run*, drop the three files, *Evaluate*,
  *Use computed total*) and a full `just seed po` both replayed with **zero** completions to OpenClaw or the gateway
  (`journalctl` shows only the free `GET /api/tags` health probes). Every `agent.finished` chip reads *via replay*;
  the whole seven-stage run takes 1.1 s.

## Seed timings (`just seed STAGE`, box, cache warm)

| Stage | created | extracted | mismatch | recommended | negotiated | replanned | po |
|---|---|---|---|---|---|---|---|
| Wall clock | 0.35 s | 0.36 s | 0.40 s | 0.54 s | 0.97 s | 1.11 s | 1.18 s |

All well inside the 15 s budget (the acceptance criterion). A cold cache adds one LLM call per decision moment
(~5 s each through OpenClaw). `just demo-reset` = `just clear-runs` + `just seed created`.
