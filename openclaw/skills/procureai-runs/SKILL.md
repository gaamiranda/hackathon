---
name: procureai-runs
description: List the ProcureAI procurement runs on this box (GET /runs) and summarise the state of each one.
metadata: { "openclaw": { "requires": { "bins": ["curl", "jq"] } } }
---
# ProcureAI: list runs

When the user asks which procurement runs exist, how many there are, what the latest run is, or
"show me the runs", run this exact command with the `exec` tool. Do not change it and do not add
arguments:

```bash
curl -sS --max-time 5 http://127.0.0.1:8000/runs | jq -c '.[] | {run_id, state, product, quantity, created_at}'
```

Report one line per run, oldest first as returned: `run_id — product × quantity — STATE (created_at)`.
Then say which run is the latest (the last line). Use the exact `run_id` values from the output;
never invent or shorten one. If the list is empty say there are no runs. If the command fails, say
the ProcureAI backend is not reachable and include the error.

States, in pipeline order: CREATED → EXTRACTING → NEEDS_HUMAN_EXTRACTION? → EXTRACTED → VALIDATING →
CALC_MISMATCH? → ENRICHING → SCORING → RECOMMENDED → NEGOTIATION_DRAFTED → AWAITING_NEGOTIATION_APPROVAL →
NEGOTIATING → COUNTER_RECEIVED → RE_SCORING → RECOMMENDED → AWAITING_PO_APPROVAL → PO_GENERATED
(REPLANNING after a requirement change). States ending in `?` and every AWAITING_* state wait for a human.

For details on one run use the `procureai-run-status` skill.
