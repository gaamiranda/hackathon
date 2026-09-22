# OPENCLAW — Lightsail spike (T10a, 2026-09-22)

What OpenClaw can do for us programmatically, measured on the real box through the organiser LLM
gateway. Answers PLAN.md OQ1 and fixes D11. Everything below was run on the box unless marked
*untested*. No secrets in this file; the gateway key and the OpenClaw token live only in
`~/.openclaw/openclaw.json` on the box (mode 600) and in `backend/.env` locally.

## 1. Box and install log

| Item | Value |
|---|---|
| Lightsail instance | `procureai-warroom`, ap-southeast-1a, Ubuntu 24.04.4 LTS, $24 plan (2 vCPU, 3.8 GB, 80 GB) |
| Public IP | `47.129.120.76` (SSH `ubuntu@`, key `LightsailDefaultKey-ap-southeast-1.pem`) |
| Lightsail firewall | 22, 80, 443 open to Any IPv4; **18789 not open** (gateway binds loopback only) |
| Versions | node v24.21.0, npm 11.19.0, **OpenClaw 2026.9.5 (ec9c1a1)**, opencode 1.18.32, Python 3.12.3 |
| systemd unit | `openclaw-gateway.service` — a **user** unit: `~/.config/systemd/user/openclaw-gateway.service` |
| Listener | `127.0.0.1:18789` and `[::1]:18789` (WS + HTTP multiplexed) |
| Logs | `journalctl --user -u openclaw-gateway`, file log `/tmp/openclaw/openclaw-<date>.log`, `openclaw logs --follow` |
| Config | `~/.openclaw/openclaw.json` (+ `.bak*`, `.last-good`), agent state `~/.openclaw/agents/main/`, workspace `~/.openclaw/workspace/` (AGENTS.md, SOUL.md, IDENTITY.md, USER.md, `skills/`) |
| Restart | `systemctl --user restart openclaw-gateway` (≈15 s until it listens again); `openclaw gateway status`; `openclaw gateway restart` also works |
| Resource use | gateway RSS ≈ 650–740 MB idle; box: 1.26 GB used / 2.5 GB available, no swap, load 0.2 |

Install sequence actually run (starter kit steps 2–5, 10, 12, scripted over SSH):

```bash
curl -fsSL https://opencode.ai/install | bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh | bash && . ~/.nvm/nvm.sh && nvm install 24
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard --no-prompt
chmod 755 ~/.config            # see "kit deviations" 2
openclaw onboard --non-interactive --accept-risk --mode local --auth-choice ollama \
  --custom-base-url "$LLM_GATEWAY_URL" --custom-model-id sonnet4.5:latest \
  --gateway-bind loopback --install-daemon --daemon-runtime node
openclaw gateway install       # re-run after the chmod
sudo loginctl enable-linger ubuntu
# then models.providers.ollama written exactly as kit step 10 (python json edit), plus:
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
openclaw config set "models.providers.ollama.models[0].params.num_predict" 2048
systemctl --user restart openclaw-gateway
```

Exact config keys in use (values redacted): `gateway.mode=local`, `gateway.auth.mode=token`,
`gateway.auth.token`, `gateway.port=18789`, `gateway.bind=loopback`,
`gateway.http.endpoints.chatCompletions.enabled=true`, `agents.defaults.model.primary=ollama/sonnet4.5:latest`,
`agents.entries.main.*`, `tools.profile=full`, `models.mode=merge`,
`models.providers.ollama.{baseUrl, apiKey, api="ollama", headers.Authorization="Bearer …", models[0].{id="sonnet4.5:latest", name, maxTokens=2048, contextWindow=200000, params.num_predict=2048}}`.

Acceptance 1: `openclaw agent --model ollama/sonnet4.5:latest -m "Reply with the single word OK"` → `OK`
(exit 0; 7.8 s cold, ~4.0 s warm). Works with and without `--agent main`.

### Starter kit: what did not work as written

1. **`openclaw onboard` Quick start is a TTY wizard.** Over SSH I used the documented equivalent
   `--non-interactive --accept-risk --auth-choice ollama …`. It wrote the same provider/agent keys.
2. **Service install refused:** `SERVICE_DEFINITION_UNKNOWN: [unsafe-permissions] ~/.config … group/world-writable`.
   The opencode installer (kit step 2) creates `~/.config` with mode 775. `chmod 755 ~/.config` then `openclaw gateway install`.
3. **It is a systemd *user* unit.** Kit step 12's `sudo systemctl restart openclaw-gateway` fails
   (`Unit … not found`). Use `systemctl --user …`, and `sudo loginctl enable-linger ubuntu` or the gateway dies when the SSH session ends.
4. **Kit step 11 (WAF ~8 KiB body limit) does not apply to the organiser gateway.** Every OpenClaw turn
   sends 52–60 KB to `/api/chat` and gets 200. (Corrects docs/INFRA.md "body limit unverified, 7000 bytes client-side": the real limit is > 60 KB.)
5. **Kit provider block truncates every answer at 256 tokens.** OpenClaw sends `options: {}`; the gateway then caps
   output at 256 (`eval_count: 256`, `done_reason: "stop"`, silently). `maxTokens` on the model entry does **not**
   map to `num_predict`; `params.num_predict` does (verified: 803-token answer after setting it).
6. Not a kit issue but a correction to docs/INFRA.md: **the gateway does return native tool calls now**
   (`done_reason: "tool_calls"`, `message.tool_calls[...]`) for both `stream: true` and `false`. "native tools ignored" is stale.

## 2. Findings (questions 1–6)

### Q1. Does the gateway expose an API to send a message and get the reply? — Yes, three ways

**(a) HTTP `POST /v1/chat/completions`** (OpenAI-compatible; must be enabled with
`gateway.http.endpoints.chatCompletions.enabled=true`). Auth: `Authorization: Bearer <gateway.auth.token>`
(the token generated by onboarding, read it from `~/.openclaw/openclaw.json`). `model` is an *agent target*
(`openclaw/default`, `openclaw/main`, `openclaw/<agentId>`), not a provider model; `x-openclaw-model` overrides the backend model.

```
$ curl -sS http://127.0.0.1:18789/v1/models -H "Authorization: Bearer $TOK"
{"object":"list","data":[{"id":"openclaw"},{"id":"openclaw/default"},{"id":"openclaw/main"}]}   HTTP 200 0.07s
$ curl … /v1/chat/completions -d '{"model":"openclaw/default","messages":[{"role":"user","content":"hi"}]}'   # no auth
{"error":{"message":"Unauthorized","type":"unauthorized"}}                                       HTTP 401
$ curl … -H "Authorization: Bearer $TOK" -d '{"model":"openclaw/default","messages":[
    {"role":"system","content":"You answer with exactly one word."},{"role":"user","content":"Reply with the single word OK"}]}'
{"id":"chatcmpl_…","choices":[{"message":{"role":"assistant","content":"OK"},"finish_reason":"stop"}],
 "usage":{"prompt_tokens":15855,"completion_tokens":4}}                                           HTTP 200 3.86s
```

A caller-supplied `system` message is appended inside OpenClaw's own system prompt and is obeyed (an
"answer only with JSON {supplier, unit_price}" system prompt returned exactly that — in a ```json fence,
same quirk as the direct gateway). Request fields supported: `tools`, `tool_choice`, `max_tokens`/`max_completion_tokens`,
`temperature`, `stop`, `stream` (SSE), `user` (session key, see Q4).

**(b) WebSocket JSON-RPC on the same port.** Handshake: server sends `connect.challenge`; client sends
`{"type":"req","method":"connect","params":{"minProtocol":4,"maxProtocol":4,"client":{"id":"cli","mode":"cli",…},
"role":"operator","scopes":["operator.read","operator.write"],"auth":{"token":"<gateway.auth.token>"}}}` → `hello-ok`
(protocol 4, no device signing needed on loopback; `mode` must be `cli` or `ui`, `operator` is rejected). Then
`chat.send {sessionKey, message, idempotencyKey}` → `res {runId, status:"started"}` → `chat` events with
`state: status | delta | final` (`final.message.content = [{"type":"text","text":"OK"}]`). Measured **3.20 s** end to end
(`t10a/ws_chat.py`, 60 lines of Python with `websockets`).

**(c) `POST /hooks/agent`** — fire-and-forget (returns `{ok, runId}` only, never the text). Disabled by default
(404 here). Not useful for us.

Also **`POST /tools/invoke`** (always on): runs one tool with no LLM turn. `sessions_list` → 200; `exec`, `read`
→ 404 "Tool not available" (hard deny list over HTTP); `web_fetch` to 127.0.0.1 → 500 (private-IP block).

### Q2. Custom tools/skills that call our backend — Yes (skills), demonstrated

Format: a directory `~/.openclaw/workspace/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`,
optional `metadata.openclaw.requires.bins/env`, `user-invocable`, `command-dispatch: tool`) and markdown
instructions. Skills are *instructions* that make the agent use its built-in tools (`exec`, `web_fetch`, …);
typed tools with schemas need a plugin (`plugins/sdk-entrypoints/define-tool-plugin`). The skill list is hot-reloaded
(`openclaw skills list` showed `✓ ready procureai-health … openclaw-workspace` immediately).

`procureai-health/SKILL.md` (kept on the box) tells the agent to run
`curl -sS --max-time 5 http://127.0.0.1:8000/health` with `exec`. With a `python3 -m http.server`-style stub on :8000:

```
$ curl … /v1/chat/completions -d '{"model":"openclaw/default","messages":[{"role":"user",
   "content":"Is the ProcureAI backend up? Use the procureai-health skill and report the JSON."}]}'
→ "The ProcureAI backend is **up**. … {"status": "ok", "service": "procureai-backend-stub", "hits": 2, …}"   HTTP 200 10.5s
stub log: 07:29:28 "GET /health HTTP/1.1" 200
upstream (logging proxy between OpenClaw and the LLM gateway):
  call 2: roles [system,user,assistant,tool,user] → resp tool_calls [{"function":{"name":"exec",
          "arguments":{"command":"curl -sS --max-time 5 http://127.0.0.1:8000/health"}}}]  done_reason "tool_calls"
  call 3: final text                                                                    (3 calls, 2.9 + 2.8 + 3.2 s)
```

Acceptance 3 met: the skill ran from an agent turn, through our LLM gateway.

### Q3. Non-interactive call with system prompt + user message + timeout — ranked

| Option | Round trip ("OK") | Notes |
|---|---|---|
| 1. HTTP `/v1/chat/completions` | **2.3 / 3.4 / 2.6 / 3.9 s** (4 runs) | Stateless by default; `system` honoured; JSON in, JSON out; timeout = HTTP client timeout (run is cancelled on disconnect). Upstream share 2.0–2.2 s, so OpenClaw adds ~0.3–1 s. |
| 2. WS `chat.send` | 3.2 s | Streaming deltas + `final`; needs a small client; per-call session key. |
| 3. `openclaw agent --agent main -m … --json --timeout N` | 4.0 / 4.0 / 4.1 s (7.8 s cold) | Subprocess, ~1.8 s CPU for the CLI itself; `--json` gives `payloads[].text`; reuses the `main` session unless `--session-key` is given. |
| 4. `openclaw agent exec "…" --json --timeout N` | 10.7 s | Embedded run, temp state dir, no gateway needed; too slow. |
| 5. `/hooks/agent` | n/a | Never returns model output. |

Direct gateway from the same box for comparison: 2.0–3.1 s per `/api/chat`. Every OpenClaw turn costs
**~14 k prompt tokens** (49 KB system prompt: AGENTS/SOUL/IDENTITY files, tool catalogue, skill list) versus
~0.5 k for a direct call; a tool-using turn is 2–3 upstream calls (42 k tokens for the health check).

### Q4. Session memory — configurable, stateless by default

`/v1/chat/completions` without a `user` field generates a new session key per request:
"The secret word is PELICAN" → next call "What is the secret word?" → `unknown.`
With the same `user: "conv:t10a-1"` on both calls → `WALRUS`. `x-openclaw-session-key` gives explicit routing.
Each stateless call leaves a session row (`openclaw sessions list` grew to 15) — harmless, but prune periodically.
`openclaw agent -m` without `--session-key` appends to `agent:main:main`.

### Q5. Does OpenClaw tool calling work through this gateway? — Yes, natively

OpenClaw's Ollama provider sends the native `/api/chat` `tools` array (12 tools by default:
`apply_patch, edit, exec, ls, openclaw, process, read, sessions_yield, tool_call, tool_describe, tool_search, write`;
the other ~40 sit behind `tool_search`). The organiser gateway forwards them and returns
`message.tool_calls` — confirmed both through OpenClaw (Q2) and with a direct curl (`stream` true and false).
So no XML/text tool-call parsing is involved or needed; OpenClaw only strips stray `<tool_call>` text for display.

### Q6. Resource use on the 4 GB box

```
$ free -m            total  used  free  buff/cache  available
Mem:                  3832  1255   693        2188       2576      Swap: 0
$ top -o %MEM        node gateway RSS 739 MB (18.8 %), spawn broker 70 MB; load 0.23 idle; CPU 4.8 % during a turn
```
Room for FastAPI (~150 MB) + nginx + a frontend build. Add a 1 GB swapfile before deploying, to be safe.

## 3. Recommendation for D11: **(C) both** — A as the load-bearing path, B as the demo surface

Reasons: (a) the OpenAI-compatible endpoint is stateless, honours our system prompt, accepts the same
`extract / explain / draft / explain-diff` JSON tasks unchanged, and costs only ~0.5–1 s over the direct gateway —
so "every agent call is executed by OpenClaw" is literally true and auditable (`executor: openclaw` in the event log)
with the direct gateway as a one-line fallback; (b) skills that call the backend already work, so a user can drive
the war room from OpenClaw chat; both together are ~1 day. (D) is not needed; (A) or (B) alone leaves easy credit on the table.

### T10b scope and effort (≈ 1 day)

**A. `OpenClawLLMClient` (≈ 4 h)** — `backend/procureai/llm/openclaw.py`, same `LLMClient` interface as the gateway client.
- `POST http://127.0.0.1:18789/v1/chat/completions`, headers `Authorization: Bearer $OPENCLAW_TOKEN`,
  body `{"model": "openclaw/procureai", "messages": [system, user], "stream": false, "temperature": 0, "max_tokens": N}`;
  read `choices[0].message.content` → existing tolerant JSON extractor; keep `LLM_MAX_BODY_BYTES` for *our* payload only.
- Env: `LLM_CLIENT=gateway|openclaw` (default gateway locally, openclaw on the box), `OPENCLAW_URL`, `OPENCLAW_TOKEN`
  (from `openclaw.json` on the box; never committed). Timeout 30 s; on connection error / timeout / non-200 fall back to
  `GatewayLLMClient` and emit `agent.executor_fallback`. Cache key unchanged (task/prompt), so replay tests never touch either.
- **Dedicated agent on the box**: `agents.entries.procureai` with `tools.profile: "minimal"` and a deny list for
  `exec, write, edit, apply_patch, process, web_fetch, browser` (per-agent `agents.entries.<id>.tools.*`), a tiny workspace
  (no SOUL/USER bootstrap, `agents.defaults.bootstrap` off) so the prompt drops from 49 KB to a few KB and the agent cannot act
  on injected instructions inside supplier quotes. Verify with the logging-proxy trick (`t10a/logproxy.py`) that `n_tools` is 0–2.
- Keep `params.num_predict: 2048` in `openclaw.json`; add `openclaw/openclaw.json.example` (no secrets) to the repo.
- *Untested*: whether request-level `max_tokens` maps to `num_predict`; the config param is the known-good.

**B. Skill set (≈ 3 h)** — `openclaw/skills/{procureai-status,procureai-create-run,procureai-approve}/SKILL.md`, rsynced to
`~/.openclaw/workspace/skills/` by the deploy script. Each skill is a fixed `curl` against the backend API
(`GET /runs/{id}`, `POST /runs`, `POST /runs/{id}/gates/{gate}/approve`) with arguments interpolated only into the JSON body
via `--data @-`; never let the model compose a shell string. Demo line: "ask OpenClaw 'approve the PO for run 42'".

**Deploy (already in T10 scope, ≈ 3 h)** — backend as a user systemd unit on :8000, nginx :80 → `frontend/dist` + `/api/` → :8000;
18789 stays loopback; add swap; pin `openclaw@2026.9.5` if reinstalling.

### Risks
1. **Prompt injection reaches an agent with shell access** — mitigated by the tool-less `procureai` agent for A and fixed-command
   skills for B; T14's injection detector still runs before any text reaches the model.
2. **Silent 256-token truncation** if `params.num_predict` is lost (e.g. re-onboarding) — add the "numbers 1–400" check to the deploy script.
3. **Token cost** ≈ 14 k prompt tokens/call with the default agent (~USD 0.04 at Sonnet prices) — the dedicated agent cuts it
   to ~2 k; demo run of ~8 calls stays under USD 0.50 either way.
4. **Gateway restart window** ≈ 15 s and `Restart=always`; the fallback client covers it.
5. **Rate limiting**: the organiser gateway returned no 403/429 in ~30 calls today, but a tool-using turn makes 2–3 upstream calls;
   keep calls sequential.
6. **Version drift**: `openclaw.ai/install.sh` pulls latest; the HTTP endpoint and config keys above are from 2026.9.5.
