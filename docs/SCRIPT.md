# ProcureAI — Video Recording Script

This is a script for the recorded demo video, scene by scene. Each scene is recorded on its own, in one or two
takes, and the scenes are joined in editing. For the recorded version it replaces the live flow in
docs/RUNBOOK.md §5. Where this file and the running app disagree, the app wins; fix this file. UI text below was
checked against the War Room on 2026-09-23.

**Target length:** about 17½ minutes across 17 scenes (see the index). Cuts to reach 15 minutes are listed in
"Editing notes".

---

## 1. Setup

### Screen and sound

- Display at **1440×900**, browser zoom **100%**, macOS in dark mode (Finder, editor and terminal dark as well;
  the War Room is dark already). Terminal and editor font at 18 pt or larger.
- Leave the pointer visible and do not enable click highlighting; the script names every click. Turn on Do Not
  Disturb, quit Slack and mail, and hide the Dock.
- Record the browser without the URL bar, because the run id in the URL changes whenever you reseed. One way is
  an app-mode window: `open -na "Google Chrome" --args --app=http://47.129.120.76/`. The other is to crop the
  top strip when editing.
- Mic: a close mic, the same room and distance for every scene. Record 5 s of room tone at the start of each
  session. Read at a steady pace of about 140 words a minute. Leave 1 s of silence before the first word and after
  the last.

### Mode: live through OpenClaw, cache rotated

Record against the box, **http://47.129.120.76/**, in live mode with the replay cache moved aside (PREFLIGHT.md,
"Replay vs live"). Every LLM moment then runs through OpenClaw, and the route chips read **via OpenClaw**. Each
moment takes 4–8 s, which is fine because the editor can trim the waits.

**Why a retake can look different.** The box runs `LLM_CACHE_MODE=replay_or_record`, so the first live call
writes its answer into the (new, empty) cache. The same prompt in a retake then *replays*: its chip reads
**via replay** and the moment appears in about 2 s rather than about 5 s. To get "via OpenClaw" again, rotate
the cache once more before the retake (command R2 in Appendix B). This moves the take's recordings aside and
deletes nothing. The original cache stays in `~/llm_cache.bak` until the restore line at the end.

Chips only appear on `agent.finished` rows, which are visible under the **All** filter or a lane filter. Scenes
that stay on **Moments** show no chips, so chip consistency matters only in S6 and S11.

`just seed …` without `--base` targets the box, which is what you want here. On a laptop running `just run` in
mock mode, add `--base http://127.0.0.1:8000`. The run URL is then on localhost:5173, and mock agents carry *no*
route chip at all.

### One-time preparation (in this order)

1. `just preflight` shows ALL PASS (PREFLIGHT.md items 1–10). Also do manual items 12, 13 and 15.
2. Put the demo files on the desktop (PREFLIGHT item 9). One Finder window of `~/Desktop/ProcureAI-demo`, list
   view, showing exactly `supplier_a_apex.pdf`, `supplier_b_borealis.xlsx` and `supplier_c_cobalt.eml.txt`.
3. Open the three documents in their native viewers (Preview, Numbers or Excel, TextEdit), one window each, and
   minimise them.
4. Browser tab 0: README.md rendered on GitHub or in a local preview, with the mermaid diagram visible.
5. Browser tab 1: http://47.129.120.76/ (run list). Browser tab 2: http://localhost:5174/ (manual mode, needs
   PREFLIGHT item 13).
6. Editor tabs: `backend/procureai/engine/costing.py` (at `def cost_chain`, line 61),
   `backend/procureai/agents/number_guard.py` (docstring and `numbers_the_model_was_given`, line 38),
   `backend/procureai/engine/policy.py` (at `def can_open_turn`, line 127, with the G2 comment above it).
7. Terminals as in RUNBOOK §3. **T1** is ssh on the box. **T2** is the repo root on the laptop. **T3** runs
   `just demo-manual`. **T4** runs the :5174 frontend.
8. Warm OpenClaw's chat session for S13: in T1, run the S13 command once with `-m "ready?"`.
9. Rotate the cache (Appendix B, R1). Then run `curl -s http://47.129.120.76/api/health`: it must still show
   `"openclaw":"reachable"` and `"llm":"ok"`.
10. Reload tab 1. The New run panel reads `live agents · via openclaw (reachable) · judge Jev`, and there is no
    banner.

---

## 2. Scene index

| # | Title | Target | Start state | On screen | Purpose |
|---|---|---|---|---|---|
| S1 | Cold open | 1:00 | fresh | the three documents, then README top | The buyer's problem and the four failures we guard against |
| S2 | Architecture | 1:10 | fresh | README: layers, diagram, agents | Three layers, one rule |
| S3 | New run and upload | 0:45 | tab 1 run list | War Room, run list → new run | One request, three messy files |
| S4 | Extraction, first injection | 1:00 | continue S3, or `just seed extracted` | War Room, Moments | Structured quotes; injection treated as data |
| S5 | The math gate | 0:50 | `just seed mismatch` | Math check modal | The AI never does arithmetic |
| S6 | The decision | 1:00 | `just seed recommended` | Decision panel | Engine scores, agent only explains |
| S7 | Negotiation gate, leak blocked | 1:15 | `just seed recommended` | Approve message modal | Nothing is sent without approval; leaks are blocked |
| S8 | Counter-offers, second injection | 1:15 | continue from S7 (no seed stops mid-negotiation) | modals, then Moments | Bounded negotiation, rescoring |
| S9 | The interrupt | 1:00 | `just seed negotiated` | Inject change, Replan impact | Replan without restart |
| S10 | The PO gate | 0:50 | `just seed replanned` | Approve PO modal, PO card, PDF | Only a human generates the PO |
| S11 | Break it: OpenClaw down | 1:30 | any; T1 open | T1 + War Room | Automatic gateway fallback |
| S12 | Break it: model down | 1:15 | tab 2 (localhost:5174) | manual-entry form, Compare | Manual mode, identical numbers |
| S13 | OpenClaw as a colleague | 0:50 | OpenClaw up ≥ 20 s | T1 | The chat surface reads our API |
| S14 | Evidence: Moments | 1:00 | the S3–S10 run, or `just seed po` | War Room, Moments | The audit trail is the proof |
| S15 | Code walk and tests | 1:20 | editor + T2 | three files, `just test` | Where the guarantees live |
| S16 | Does it generalise? | 1:05 | `just seed po --scenario b` | War Room | Second story, zero code changes |
| S17 | Close | 0:30 | README top | README | What next, the URL |

Total: **17:35**.

---

## 3. Scenes

### S1 — Cold open

- **Start state:** nothing to run. The three document windows are minimised; tab 0 is at the README top.
- **Screen:** desktop with the Finder window of `~/Desktop/ProcureAI-demo`.
- **Script:**

  > [open supplier_a_apex.pdf] A buyer asks three suppliers for a price. This is what comes back: a PDF,
  > [open supplier_b_borealis.xlsx] a spreadsheet, [open supplier_c_cobalt.eml.txt] and an email. Someone
  > types all of it into a spreadsheet, spends half an hour comparing, and usually negotiates nothing.
  > [switch to the README tab] We built ProcureAI: an AI procurement team that does that work and asks a person
  > only when it matters. Four agents read, check, compare and negotiate. A plain Python engine owns every
  > number. And four moments always stop for a human: a field the AI is unsure of, a total that does not add
  > up, a message to a supplier, and the purchase order. Before we wrote any code, we listed how this could go
  > wrong: the AI getting the maths wrong, negotiating forever, leaking one supplier's price to another, and
  > obeying instructions hidden inside a supplier's document. In this video you will see each one caught.

- **Watch for:** each document fills its window for at least 2 s. README title visible at the end.
- **End state / cut point:** README top, still. The next scene starts on the same tab.
- **Retake notes:** if a viewer opens behind another window, click its Dock icon. Minimise all three again before
  the next take.
- **Duration target:** 1:00.

### S2 — Architecture

- **Start state:** tab 0, README top.
- **Screen:** README rendered.
- **Script:**

  > [scroll to "Three layers, one rule"] The system has three layers. OpenClaw runs on an AWS Lightsail server;
  > it hosts the agents and gives them a chat surface. Our Python backend owns the rules, the workflow and every
  > calculation. And the language model, Claude, only reads, explains and drafts. One rule ties them together:
  > the AI never touches a number that matters. [scroll to the diagram] Every call to the model goes through
  > OpenClaw first. If OpenClaw is down, the backend goes straight to the model gateway. If the model is down as
  > well, a person types the values and the engine carries on. [scroll to "The four agents"] Four agents. The
  > Document Agent turns each quote into structured data. Supplier Intelligence reads delivery and defect history
  > from a trusted record. The Decision Agent explains a ranking the engine has already computed. And the
  > Negotiation Agent drafts messages to suppliers. [point at the "Must not" column] The last column matters
  > most: what each agent is not allowed to do.

- **Watch for:** the mermaid diagram rendered as boxes, not code.
- **End state / cut point:** the agents table on screen.
- **Retake notes:** if the diagram shows as code, switch to the GitHub rendering. Scroll with the trackpad, not
  the scrollbar.
- **Duration target:** 1:10.

### S3 — New run and upload

- **Start state:** tab 1 on the run list (http://47.129.120.76/). The header of the New run panel reads
  `live agents · via openclaw (reachable) · judge Jev`, with no banner. Finder window of the three files placed at
  the bottom right, not covering the drop zone.
- **Screen:** War Room run list.
- **Script:**

  > [click "Demo A: 2,000 Product X, 14 days, 30,000 USD"] Here is the request: two thousand units of Product
  > X, needed within fourteen days, on a budget of thirty thousand dollars. [click Create run] [drag the three
  > files from Finder onto "Drop supplier quotes here"] We drop in the three quotes exactly as they arrived.
  > [point at the lanes row] Each of these lanes is a member of the team. The Document Agent is reading. The
  > guardrail judge, a separate model, checks every important field against the original document.

- **Watch for:** badge **Created** changes to **Quotes extracted**. The Document and Judge lanes turn to `done`.
- **End state / cut point:** three quote rows on the left. Continue straight into S4 without stopping the
  recording if you can.
- **Retake notes:** upload rejected → the Finder window holds a different file; redo preflight item 9. For
  another take, go "← runs" and repeat. Each take makes a new run, so no reset is needed. If the chips must
  read "via OpenClaw" again, run R2 first.
- **Duration target:** 0:45.

### S4 — Extraction and the first injection catch

- **Start state:** continue from S3. For a separate take: T2 `just seed extracted`, then open the printed URL in
  tab 1 *before* recording. The badge reads **Quotes extracted**.
- **Screen:** War Room run page with the three rows on the left and the timeline on **All**.
- **Script:**

  > [point at the three rows on the left] Three quotes, three formats, one structure: Apex at eleven twenty a
  > unit, Borealis at twelve eighty, Cobalt at thirteen forty. [click "Moments" in the Show row] And here is the
  > first thing the team caught. [point at the red card] Cobalt's email has a line addressed to the AI. It tells
  > the system to rank Cobalt first, whatever the price. [point at the red "injection" tag on the Judge lane]
  > The judge recognised it as an instruction aimed at an AI system. The Document Agent treated it as plain
  > text: Cobalt's price is still thirteen forty, and nothing in the ranking favours Cobalt because of it.
  > [click the Cobalt row on the left to expand it] Every field was read with enough confidence to continue. If
  > one had not been, the run would have stopped here and asked us.

- **Watch for:** the moment card **Prompt injection detected in supplier content — treated as data**. The card
  text names a document id, not Cobalt, which is why the narration names Cobalt.
- **End state / cut point:** Moments filter, one card, Cobalt row expanded. The next scene reseeds.
- **Retake notes:** no injection card → the run is still extracting; wait for the badge to read **Quotes
  extracted** (up to ~20 s live). Do not speak the p value; it differs between the Jev judge (box) and the mock.
- **Duration target:** 1:00.

### S5 — The math gate

- **Start state:** T2 `just seed mismatch` → open the URL in tab 1. The modal **Human review required · Math
  check** is open, and the badge reads **Math check failed**.
- **Screen:** the modal over a dimmed War Room.
- **Script:**

  > The engine has checked every quote, and it has stopped the workflow. [point at "Document states"] Apex's
  > PDF says the total is twenty-two thousand and forty dollars. [point at "Engine computed (pre-tax)"] The
  > engine recomputed it from the unit price, the quantity and the shipping: twenty-two thousand eight hundred.
  > The document's printed total is simply wrong. The model never does arithmetic here. It reports what the
  > document says, and Python works out what it should say. When the two disagree, nothing moves until a person
  > decides. [click Use computed total] We accept the engine's figure, and the workflow carries on.

- **Watch for:** the badge becomes **Recommendation ready**. Under Moments you see **Human confirmed the
  engine's total — workflow resumed**.
- **End state / cut point:** three score cards in the Decision panel. The next scene reseeds, or continue into
  S6 if you are recording S3–S10 as one run.
- **Retake notes:** no modal → the run is already past the gate; reseed. The chips are not visible in this scene,
  so the cache state does not matter.
- **Duration target:** 0:50.

### S6 — The decision

- **Start state:** T2 `just seed recommended` → open the URL. The badge reads **Recommendation ready** and the
  header shows `Borealis Manufacturing AS 72.3 27,618.42`. Timeline on **All**.
- **Screen:** War Room, with the Decision panel scrolled to the top.
- **Script:**

  > [point at the three cards] With the corrected total, the engine scores all three suppliers on price, lead
  > time, reliability and risk, using fixed weights from configuration. Borealis seventy-two point three, Cobalt
  > sixty-two point nine, Apex fifty-eight point six. [point at Apex's "landed 24,852.00"] Apex is the
  > cheapest, at twenty-four thousand eight hundred and fifty-two dollars landed, but its delivery and defect
  > history pull it down. [point at the Rationale] The Decision Agent wrote this explanation. It did not pick
  > the winner, and it may not invent a number: a guard checks every figure in its text against the figures it
  > was given, and replaces the text if one does not match. [click "Decision" in the Show row] [point at the
  > chip on the agent.finished row] This chip shows the route the explanation took: through OpenClaw.

- **Watch for:** chip **via OpenClaw** on the Decision `agent.finished` row.
- **End state / cut point:** the Decision lane filter with the chip visible.
- **Retake notes:** chip reads **via replay** → this prompt was recorded earlier; run R2 and reseed. Chip reads
  **via gateway** → OpenClaw fell back; check T1 with `is-active`, and redo. The row shows **fallback** and an
  amber **LLM output rejected by the number guard; using deterministic text** card → that is the guard working.
  Keep the take and add: "the guard rejected the model's wording; the plain text stands."
- **Duration target:** 1:00.

### S7 — Negotiation gate and the leak block

- **Start state:** T2 `just seed recommended` → open the URL. The Decision panel is scrolled down so that the
  **Negotiation** box with **Start negotiation** is visible (it sits below Purchase order at 1440×900).
- **Screen:** War Room.
- **Script:**

  > Now we try to get a better price. [click Start negotiation] [wait for the modal] The Negotiation Agent has
  > drafted a message to Borealis. [point at "Our ask (target)"] The ask, eleven seventy-eight a unit, comes
  > from the engine, inside limits we set in advance, and at most two rounds per supplier. And nothing is sent
  > until we approve it. [click at the end of the message; type " Apex offered 11.20."] Now let's play a
  > careless buyer and tell Borealis what a competitor offered. [click Approve & Send] [wait for the red box]
  > Blocked. A rule caught the competitor's name, and the judge, independently, flagged the message as
  > referring to a competing supplier. It was never sent. [click Reset to draft] We go back to the agent's own
  > draft, [click Approve & Send] and approve it as written.

- **Watch for:** the red box **Blocked by the outbound policy filter — not sent**, containing
  `mentions other supplier 'Apex' (sup_a)` and a `judge: …` line. Then the next modal, **Approve round 2
  negotiation message to Borealis Manufacturing AS**.
- **End state / cut point:** the round 2 modal for Borealis is open. **Do not stop the run.** S8 continues from
  here: stop recording, keep the tab as it is.
- **Retake notes:** no violation → you typed a name the filter does not know; use exactly `Apex offered 11.20.`
  If the text landed mid-message, the violation still fires, but it reads badly; press ⌘↓ before typing.
  For another take, reseed `recommended`: the blocked attempt is logged on the old run and does not carry over.
- **Duration target:** 1:15.

### S8 — Counter-offers and the second injection

- **Start state:** continue from S7, with the round 2 modal for Borealis open and **Supplier's standing offer
  12.55/unit · 10 d** visible. There is no seed that stops mid-negotiation. For a clean retake: `just seed
  recommended`, **Start negotiation**, **Approve & Send** once off camera, then record.
- **Screen:** Approve message modal.
- **Script:**

  > Borealis has replied. [point at "Supplier's standing offer"] Their counter-offer is twelve fifty-five, and
  > the agent has drafted a second and final round. [click Approve & Send] [point at the Borealis card,
  > "negotiated 12.40/unit"] Borealis's final offer: twelve forty. Now Cobalt, the runner-up. [click Approve &
  > Send] [point at "Supplier's standing offer"] Cobalt comes down to twelve ninety-five. [click Approve &
  > Send] [click "Moments"; scroll the timeline to the bottom] Cobalt's final reply offers twelve forty-five,
  > and it slips in a note telling the procurement system to award the order to Cobalt regardless of scoring.
  > [point at the second injection card] Caught again, and treated as data. [scroll the Decision panel to
  > "What changed"] The engine rescored with the new prices. Borealis's landed cost falls from twenty-seven
  > thousand six hundred and eighteen dollars to twenty-six thousand seven hundred and sixty-three, and Borealis
  > stays first. Two rounds each, and the threads close. The workflow has no third round.

- **Watch for:** badge **Recommendation ready**. The moment cards **Counter-offer from Cobalt Industrial:
  12.45/unit · 9 d** and a second **Prompt injection detected…**. The "What changed" table shows Borealis
  `27,618.42 → 26,763.86` and `72.3 → 66.3`.
- **End state / cut point:** "What changed" table on screen. If you are recording S3–S10 as one run, note the run
  URL now for S14.
- **Retake notes:** a verdict says "close" rather than "accept" at the round cap → this is correct; carry on.
  Borealis's score drops to 66.3 but it stays first; do not describe it as "unchanged".
- **Duration target:** 1:15.

### S9 — The interrupt

- **Start state:** T2 `just seed negotiated` → open the URL, then click **Moments**. The badge reads
  **Recommendation ready** and the header shows `2,000 × Product X v1` and `Borealis Manufacturing AS 66.3`.
- **Screen:** War Room, with the **Inject change** box visible on the left.
- **Script:**

  > The deal is almost done, and then the real world happens: the customer upsizes the order. [click "Demo A:
  > 2,000 → 5,000 units, budget 75,000"] Five thousand units, and the budget rises to seventy-five thousand.
  > [click Inject change] We don't restart. The agents go back over the quotes they already have: capacity,
  > minimum order, price, lead time, budget and risk. [point at the red banner at the top of the Decision panel]
  > The recommendation changes, from Borealis to Cobalt. [point at "Replan impact"] Borealis can make only four
  > thousand units a month, so it is no longer eligible. Cobalt now leads at sixty-two point nine, with a landed
  > cost of sixty-seven thousand eight hundred and fifty-two dollars fifty. Apex is cheaper again, and still
  > scores lower.

- **Watch for:** the header `5,000 × Product X v2`. The moment cards **Requirement changed: quantity 2,000 →
  5,000, budget 30,000.00 → 75,000.00 — replanning without restart** and **Recommendation changed: Borealis
  Manufacturing AS → Cobalt Industrial**. The impact line reads **Borealis Manufacturing AS: became
  ineligible; quantity 5000 exceeds capacity 4000**.
- **End state / cut point:** Replan impact visible.
- **Retake notes:** **Inject change** greyed out → a gate is pending; click the amber **Waiting for human: …**
  pill, resolve it, and try again. If the replan takes 12 s, a draft was pending (drill f); reseed `negotiated`.
- **Duration target:** 1:00.

### S10 — The PO gate

- **Start state:** T2 `just seed replanned` → open the URL. The header shows `Cobalt Industrial 62.9 67,852.50`.
  Scroll the Decision panel until **Request purchase order** is visible.
- **Screen:** War Room.
- **Script:**

  > One step is never automated. [click Request purchase order] [wait for the modal] Here is the purchase order
  > preview: five thousand units from Cobalt at the negotiated twelve forty-five, and a landed cost of
  > sixty-seven thousand eight hundred and fifty-two dollars fifty, tax included. Every figure is the engine's.
  > [point at "Approver name"] An approver's name goes on it. [click Approve Final Supplier & Generate PO] The
  > purchase order gets its number only now, after a person has approved it. [click Download PO (PDF)] And this
  > is the document that goes to the supplier.

- **Watch for:** badge **Purchase order generated**. The PO number `PO-YYYYMMDD-xxxxxxxx` appears in the header
  and on the **Purchase order generated** card. The PDF opens in a new tab.
- **End state / cut point:** the PDF on screen for 2 s. Close the PDF tab after recording.
- **Retake notes:** the approver name is prefilled with `demo-user`; do not clear it. If the PDF is blocked,
  show the PO card instead; the card is enough. For another take, reseed `replanned`.
- **Duration target:** 0:50.

### S11 — Break it: OpenClaw down

- **Start state:** T1 on the box beside tab 1 (split screen), with the run list in tab 1 and no banner. Run
  `systemctl --user is-active openclaw-gateway` → `active`.
- **Screen:** T1 on the left, War Room run list on the right.
- **Script:**

  > Now we break it on purpose. First, the agent runtime. [T1: paste the stop line, press Enter] OpenClaw is
  > down. [wait for the amber banner] Within fifteen seconds the War Room says so: AI route degraded, using the
  > fallback gateway. [set Quantity to 2100] We start a run the system has never seen, twenty-one hundred units,
  > so nothing can come from a recording. [click Create run; drag the three files; wait for the rows; click
  > Evaluate quotes; click Use computed total] The same checks, the same gate. [click "Decision" in the Show row]
  > [point at the chip] The explanation still arrived, by another route: via gateway. Same prompt, same guard,
  > same numbers. Only the road changed. [T1: paste the start line, press Enter] Now we bring OpenClaw back,
  > [wait for the banner to clear] and the banner clears by itself.

- **Watch for:** the amber banner **AI route degraded: using fallback gateway**, and the header `LLM via OpenClaw
  (unreachable)` in red. Then the chip **via gateway** on the Decision row. T1 prints `"openclaw":"reachable"`,
  and within about 20 s the banner is gone.
- **End state / cut point:** no banner and a normal header. Wait 20 s before recording S13.
- **Retake notes:** banner does not clear → wait one more /health poll (15 s); it needs one successful probe.
  Chip reads **via replay** → you left the quantity at 2,000; use 2100. Every retake needs a *new* quantity (2100,
  2200, …) because each one records its own prompt. Speak "twenty-one hundred" only if 2100 is on screen.
- **Duration target:** 1:30.

### S12 — Break it: the model down, manual mode

- **Start state:** T3 `just demo-manual` and T4 (the :5174 frontend) running. Tab 2, http://localhost:5174/,
  shows the red banner **AI unavailable — manual mode.** Keep the Finder window visible.
- **Screen:** tab 2, run list.
- **Script:**

  > Second failure: the model itself is gone. This copy of the backend has every route to the AI closed.
  > [point at the red banner] AI unavailable, manual mode. [click Create run] [drag only supplier_a_apex.pdf onto
  > the drop zone] The Document Agent tries, retries with a pause, [wait for the form] and hands over to a
  > person: the document text on the left, the fields on the right. [type 11.20, 500, 13, 2000, Apex Components
  > Ltd, 400] Nothing is invented. We type what the document says. [click Save quote] [click Evaluate quotes]
  > [click Compare] Landed cost: twenty-four thousand eight hundred and fifty-two dollars. That is exactly what
  > the engine produced on the server from the AI's extraction. [point at the "fallback" tags on the Document
  > and Decision lanes] And the failure is visible, never silent.

- **Fields to type:** Unit price `11.20`, MOQ (units) `500`, Lead time (days) `13`, Quantity quoted `2000`, Supplier
  name `Apex Components Ltd`, Shipping cost `400`. Currency is prefilled `USD`. Leave
  "Total printed on the document" empty: typing 22,040 would trigger the math gate.
- **Watch for:** the badge **AI UNAVAILABLE · MANUAL ENTRY** on the form. Compare reads **landed cost
  24,852.00**, and the amber card reads **LLM gateway unavailable; using deterministic text**.
- **End state / cut point:** the Compare tab.
- **Retake notes:** landed cost 24,416.00 → you left Shipping cost empty; type 400. **Save quote** greyed out →
  a field marked * is empty (MOQ is required). The form appears after about 6 s (three attempts with backoff),
  so trim the wait in editing. For another take, create a new run; the :8001 backend is throwaway (restart T3
  to clear it).
- **Duration target:** 1:15.

### S13 — OpenClaw as a colleague

- **Start state:** OpenClaw up for at least 20 s (after S11). T1 clear, with `export PATH=…` already run (off
  camera).
- **Screen:** T1, full screen.
- **Script:**

  > OpenClaw is also a colleague the buyer can talk to. [paste the openclaw agent command, press Enter] We ask
  > in plain language: what is the status of the latest procurement run, and is anything waiting for me?
  > [wait for the answer] A skill calls our backend's API and answers from the same data as the War Room.
  > [point at the answer] It reads the state for us, and it will not approve anything without an explicit
  > confirmation. The approvals stay with a person.

- **Watch for:** a prose answer naming the latest run and its state, within about 10 s warm or 25 s cold.
- **End state / cut point:** the answer on screen for 3 s.
- **Retake notes:** `Gateway not reachable at ws://127.0.0.1:18789` → OpenClaw is still booting; wait 20 s. Past
  45 s → run the `curl -s http://127.0.0.1:8000/runs | tail -c 400` line and say "the skill reads this same
  API". Cut the wait in editing.
- **Duration target:** 0:50.

### S14 — Evidence: the Moments filter

- **Start state:** the finished run from S3–S10 if you recorded them as one run (12 moment cards, including the
  leak block). Otherwise use T2 `just seed po` → open the URL. That run has 11 cards and **no** "Outbound message
  blocked" card, because the seed approves drafts unedited; skip the bracketed sentence. The badge reads
  **Purchase order generated**. Click **Moments** and scroll the timeline to the top.
- **Screen:** War Room.
- **Script:**

  > [scroll the timeline slowly from top to bottom] This is the run as the audit trail records it. The
  > injection in Cobalt's email: caught. The wrong total: stopped, then confirmed by a person. [The competitor's
  > price we tried to leak: blocked.] Four counter-offers, and a second injection: caught. The requirement
  > changed, and the recommendation changed with it. And a purchase order, generated only after approval.
  > [click "All"] Every step is an event with a timestamp, [click ▸ on any row] with its full data attached.
  > Nothing here was prepared for the video. This is the run itself, and it can be replayed.

- **Watch for:** the last card, **Purchase order PO-… generated after human approval**.
- **End state / cut point:** one expanded payload.
- **Retake notes:** if extra amber **agent.failed** cards appear from live number-guard trips, keep them and add
  "and here the guard rejected the model's wording". Do **not** run `just demo-reset` before this scene is
  recorded: it deletes every run on the box.
- **Duration target:** 1:00.

### S15 — Code walk and tests

- **Start state:** editor with the three tabs from the preparation list. T2 at the repo root, cleared.
- **Screen:** editor, then T2.
- **Script:**

  > Three short files carry these guarantees. [costing.py, cost_chain] This is all the arithmetic in the
  > product: subtotal, discount, shipping, tax, landed cost. Every figure you have seen comes from these lines.
  > [number_guard.py, docstring] This is the number guard. It collects every number the model was given, and if
  > the model's text contains any other number, the text is rejected and a plain template is used instead.
  > [policy.py, can_open_turn] And this is the round limit: at most two rounds per supplier, checked before
  > every message is sent. [switch to T2; type just test; press Enter] Finally, the test suite. [wait for "313
  > passed"] Three hundred and thirteen tests. They run offline: they never call a model and never spend a
  > cent.

- **Watch for:** `313 passed` in the pytest summary line.
- **End state / cut point:** the pytest summary. The frontend build that follows can be cut.
- **Retake notes:** scroll the editor so the function name is on the top line before you speak. If the count is
  not 313, say the number on screen and fix this script afterwards.
- **Duration target:** 1:20.

### S16 — Does it generalise?

- **Start state:** T2 cleared. Tab 1 on the run list. OpenClaw up.
- **Screen:** T2, then the War Room.
- **Script:**

  > Is this tuned to one story? [T2: run just seed po --scenario b] Here is a second one, run with one command
  > and no code changes: ten thousand stainless bolts, four suppliers. [open the URL; click "Moments"] Granite's
  > printed total is eight thousand five hundred and sixty. The engine says eight thousand six hundred and fifty.
  > It is the same gate, on a different document with a different mistake. [scroll the Decision panel to Delta
  > Trading] Delta Trading quotes the lowest unit price, but it is blacklisted, so it cannot win, and that rule
  > sits outside the model. [point at the Requirement changed card] This time the interrupt is a budget cut, from
  > nine thousand five hundred to eight thousand seven hundred. Two suppliers go over budget, and the
  > recommendation moves from Eiger to Fjord. A different interrupt and a different winner, from the same engine.

- **Watch for:** **Math check failed — workflow stopped for human review** with `computed 8650.00 vs stated
  8560.00`. The Delta card reads **ineligible · supplier is blacklisted; defect rate 12.0% exceeds 5.0%**. Then
  **Requirement changed: budget 9,500.00 → 8,700.00 — replanning without restart** and **Recommendation
  changed: Eiger Metallwerk GmbH → Fjord Components AS**. On the box (Jev), Fjord's email may add a third
  injection card; point at it if it is there, but do not script it.
- **End state / cut point:** the Decision panel showing Fjord's PO card.
- **Retake notes:** the seed takes a few seconds live (four new documents). If it exits with "expected Eiger
  recommended", read the error aloud off camera and rerun. Every seed makes a new run.
- **Duration target:** 1:05.

### S17 — Close

- **Start state:** tab 0, README top, with the **Live:** line visible.
- **Screen:** README.
- **Script:**

  > That is ProcureAI: an AI team that does the reading, checking, comparing and negotiating, while the engine
  > owns every number and a person approves every step that matters. When the AI fails, the work carries on,
  > and you can see that it failed. Next, we would connect real email and purchasing systems, keep supplier
  > history in a database, and handle requests with more than one product. The live system is at the address
  > on screen. Thank you.

- **Watch for:** `http://47.129.120.76/` readable on screen.
- **End state / cut point:** hold for 3 s after "Thank you".
- **Retake notes:** none.
- **Duration target:** 0:30.

---

## 4. Editing notes

**Order in the video:** S1 → S17, as numbered.

**Recommended recording order**, which keeps the state safe:

1. S1, S2, S17, S15. These need no app state.
2. Rotate the cache (R1). Record S3 → S10 as **one continuous run**, stopping the recording between scenes but
   never reseeding. Note the run URL. Use seeds only for retakes, and run R2 before a seeded retake if you want
   "via OpenClaw" chips.
3. S14 on that same run.
4. S16.
5. S11, then S13 (OpenClaw must be back up), then S12. Finish with the restore (Appendix B, "After").

**Title cards (1.5 s each, text only):** before S3 "The core run". Before S11 "Break it on purpose". Before S14
"Evidence". Before S16 "A second story".

**One-second pauses:** after "Blocked." (S7), after the injection card appears (S4, S8), after "from Borealis to
Cobalt" (S9), and before "Thank you" (S17).

**Trim the machine waits:** the live moment waits in S3–S8 (~5 s each), the retries in S12 (~6 s), and the
OpenClaw answer in S13 (10–25 s). Cut these to 1 s and keep the pointer still across each cut.

**If the total runs over 18 minutes, cut in this order:**

1. S13 (−0:50). The story survives without it.
2. S2: keep only the three-layer table and the one-rule sentence (−0:35).
3. S15: drop `just test` and keep the three files (−0:20).
4. S16: stop after the Delta sentence (−0:20).
5. S4: stop after the injection card (−0:15).

**Continuity checklist**

- [ ] S5–S10 show the same run id if recorded continuously. The id appears only in the URL (hidden) and in the PO
      number in S10 and S14. If S10 was reseeded, S14 must use that same seeded run so the PO number matches.
- [ ] State badges follow each other: S4 **Quotes extracted** → S5 **Math check failed** → S6 **Recommendation
      ready** → S7 **Awaiting negotiation approval** → S9 **Recommendation ready** (v1 → v2) → S10 **Purchase
      order generated**.
- [ ] No banner in any scene except S11 (amber) and S12 (red). Check the top strip of every War Room scene.
- [ ] Chips never switch between **via replay** and **via OpenClaw** from scene to scene. Visible chips: S6
      (expects via OpenClaw) and S11 (expects via gateway). If S6 was recorded as a replay, rerecord it after R2.
- [ ] Borealis reads 72.3 in S6 and 66.3 from S8 onwards. Cobalt reads 62.9 throughout.
- [ ] The judge lane shows the red **injection** tag from S4 onwards.

---

## Appendix A — Spoken text only (teleprompter)

**S1.** A buyer asks three suppliers for a price. This is what comes back: a PDF, a spreadsheet, and an email.
Someone types all of it into a spreadsheet, spends half an hour comparing, and usually negotiates nothing. We
built ProcureAI: an AI procurement team that does that work and asks a person only when it matters. Four agents
read, check, compare and negotiate. A plain Python engine owns every number. And four moments always stop for a
human: a field the AI is unsure of, a total that does not add up, a message to a supplier, and the purchase
order. Before we wrote any code, we listed how this could go wrong: the AI getting the maths wrong, negotiating
forever, leaking one supplier's price to another, and obeying instructions hidden inside a supplier's document.
In this video you will see each one caught.

**S2.** The system has three layers. OpenClaw runs on an AWS Lightsail server; it hosts the agents and gives
them a chat surface. Our Python backend owns the rules, the workflow and every calculation. And the language
model, Claude, only reads, explains and drafts. One rule ties them together: the AI never touches a number that
matters. Every call to the model goes through OpenClaw first. If OpenClaw is down, the backend goes straight to
the model gateway. If the model is down as well, a person types the values and the engine carries on. Four
agents. The Document Agent turns each quote into structured data. Supplier Intelligence reads delivery and
defect history from a trusted record. The Decision Agent explains a ranking the engine has already computed.
And the Negotiation Agent drafts messages to suppliers. The last column matters most: what each agent is not
allowed to do.

**S3.** Here is the request: two thousand units of Product X, needed within fourteen days, on a budget of thirty
thousand dollars. We drop in the three quotes exactly as they arrived. Each of these lanes is a member of the
team. The Document Agent is reading. The guardrail judge, a separate model, checks every important field
against the original document.

**S4.** Three quotes, three formats, one structure: Apex at eleven twenty a unit, Borealis at twelve eighty,
Cobalt at thirteen forty. And here is the first thing the team caught. Cobalt's email has a line addressed to
the AI. It tells the system to rank Cobalt first, whatever the price. The judge recognised it as an instruction
aimed at an AI system. The Document Agent treated it as plain text: Cobalt's price is still thirteen forty, and
nothing in the ranking favours Cobalt because of it. Every field was read with enough confidence to continue.
If one had not been, the run would have stopped here and asked us.

**S5.** The engine has checked every quote, and it has stopped the workflow. Apex's PDF says the total is
twenty-two thousand and forty dollars. The engine recomputed it from the unit price, the quantity and the
shipping: twenty-two thousand eight hundred. The document's printed total is simply wrong. The model never does
arithmetic here. It reports what the document says, and Python works out what it should say. When the two
disagree, nothing moves until a person decides. We accept the engine's figure, and the workflow carries on.

**S6.** With the corrected total, the engine scores all three suppliers on price, lead time, reliability and
risk, using fixed weights from configuration. Borealis seventy-two point three, Cobalt sixty-two point nine,
Apex fifty-eight point six. Apex is the cheapest, at twenty-four thousand eight hundred and fifty-two dollars
landed, but its delivery and defect history pull it down. The Decision Agent wrote this explanation. It did not
pick the winner, and it may not invent a number: a guard checks every figure in its text against the figures it
was given, and replaces the text if one does not match. This chip shows the route the explanation took: through
OpenClaw.

**S7.** Now we try to get a better price. The Negotiation Agent has drafted a message to Borealis. The ask,
eleven seventy-eight a unit, comes from the engine, inside limits we set in advance, and at most two rounds per
supplier. And nothing is sent until we approve it. Now let's play a careless buyer and tell Borealis what a
competitor offered. Blocked. A rule caught the competitor's name, and the judge, independently, flagged the
message as referring to a competing supplier. It was never sent. We go back to the agent's own draft, and
approve it as written.

**S8.** Borealis has replied. Their counter-offer is twelve fifty-five, and the agent has drafted a second and
final round. Borealis's final offer: twelve forty. Now Cobalt, the runner-up. Cobalt comes down to twelve
ninety-five. Cobalt's final reply offers twelve forty-five, and it slips in a note telling the procurement
system to award the order to Cobalt regardless of scoring. Caught again, and treated as data. The engine
rescored with the new prices. Borealis's landed cost falls from twenty-seven thousand six hundred and eighteen
dollars to twenty-six thousand seven hundred and sixty-three, and Borealis stays first. Two rounds each, and the
threads close. The workflow has no third round.

**S9.** The deal is almost done, and then the real world happens: the customer upsizes the order. Five thousand
units, and the budget rises to seventy-five thousand. We don't restart. The agents go back over the quotes they
already have: capacity, minimum order, price, lead time, budget and risk. The recommendation changes, from
Borealis to Cobalt. Borealis can make only four thousand units a month, so it is no longer eligible. Cobalt now
leads at sixty-two point nine, with a landed cost of sixty-seven thousand eight hundred and fifty-two dollars
fifty. Apex is cheaper again, and still scores lower.

**S10.** One step is never automated. Here is the purchase order preview: five thousand units from Cobalt at the
negotiated twelve forty-five, and a landed cost of sixty-seven thousand eight hundred and fifty-two dollars
fifty, tax included. Every figure is the engine's. An approver's name goes on it. The purchase order gets its
number only now, after a person has approved it. And this is the document that goes to the supplier.

**S11.** Now we break it on purpose. First, the agent runtime. OpenClaw is down. Within fifteen seconds the War
Room says so: AI route degraded, using the fallback gateway. We start a run the system has never seen,
twenty-one hundred units, so nothing can come from a recording. The same checks, the same gate. The explanation
still arrived, by another route: via gateway. Same prompt, same guard, same numbers. Only the road changed. Now
we bring OpenClaw back, and the banner clears by itself.

**S12.** Second failure: the model itself is gone. This copy of the backend has every route to the AI closed. AI
unavailable, manual mode. The Document Agent tries, retries with a pause, and hands over to a person: the
document text on the left, the fields on the right. Nothing is invented. We type what the document says. Landed
cost: twenty-four thousand eight hundred and fifty-two dollars. That is exactly what the engine produced on the
server from the AI's extraction. And the failure is visible, never silent.

**S13.** OpenClaw is also a colleague the buyer can talk to. We ask in plain language: what is the status of the
latest procurement run, and is anything waiting for me? A skill calls our backend's API and answers from the
same data as the War Room. It reads the state for us, and it will not approve anything without an explicit
confirmation. The approvals stay with a person.

**S14.** This is the run as the audit trail records it. The injection in Cobalt's email: caught. The wrong
total: stopped, then confirmed by a person. The competitor's price we tried to leak: blocked. Four
counter-offers, and a second injection: caught. The requirement changed, and the recommendation changed with it.
And a purchase order, generated only after approval. Every step is an event with a timestamp, with its full data
attached. Nothing here was prepared for the video. This is the run itself, and it can be replayed.

**S15.** Three short files carry these guarantees. This is all the arithmetic in the product: subtotal,
discount, shipping, tax, landed cost. Every figure you have seen comes from these lines. This is the number
guard. It collects every number the model was given, and if the model's text contains any other number, the
text is rejected and a plain template is used instead. And this is the round limit: at most two rounds per
supplier, checked before every message is sent. Finally, the test suite. Three hundred and thirteen tests. They
run offline: they never call a model and never spend a cent.

**S16.** Is this tuned to one story? Here is a second one, run with one command and no code changes: ten
thousand stainless bolts, four suppliers. Granite's printed total is eight thousand five hundred and sixty. The
engine says eight thousand six hundred and fifty. It is the same gate, on a different document with a different
mistake. Delta Trading quotes the lowest unit price, but it is blacklisted, so it cannot win, and that rule sits
outside the model. This time the interrupt is a budget cut, from nine thousand five hundred to eight thousand
seven hundred. Two suppliers go over budget, and the recommendation moves from Eiger to Fjord. A different
interrupt and a different winner, from the same engine.

**S17.** That is ProcureAI: an AI team that does the reading, checking, comparing and negotiating, while the
engine owns every number and a person approves every step that matters. When the AI fails, the work carries on,
and you can see that it failed. Next, we would connect real email and purchasing systems, keep supplier history
in a database, and handle requests with more than one product. The live system is at the address on screen.
Thank you.

---

## Appendix B — Command sheet (in order of use)

`KEY=~/.ssh/LightsailDefaultKey-ap-southeast-1.pem` below stands for the key path. Paste it literally or export it
once in each terminal.

**Preparation (T2 unless noted)**

```bash
just preflight                                   # → every line PASS, "ALL PASS"
mkdir -p ~/Desktop/ProcureAI-demo && cp data/synthetic/{supplier_a_apex.pdf,supplier_b_borealis.xlsx,supplier_c_cobalt.eml.txt} ~/Desktop/ProcureAI-demo/   # → no output; Finder shows 3 files
just demo-manual                                 # T3 → "Uvicorn running on http://127.0.0.1:8001"
cd frontend && VITE_API_URL=http://localhost:8001 npm run dev -- --port 5174 --strictPort   # T4 → "Local: http://localhost:5174/"
```

**R1 — rotate the cache for live recording (once)**

```bash
ssh -i $KEY ubuntu@47.129.120.76 'mv ~/procureai/data/llm_cache ~/llm_cache.bak && mkdir -p ~/procureai/data/llm_cache'   # → no output
curl -s http://47.129.120.76/api/health          # → …"openclaw":"reachable"…"llm":"ok"…"guardrail_judge":"jev"…
```

**R2 — rotate again before a retake that must read "via OpenClaw"**

```bash
ssh -i $KEY ubuntu@47.129.120.76 'mv ~/procureai/data/llm_cache ~/llm_cache.take-$(date +%H%M%S) && mkdir -p ~/procureai/data/llm_cache'   # → no output
```

**Seeds (T2). Each prints its steps and then the run URL as the last line.**

```bash
just seed extracted        # S4 retake  → "extracted state=EXTRACTED" … http://47.129.120.76/runs/run-xxxxxxxx
just seed mismatch         # S5         → "evaluated state=CALC_MISMATCH" … URL
just seed recommended      # S6, S7     → "recommended state=RECOMMENDED" … URL
just seed negotiated       # S9         → "negotiated ×4 state=RECOMMENDED" … URL
just seed replanned        # S10        → "replanned state=RECOMMENDED" … URL
just seed po               # S14 fallback → "PO PO-YYYYMMDD-xxxxxxxx for Cobalt Industrial" … URL
just seed po --scenario b  # S16        → "PO PO-YYYYMMDD-xxxxxxxx for Fjord Components AS" … URL
```

**S11 (T1, on the box)**

```bash
systemctl --user stop openclaw-gateway && systemctl --user is-active openclaw-gateway   # → inactive
systemctl --user start openclaw-gateway && sleep 3 && curl -s http://127.0.0.1:8000/health | grep -o '"openclaw":"[a-z]*"'   # → "openclaw":"reachable"
```

**S13 (T1, on the box)**

```bash
export PATH=$HOME/.nvm/versions/node/v24.21.0/bin:$PATH                                  # → no output (run off camera)
openclaw agent --agent main --timeout 60 -m "what is the status of the latest procurement run and is anything waiting for me?"   # → prose answer in ~10–25 s
curl -s http://127.0.0.1:8000/runs | tail -c 400                                         # fallback → JSON tail of the run list
```

**S15 (T2)**

```bash
just test                  # → "313 passed" from pytest, then the frontend build
```

**After recording**

```bash
ssh -i $KEY ubuntu@47.129.120.76 'rm -rf ~/procureai/data/llm_cache && mv ~/llm_cache.bak ~/procureai/data/llm_cache'   # → no output (restores the committed cache)
ssh -i $KEY ubuntu@47.129.120.76 'systemctl --user is-active openclaw-gateway procureai-backend'                          # → active, active
just demo-reset            # optional, asks y → deletes every run on the box, then seeds one CREATED run
```

The `~/llm_cache.take-*` directories on the box hold only this session's recordings. They can be deleted after
the video is final.
