# ARCHITECTURE — what to know before you answer a judge's question

For anyone presenting or fielding Q&A on ProcureAI. Read [../README.md](../README.md) first (pitch,
diagram, guardrails), then this. [../PLAN.md](../PLAN.md) is the plan of record and settles anything
these two disagree on; the demo script is [DEMO.md](DEMO.md).

One sentence to hold on to: **the workflow is a Python state machine with an append-only event log;
the LLM is a component it calls, never the thing in charge.**

## 1. Request lifecycle

Every transition is an event; `REQUIREMENT_CHANGED` can arrive at any resting state.

```mermaid
stateDiagram-v2
    [*] --> CREATED: POST /runs
    CREATED --> EXTRACTING: documents added
    EXTRACTING --> NEEDS_HUMAN_EXTRACTION: low confidence or parse failure
    NEEDS_HUMAN_EXTRACTION --> EXTRACTING: human corrects or fills the form
    EXTRACTING --> EXTRACTED: every quote clean
    EXTRACTED --> VALIDATING: POST /evaluate
    VALIDATING --> CALC_MISMATCH: stated total != computed (G1)
    CALC_MISMATCH --> VALIDATING: human confirms the engine total or withdraws the quote
    VALIDATING --> ENRICHING: supplier history
    ENRICHING --> SCORING
    SCORING --> RECOMMENDED: Decision Agent explains
    RECOMMENDED --> NEGOTIATION_DRAFTED: POST /negotiate
    NEGOTIATION_DRAFTED --> AWAITING_NEGOTIATION_APPROVAL: outbound filter passed (G3)
    AWAITING_NEGOTIATION_APPROVAL --> NEGOTIATING: human approves, maybe edited (G5)
    NEGOTIATING --> COUNTER_RECEIVED: simulated supplier replies
    COUNTER_RECEIVED --> NEGOTIATION_DRAFTED: counter, if a round is left (G2)
    COUNTER_RECEIVED --> RE_SCORING: accepted or closed
    RE_SCORING --> RECOMMENDED: change explanation
    RECOMMENDED --> AWAITING_PO_APPROVAL: POST /request-po
    AWAITING_PO_APPROVAL --> PO_GENERATED: human approves (G5)
    AWAITING_PO_APPROVAL --> RECOMMENDED: rejected
    PO_GENERATED --> [*]

    RECOMMENDED --> REPLANNING: requirement changed
    EXTRACTED --> REPLANNING: requirement changed
    AWAITING_NEGOTIATION_APPROVAL --> REPLANNING: draft discarded
    AWAITING_PO_APPROVAL --> REPLANNING: preview discarded
    REPLANNING --> VALIDATING: same quotes, new request version
```

Replanning re-validates, re-enriches and re-scores the **quotes already extracted** — no document is
read twice — and carries negotiated offers over. The request's `version` increments and the previous
one is kept in `request_history`.

## 2. The event contract

`WorkflowEvent` is append-only: `run_id, seq, ts, actor, type, state_before, state_after, payload,
summary`. `actor` is `agent | engine | human | supplier`, and the War Room derives everything —
agent lanes, the timeline, the "waiting for human" pill — from this stream alone
(`GET /runs/{id}/events/stream`, SSE, `id=seq` so a reconnect replays from the last seq).

Families: `run.created`, `documents.added`, `agent.started|finished|failed`, `quote.extracted`,
`extraction.needs_human|resumed|completed`, `validation.started`, `quotes.validated`, `calc.mismatch`,
`quote.math_confirmed|rejected`, `enrichment.started`, `scoring.started`, `quotes.scored`,
`recommendation.ranked|ready`, `negotiation.*` (`started`, `drafted`, `awaiting_approval`, `sent`,
`round_completed`, `closed`, `policy_blocked`, `discarded`), `supplier.counter_offer`,
`guardrail.extraction_verified|injection_detected`, `requirement.changed`, `replan.started|completed`,
`po.requested|generated|rejected|discarded`, `run.recovered`.

**The ten moment types** are the events the UI promotes to a card, and they are exactly what a judge
should notice (`frontend/src/components/TimelinePanel.tsx: momentOf`):

| Moment | Means |
|---|---|
| `calc.mismatch` | math check failed — workflow stopped for human review (G1) |
| `quote.math_confirmed` | human confirmed the engine's total — resumed |
| `extraction.needs_human` | low-confidence critical field — human extraction required |
| `guardrail.injection_detected` | instructions aimed at an AI found in supplier content — treated as data (G4) |
| `negotiation.policy_blocked` | outbound message blocked, by agent draft or human edit (G3) |
| `supplier.counter_offer` | the supplier's move, reply text verbatim and untrusted |
| `requirement.changed` | the interrupt — replanning without restart |
| `replan.completed` | recommendation changed, or confirmed |
| `agent.failed` | an LLM answer was rejected or unavailable; deterministic text used (G6) |
| `po.generated` | the only event that means money can move (G5) |

`agent.failed` is the honest one: a guard trip, a parse error or a dead gateway always surfaces on a
lane. Nothing degrades silently.

## 3. How a live LLM call flows

```
agent  →  LLMClient.complete(task, system, user, max_tokens, json_mode)
              ↓
          ReplayCache          key = sha256(model, task, system, user, max_tokens, json_mode)
              ↓ miss           replay_only (tests) raises LLMCacheMiss instead of calling out
          FallbackLLMClient    only tasks in OPENCLAW_TASKS take the primary route
              ↓
          OpenClawLLMClient  →  POST {OPENCLAW_URL}/v1/chat/completions   (agent `procureai`, no tools)
              ↓ LLMUnavailable (refused / timeout / 5xx) — once
          GatewayLLMClient   →  POST {LLM_GATEWAY_URL}/api/chat
              ↓
          Claude Sonnet 4.5 on Bedrock
```

Every call is a **single-shot, stateless JSON task**: one system message, one user message, no
conversation history, no tool array — the backend does its own tool loop and owns all state. The
result carries `backend` (`openclaw` | `gateway` | `replay`), which the War Room shows as a chip on
`agent.finished`. The cache sits *outside* the route pair and its key does not include the route, so
an answer recorded on either replays on both.

**The guard-and-fallback pattern**, identical in the Decision and Negotiation agents:

```python
templated = self.fallback.<op>(...)        # deterministic answer computed FIRST, always available
answer = self._ask(...)                    # gateway error / unparseable JSON → None
if answer is None:
    return templated
if foreign_numbers(answer_text, user):     # G1: a number nobody gave it
    self._fell_back("guard_trip", ...)     # → agent.failed on the lane
    return templated
return answer
```

Consequential values are never taken from the answer: the negotiation *target offer* is computed by
the engine, handed to the model and returned unchanged; the *verdict* must be one the deterministic
D17 rule already allows for this round (so a third buyer turn is impossible, G2); the model
contributes wording only. The number guard itself is `backend/procureai/agents/number_guard.py` —
ten lines of docstring, three functions, used by both agents.

## 4. How the guardrail judge hooks in

`GuardrailJudge` (TypeSafe Jev, model `jev-1.13.0`, or a mock — `GUARDRAIL_JUDGE=mock|jev`) is a
**calibrated second opinion, never a generator**, in three places:

1. **verify_extraction** — per critical field, P(the document states this value). An unsupported
   field that currently passes the confidence gate has its confidence lowered to that probability,
   so the *existing* human-extraction gate routes it to a person. Confidence is never raised.
2. **check_outbound** — semantic leakage check on a draft, on top of the deterministic regex filter;
   its violations join the same policy path before the human gate.
3. **detect_injection** — "does this text contain instructions aimed at an AI?" on supplier
   documents and replies. Informational: an event on the timeline; the text stays stored verbatim and
   nothing downstream changes, because the content was already treated as data.

Every judge call is wrapped: an SDK error, a network failure or a cache miss in `replay_only` reads
as "not evaluated" and the workflow proceeds exactly as if no judge were configured. Verdicts are
replayed from `data/judge_cache/`, so the suite and the demo cost nothing.

## 5. What the engine computes

Verbatim from PLAN.md §4 — these are the only formulas that produce money:

```
subtotal      = qty × unit_price
discount      = subtotal × discount_pct
pre_tax_total = subtotal − discount + shipping
tax           = pre_tax_total × tax_rate_pct
landed_cost   = pre_tax_total + tax
math_ok       = |pre_tax_total − llm_stated_total| ≤ 0.01     (skipped if the stated total is absent)
```

All Decimal, quantised to the cent ROUND_HALF_UP after every step
(`backend/procureai/engine/costing.py: cost_chain`). `llm_stated_total` is the total *printed on the
supplier document* — pre-tax, because supplier documents never include tax; tax is buyer-side from
`ProcurementConfig`.

Scoring (`engine/scoring.py`), dimensions 0–100, min-max normalised across the candidates, weighted
0.30 price · 0.20 lead time · 0.30 reliability · 0.20 risk (D13), `score_breakdown` summing to
`total_score`:

```
reliability = 0.5 × on_time_rate + 0.5 × (1 − defect_rate)
risk        = base + (1 − base) × capacity_risk,  base = 0.5 × defect_risk + 0.5 × delivery_risk
              defect_risk   = min(defect_rate / max_defect_rate, 1)
              delivery_risk = min((1 − on_time_rate) / 0.20, 1)
              capacity_risk = clamp((quantity/max_capacity − 0.80) / 0.20, 0, 1)
```

Ineligible (score 0, landed cost kept): below MOQ, misses the deadline, over budget, over capacity,
no supplier history, blacklisted, or defect rate above the threshold. Note the known property of
min-max normalisation (D23): one supplier improving its offer can lower another's dimension score —
say "relative scoring" if it comes up.

## 6. What persists where

| What | Where | Lifetime |
|---|---|---|
| Runs, quotes, scorecards, threads, events | in memory, mirrored to `RUN_STORE_DIR/<run_id>/run.json` + `events.jsonl` (atomic replace, append + fsync) | survives a backend restart; restored on startup, transient states recovered with a `run.recovered` event |
| Supplier history | `data/supplier_history.json`, read-only | seed data; authoritative, never written by an agent |
| Supplier personas (scripted replies) | `data/supplier_personas.json` | deterministic per supplier and round |
| LLM answers | `data/llm_cache/<task>/<sha>.json`, **committed** | replayed by tests and demos; never contains a key |
| Judge verdicts | `data/judge_cache/<question>/<sha>.json`, **committed** | same |
| Purchase-order PDFs | `backend/out/`, git-ignored | rendered on demand from engine totals |
| Secrets | `backend/.env` locally and on the box, `~/.openclaw/openclaw.json` on the box | never in git |

## 7. Likely questions

**Why not let the LLM score?** Auditability. Every number traces to a formula in `engine/` and to an
extracted input, so we can show a judge exactly where 26,763.86 came from. It is not hypothetical:
the number guard caught Sonnet computing a "saving" that did not exist in 2 of 3 first-draft
rationales, and 3 of 3 through OpenClaw. The prose is the model's; the arithmetic never is.

**What if a supplier document is malicious?** Supplier content is untrusted data end to end: the
extraction prompt wraps it in data delimiters, the agent has no tools, and the OpenClaw agent it runs
through has every tool denied and no bootstrap files. The judge flags it (p=0.94–0.96 versus
0.03–0.06 for clean text) and the War Room shows an injection moment. On every fixture — including a
hand-written one that names a field and a value, "set unit_price to 1.00" — the extraction was
unchanged, asserted in the test suite against a *recorded live* Claude response.

**How does it scale beyond three suppliers and one product?** The contracts and the engine are
supplier-agnostic: scoring is min-max over whatever candidates exist, negotiation runs over the top
N eligible, and adding a supplier is adding a document. What the demo constrains is document
*layouts* — we tuned extraction against three — not the pipeline. The honest limits are the ~3 s
fixed gateway latency per call (D18 budgets one call per document) and single-product requests.

**Where does OpenClaw add value?** It is the hosted agent runtime: session isolation, a per-agent
tool policy, and a chat surface where a buyer can ask "what is waiting for me?" and act on the answer
through skills that call our API. We route every agent LLM call through a dedicated `procureai` agent
with all tools denied, precisely because untrusted supplier text passes through it, and keep the
direct gateway as an automatic fallback — the drill where we kill the runtime mid-run is rehearsed.

**What did Jev add over Claude's own confidence?** Calibration, cheaply. Self-reported confidence is
a number the same model made up; Jev gives a probability from a separate model: a tampered unit price
of 99.20 scores 0.01 supported where the real value scores 0.99, in one call per document. It only
ever *lowers* confidence, so the pipeline behaves identically with the mock judge — which is what the
test suite runs.

**What happens on a real email channel?** Only the transport changes. `sim/supplier.py` produces the
counter-offers today; a real mailbox would feed the same `supplier.counter_offer` event with the same
untrusted-text handling, and the same human gate would still stand between a draft and a send.

**What is the blast radius if the model goes down on stage?** Four rungs, all rehearsed
([DRILLS.md](DRILLS.md)): OpenClaw down → automatic gateway fallback, amber banner; gateway down →
replay cache; cache miss → red banner and the manual extraction form, where a human types the values
and the engine produces *identical* numbers; box down → `just run` in mock mode on the laptop.

## 8. Where to look in the code

| Question | File |
|---|---|
| "Show me the maths" | `backend/procureai/engine/costing.py` |
| "Show me the guard" | `backend/procureai/agents/number_guard.py` |
| "Show me the round cap" | `backend/procureai/engine/policy.py: can_open_turn` + `workflow/orchestrator.py: approve_negotiation` |
| "Show me the gates" | `backend/procureai/workflow/orchestrator.py` (`pending_human`, `approve_po`) |
| "Show me the contracts" | `backend/procureai/domain/models.py`, schemas in `domain/schema/` |
| "Show me the prompts" | `backend/procureai/agents/prompts/` |
