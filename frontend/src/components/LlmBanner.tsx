import type { Health } from '../api/types'

/**
 * Fail-safe banner (T17, PLAN.md G6). Amber: the OpenClaw route failed and calls take the direct gateway — nothing
 * for the viewer to do. Red: no LLM route answers, so extraction and explanations need a human while every number
 * stays the engine's. Hidden when the AI is fine (mock mode is always fine: it has no LLM).
 */
export function LlmBanner({ health }: { health: Health | null }) {
  if (!health || health.llm === 'ok') return null
  const routes = Object.entries(health.llm_routes)
    .map(([route, a]) => `${route}: ${a.ok ? 'ok' : `failed (${a.error ?? 'unknown error'})`} at ${new Date(a.at).toLocaleTimeString(undefined, { hour12: false })}`)
    .join(' · ')
  const title = `llm=${health.llm} · openclaw ${health.openclaw} · gateway ${health.gateway}${routes ? ` · ${routes}` : ''}`
  if (health.llm === 'degraded') {
    return (
      <div role="status" title={title} className="flex items-center gap-2 border-b border-amber-700 bg-amber-950/70 px-4 py-1.5 text-sm text-amber-100">
        <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-amber-400" />
        <span className="font-semibold">AI route degraded:</span> using fallback gateway
      </div>
    )
  }
  return (
    <div role="alert" title={title} className="flex items-center gap-2 border-b border-red-700 bg-red-950/80 px-4 py-1.5 text-sm text-red-100">
      <span className="live-dot inline-block h-2 w-2 shrink-0 rounded-full bg-red-400" />
      <span className="font-semibold">AI unavailable — manual mode.</span>
      <span>Extraction and explanations require human input; all numbers remain engine-verified.</span>
    </div>
  )
}
