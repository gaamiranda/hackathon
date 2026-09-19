# INFRA — organiser LLM gateway

Findings from T4 (`backend/scripts/probe_gateway.py`, run 2026-09-19). Answers PLAN.md OQ2 and the
gateway assumptions in §2. Everything here was measured against the live gateway, not inferred from
the starter kit, unless marked *untested*.

Endpoint: `https://api.softwaresystems.app` (Ollama-compatible). Credentials live in `backend/.env`
(`LLM_GATEWAY_URL`, `LLM_GATEWAY_API_KEY`, `LLM_MODEL`) and nowhere else.

## Auth header

`Authorization: Bearer <key>` — works, 200 on the first attempt. The `X-API-Key` header mentioned in
the kit README was never needed, so the client only sends `Bearer` (OQ2 closed). The probe falls back
to `X-API-Key` only on 401/403.

## Models — `GET /api/tags`

Returns `{"models": [...]}` in ~20–26 ms. Three aliases, all Bedrock-backed Claude:

| name | details |
|---|---|
| `sonnet4.5:latest` | `{"format": "bedrock", "family": "claude", "parameter_size": "", "quantization_level": ""}` |
| `sonnet:latest` | same |
| `haiku:latest` | same |

`size` is `0` for all three. The gateway **also accepts the full Bedrock model id directly**:
`global.anthropic.claude-sonnet-4-5-20250929-v1:0` (what `backend/.env` currently sets) returns 200 and
echoes that id back in `model`. Both forms work; the alias is the safer default for the demo because it
survives a model swap on the organiser side.

## Request shape — `POST /api/chat`

```json
{"model": "...", "messages": [{"role": "system", ...}, {"role": "user", ...}],
 "stream": false, "options": {"num_predict": 400, "temperature": 0}}
```

A `system` role message is accepted and obeyed. `stream: false` returns one complete JSON document, so
**streaming is not needed** anywhere in this project; the backend never needs an SSE reader for the LLM.

## Response shape

Top-level keys: `created_at`, `done`, `done_reason`, `eval_count`, `message`, `model`,
`prompt_eval_count`, `total_duration`.

- assistant text: `message.content` (`message` = `{"role": "assistant", "content": ...}`)
- token counts: `prompt_eval_count` (input), `eval_count` (output)
- `total_duration` is always `0` — not populated, so latency must be measured client-side
- no `load_duration` / `eval_duration` / `context` fields

## Latency

| call | measured |
|---|---|
| `GET /api/tags` | 20–26 ms |
| `POST /api/chat`, 13-token prompt, 5 tokens out | 3.2 s |
| `POST /api/chat`, quote extraction (~430–1000 chars in, ~120 tokens out) | 2.4–3.2 s |

There is a fixed overhead of roughly 2–3 s per call regardless of size. Budget for it: the 30 s
processing-time metric (D10) allows about 3 extraction calls plus an explanation, sequentially, with
room to spare — but a per-supplier chain of 6+ calls would not fit.

## `num_predict` — always set it

Omitting `options.num_predict` caps the answer at **exactly 256 output tokens** (`eval_count: 256`),
not the ~200 assumed in PLAN.md §2.

The dangerous part: a truncated response still reports `done: true` and `done_reason: "stop"`. A cut-off
answer is **indistinguishable from a complete one** by the response metadata alone — asking for the
numbers 1 to 400 returned a string ending mid-sequence at "127 " with `done_reason: "stop"`. So:

- `GatewayLLMClient` always sends `options.num_predict` (from the call's `max_tokens`); never rely on
  the default;
- never trust `done_reason` to detect truncation — validate the payload (pydantic parse / JSON parse)
  instead, which is what `json_mode` already does.

## Body size limit

*Untested* — the ~8 KiB WAF limit comes from the starter kit, and deliberately was not probed (a
rejected oversized request risks tripping rate limiting on a shared key). The client enforces
`LLM_MAX_BODY_BYTES = 7000` bytes on the serialised body and raises `LLMRequestTooLarge` **before**
sending. For reference, the three synthetic quotes produce bodies of 852–1,427 bytes, so there is ~5x
headroom on the demo path.

## Rate limiting / quota

Not triggered during T4 (7 live calls total, spread over minutes). The client treats `403` as rate
limiting and `429` as quota, retrying both at 2 s then 4 s (3 attempts) before raising `LLMUnavailable`;
4xx other than those fails immediately without burning a retry.

## Output formatting quirk

Even with an explicit "Respond with a single JSON object and nothing else. No prose, no code fences."
instruction, Sonnet 4.5 wrapped its answer in a ```json fence on all three extraction calls. The
tolerant extractor in `gateway.extract_json` (strip fences → first balanced `{...}` block) handles it,
and the recorded cache entries in `data/llm_cache/extract/` show the raw fenced text. Do not assume
bare JSON from this gateway.

## Cost control

`data/llm_cache/` is a committed record/replay cache keyed by
`sha256(model, task, system, user, max_tokens, json_mode)` (D19). `LLM_CACHE_MODE`:

- `replay_or_record` (default) — replay if recorded, otherwise call and record
- `record` — always call, overwrite
- `replay_only` — raise `LLMCacheMiss` on a miss; set by `backend/tests/conftest.py`, so the test suite
  and CI can never spend a token or reach the network

T4 spent **7 live calls**: 2 × `/api/chat` probe, 2 × `/api/tags`, 3 × extraction (recorded).
T5 spent **7 more**: 3 extractions on a first draft of the Document Agent prompt, 3 after the prompt
was corrected, 1 on the injection fixture. The superseded entries were pruned, so every file under
`data/llm_cache/` is reachable by some current prompt.
