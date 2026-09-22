import { useMemo } from 'react'
import type { PendingHuman, WorkflowEvent, WorkflowState } from '../api/types'
import { ENGINE_BUSY, LANES, gateLabel, laneOf, type LaneKey } from '../labels'

export type LaneStatus = 'idle' | 'working' | 'waiting' | 'done' | 'failed'

export interface LaneState {
  key: LaneKey
  status: LaneStatus
  /** One-line summary of the lane's latest event. */
  last: string | null
  count: number
  /** agent.finished carried fallback=true at least once (deterministic text stood in for the LLM). */
  fallback: boolean
  /** Guardrail judge raised an injection flag at least once. */
  flagged: boolean
  /** agent.failed payload.reason of the latest failure. */
  reason: string | null
}

const FALLBACK_REASON: Record<string, string> = {
  guard_trip: 'number guard',
  parse_error: 'unusable output',
  llm_unavailable: 'gateway down',
  error: 'error',
}

/**
 * Lane states derived purely from the event stream (plus the run state for the engine pulse and the pending gate
 * for the human lane): agent.started → working, agent.finished → done, agent.failed → failed (reason); guardrail.*
 * → judge; engine / human events → their lanes.
 */
export function deriveLanes(events: WorkflowEvent[], state: WorkflowState, pending: PendingHuman | null): Record<LaneKey, LaneState> {
  const lanes = Object.fromEntries(
    LANES.map((l) => [l.key, { key: l.key, status: 'idle', last: null, count: 0, fallback: false, flagged: false, reason: null } satisfies LaneState]),
  ) as Record<LaneKey, LaneState>
  for (const e of events) {
    const key = laneOf(e)
    if (!key) continue
    const lane = lanes[key]
    lane.count += 1
    lane.last = e.summary
    if (e.type === 'agent.started') lane.status = 'working'
    else if (e.type === 'agent.finished') {
      lane.status = 'done'
      if (e.payload.fallback === true) lane.fallback = true
    } else if (e.type === 'agent.failed') {
      lane.status = 'failed'
      lane.reason = typeof e.payload.reason === 'string' ? e.payload.reason : 'error'
    } else if (lane.status === 'idle') lane.status = 'done'
    if (e.type === 'guardrail.injection_detected') lane.flagged = true
  }
  if (ENGINE_BUSY.has(state)) lanes.engine.status = 'working'
  if (pending) {
    lanes.human.status = 'waiting'
    lanes.human.last = `Waiting: ${gateLabel(pending.kind)} — ${pending.message}`
  }
  return lanes
}

const STATUS: Record<LaneStatus, { label: string; dot: string; text: string }> = {
  idle: { label: 'idle', dot: 'bg-zinc-600', text: 'text-zinc-500' },
  working: { label: 'working', dot: 'bg-violet-400 live-dot', text: 'text-violet-300' },
  waiting: { label: 'waiting', dot: 'bg-amber-400 live-dot', text: 'text-amber-300' },
  done: { label: 'done', dot: 'bg-emerald-500', text: 'text-emerald-300' },
  failed: { label: 'failed', dot: 'bg-red-500', text: 'text-red-300' },
}

/** One card per actor; clicking a lane filters the timeline to that actor (click again to clear). */
export function AgentLanes({
  events,
  state,
  pending,
  selected,
  onSelect,
}: {
  events: WorkflowEvent[]
  state: WorkflowState
  pending: PendingHuman | null
  selected: LaneKey | null
  onSelect: (key: LaneKey | null) => void
}) {
  const lanes = useMemo(() => deriveLanes(events, state, pending), [events, state, pending])
  return (
    <div className="grid grid-cols-4 gap-1 min-[1400px]:grid-cols-7">
      {LANES.map((l) => {
        const lane = lanes[l.key]
        const s = STATUS[lane.status]
        const active = selected === l.key
        const border =
          lane.status === 'failed' || lane.flagged
            ? 'border-red-700/80'
            : lane.status === 'waiting'
              ? 'border-amber-600/80'
              : lane.status === 'working'
                ? 'border-violet-600/80'
                : 'border-zinc-800'
        return (
          <button
            key={l.key}
            type="button"
            onClick={() => onSelect(active ? null : l.key)}
            title={`${l.label}: ${lane.count} event${lane.count === 1 ? '' : 's'} — click to ${active ? 'show all lanes' : 'filter the timeline'}`}
            className={`min-w-0 rounded-md border px-1.5 py-1 text-left transition-colors hover:bg-zinc-800/60 ${border} ${
              active ? 'bg-zinc-800 ring-1 ring-emerald-500' : 'bg-zinc-900/70'
            } ${lane.status === 'working' ? 'bg-violet-950/30' : ''} ${lane.status === 'waiting' ? 'bg-amber-950/30' : ''}`}
          >
            <div className="flex items-center gap-1.5">
              <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${s.dot}`} />
              <span className={`truncate text-[11px] font-semibold ${l.text}`} title={l.label}>
                {l.short}
              </span>
            </div>
            <div className={`mt-0.5 flex items-center gap-1 whitespace-nowrap text-[10px] ${s.text}`}>
              {lane.status === 'failed' && lane.reason ? (
                <span className="truncate" title={`agent.failed: ${lane.reason}`}>
                  failed · {FALLBACK_REASON[lane.reason] ?? lane.reason}
                </span>
              ) : lane.flagged ? (
                <span className="rounded bg-red-900/60 px-1 text-red-200" title="prompt injection detected in supplier content; treated as data (G4)">
                  injection
                </span>
              ) : lane.fallback ? (
                <span className="rounded bg-amber-900/60 px-1 text-amber-200" title="an LLM answer was rejected; deterministic text was used (G6)">
                  fallback
                </span>
              ) : (
                <span>{s.label}</span>
              )}
              {lane.count > 0 && <span className="mono text-zinc-500">· {lane.count}</span>}
            </div>
            <div className="mt-0.5 truncate text-[10px] leading-tight text-zinc-400" title={lane.last ?? undefined}>
              {lane.last ?? '—'}
            </div>
          </button>
        )
      })}
    </div>
  )
}
