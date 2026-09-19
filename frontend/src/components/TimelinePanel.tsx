import { useEffect, useRef, useState } from 'react'
import type { EventActor, NegotiationOffer, WorkflowEvent, WorkflowState } from '../api/types'
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

/** Negotiation events (T11) carry {supplier_id, round, offer} and get their own rendering. */
function isNegotiation(type: string): boolean {
  return type.startsWith('negotiation.') || type === 'supplier.counter_offer' || type === 'rescoring.started'
}

function offerOf(payload: Record<string, unknown>): NegotiationOffer | null {
  const o = payload.offer as Partial<NegotiationOffer> | null | undefined
  return o && typeof o.unit_price === 'string' && typeof o.lead_time_days === 'number' ? (o as NegotiationOffer) : null
}

function EventRow({ event }: { event: WorkflowEvent }) {
  const [open, setOpen] = useState(false)
  const a = ACTOR[event.actor]
  const transition = event.state_before !== event.state_after
  const agent = typeof event.payload.agent === 'string' ? (event.payload.agent as string) : null
  const negotiation = isNegotiation(event.type)
  const offer = negotiation ? offerOf(event.payload) : null
  const blocked = event.type === 'negotiation.policy_blocked'
  const counter = event.type === 'supplier.counter_offer'
  const violations = blocked && Array.isArray(event.payload.violations) ? (event.payload.violations as string[]) : []
  const replyText = counter && typeof event.payload.reply_text === 'string' ? (event.payload.reply_text as string) : null
  const accent = blocked ? 'border-l-2 border-l-red-500' : counter ? 'border-l-2 border-l-pink-400' : negotiation ? 'border-l-2 border-l-amber-400' : ''
  return (
    <li className={`rounded border px-3 py-2 text-sm ${transition ? 'border-zinc-700 bg-zinc-900' : 'border-zinc-800/60 bg-zinc-950/40'} ${accent}`}>
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
          {negotiation && (offer || typeof event.payload.round === 'number') && (
            <div className="mono mt-1 flex flex-wrap gap-1.5 text-[11px]">
              {typeof event.payload.supplier_id === 'string' && <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-300">{event.payload.supplier_id}</span>}
              {typeof event.payload.round === 'number' && <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-300">round {event.payload.round}</span>}
              {offer && (
                <span className={`rounded px-1.5 py-0.5 ${counter ? 'bg-pink-900/50 text-pink-100' : 'bg-amber-900/50 text-amber-100'}`}>
                  {counter ? 'counter' : 'ask'} {offer.unit_price}/unit · {offer.lead_time_days} d
                </span>
              )}
              {typeof event.payload.verdict === 'string' && <span className="rounded bg-sky-900/50 px-1.5 py-0.5 text-sky-100">verdict: {event.payload.verdict}</span>}
              {typeof event.payload.status === 'string' && <span className="rounded bg-emerald-900/50 px-1.5 py-0.5 text-emerald-100">{event.payload.status}</span>}
              {event.payload.edited === true && <span className="rounded bg-amber-900/50 px-1.5 py-0.5 text-amber-100">edited by human</span>}
            </div>
          )}
          {/* Supplier reply text is untrusted (G4): plain escaped text, never HTML/markdown. */}
          {replyText && <p className="mt-1 whitespace-pre-wrap break-words border-l border-pink-900 pl-2 text-xs italic text-zinc-400">{replyText}</p>}
          {violations.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-red-300">
              {violations.map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
          )}
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
