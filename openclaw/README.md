# openclaw/ — what lives on the Lightsail box (T10b, PLAN.md D11)

Part of [ProcureAI](../README.md); plan of record [PLAN.md](../PLAN.md) D11, background in
[docs/OPENCLAW.md](../docs/OPENCLAW.md) and [docs/DEPLOY.md](../docs/DEPLOY.md).

Source of truth for the OpenClaw side of ProcureAI. Nothing here holds a secret; the gateway key and
the OpenClaw token live only in `~/.openclaw/openclaw.json` on the box and in `backend/.env` locally.

## Skills (`skills/*/SKILL.md` → `~/.openclaw/workspace/skills/` on the box)

Chat surface for the `main` agent (D11 option B). Each skill is a fixed `curl | jq` against the backend
on `http://127.0.0.1:8000`; only the run id / supplier id are interpolated, and the skill text tells the
model to copy them verbatim from `GET /runs`.

| skill | backend call | notes |
|---|---|---|
| `procureai-health` | `GET /health` | from T10a |
| `procureai-runs` | `GET /runs` | one line per run, names the latest |
| `procureai-run-status` | `GET /runs/{id}` | state, recommendation + scores, pending human gate |
| `procureai-approve-negotiation` | `POST /runs/{id}/negotiation/{sid}/approve` with `{}` | only after an explicit "yes" in chat; never edits the draft |

Deliberately absent: anything that generates a PO or edits negotiation text. `just deploy` rsyncs this
directory to `~/.openclaw/workspace/skills/` (hot-reloaded) and the backend now runs on the box at
`127.0.0.1:8000` (T10c, docs/DEPLOY.md); `just tunnel-reverse` is only for testing the skills against a
local backend.

## The `procureai` agent (`agents.entries.procureai` in `~/.openclaw/openclaw.json`)

Target of `OPENCLAW_MODEL=openclaw/procureai`, i.e. what `OpenClawLLMClient` talks to. Untrusted supplier
text passes through it, so it has no tools, no bootstrap files and no memory (values shown, token omitted):

```json5
{
  name: "procureai",
  workspace: "/home/ubuntu/.openclaw/workspace-procureai",   // empty directory: no AGENTS/SOUL/USER/IDENTITY
  agentDir: "/home/ubuntu/.openclaw/agents/procureai/agent",
  model: { primary: "ollama/sonnet4.5:latest" },              // num_predict 2048 comes from models.providers.ollama.models[0].params
  contextInjection: "never",                                  // per-agent "bootstrap off"
  skills: [],
  tools: {
    profile: "minimal",
    deny: ["exec", "write", "edit", "apply_patch", "process", "web_fetch", "browser",
           // defence in depth: the minimal profile still exposed tool_search/tool_call/gateway/session_status
           "gateway", "session_status", "tool_search", "tool_describe", "tool_call", "read", "ls", "openclaw",
           "sessions_yield", "sessions_list", "sessions_send", "sessions_spawn", "sessions_history", "subagents",
           "cron", "message", "memory_search", "memory_get", "canvas", "nodes", "image"],
  },
  memory: { search: { enabled: false, rememberAcrossConversations: false } },
  identity: { name: "procureai" },
}
```

Applied with `openclaw config patch --file <json5>`; the previous config is kept as `openclaw.json.pre-t10b`.
`main` is untouched (full tools, all skills) and stays the chat agent. OpenClaw auto-created a weekly
"skill collection review" cron job for the new agent (`openclaw cron list`); disable it to save credit.
