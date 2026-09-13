import { useEffect, useRef, useState } from 'react'
import type { EventActor, WorkflowEvent, WorkflowState } from '../api/types'
import type { StreamStatus } from '../hooks/useEventStream'
import { time } from '../format'
import { Panel } from './Panel'
import { StateBadge } from './StateBadge'

const ACTOR: Record<EventActor, { dot: string; text: string; label: string }> = {
  agent: { dot: 'bg-violet-400', text: 'text-violet-300', label: 'agent' },
  engine: { dot: 'bg-sky-400', text: 'text-sky-300', label: 'engine' },
  human: { dot: 'bg-amber-400', text: 'text-amber-300', label: 'human' },
  supplier: { dot: 'bg-pink-400', text: 'text-pink-300', label: 'supplier' },
}

const STATUS: Record<StreamStatus, { cls: string; label: string }> = {
  live: { cls: 'bg-emerald-400 live-dot', label: 'live' },
  connecting: { cls: 'bg-amber-400', label: 'connecting…' },
  disconnected: { cls: 'bg-red-500', label: 'disconnected – retrying' },
  gone: { cls: 'bg-zinc-500', label: 'run no longer exists on the backend' },
}

export function TimelinePanel({ events, status, state }: { events: WorkflowEvent[]; status: StreamStatus; state: WorkflowState }) {
  const bottom = useRef<HTMLDivElement>(null)
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [events.length])

  return (
    <Panel
      title="Agents & Timeline"
      right={
        <div className="flex items-center gap-3">
          <StateBadge state={state} />
          <span className="flex items-center gap-1.5 text-xs text-zinc-400">
            <span className={`inline-block h-2 w-2 rounded-full ${STATUS[status].cls}`} />
            {STATUS[status].label}
          </span>
        </div>
      }
    >
      {events.length === 0 && <p className="text-sm text-zinc-500">Waiting for events…</p>}
      <ol className="space-y-1.5">
        {events.map((e) => (
          <EventRow key={e.seq} event={e} />
        ))}
      </ol>
      <div ref={bottom} />
    </Panel>
  )
}

function EventRow({ event }: { event: WorkflowEvent }) {
  const [open, setOpen] = useState(false)
  const a = ACTOR[event.actor]
  const transition = event.state_before !== event.state_after
  const agent = typeof event.payload.agent === 'string' ? (event.payload.agent as string) : null
  return (
    <li className={`rounded border px-3 py-2 text-sm ${transition ? 'border-zinc-700 bg-zinc-900' : 'border-zinc-800/60 bg-zinc-950/40'}`}>
      <div className="flex items-start gap-2">
        <span className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${a.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 text-xs">
            <span className="mono text-zinc-600">#{event.seq}</span>
            <span className="mono text-zinc-500">{time(event.ts)}</span>
            <span className={`font-semibold ${a.text}`}>{agent ? `${a.label}:${agent}` : a.label}</span>
            <span className="mono text-zinc-400">{event.type}</span>
            {transition && (
              <span className="mono ml-auto text-zinc-500">
                {event.state_before ?? '∅'} → <span className="text-zinc-200">{event.state_after}</span>
              </span>
            )}
          </div>
          <p className="mt-0.5 break-words text-zinc-200">{event.summary}</p>
          {Object.keys(event.payload).length > 0 && (
            <button onClick={() => setOpen(!open)} className="mt-1 text-xs text-zinc-500 hover:text-zinc-300">
              {open ? '▾ payload' : '▸ payload'}
            </button>
          )}
          {open && <pre className="mono mt-1 max-h-64 overflow-auto rounded bg-black/50 p-2 text-[11px] leading-snug text-zinc-300">{JSON.stringify(event.payload, null, 2)}</pre>}
        </div>
      </div>
    </li>
  )
}
