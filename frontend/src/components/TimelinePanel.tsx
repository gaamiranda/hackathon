import { useEffect, useRef, useState } from 'react'
import type { EventActor, NegotiationOffer, WorkflowEvent, WorkflowState } from '../api/types'
import type { StreamStatus } from '../hooks/useEventStream'
import { money, time } from '../format'
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

/** Guardrail judge events (T14, D20): a second, calibrated opinion. Green shield = every critical field is
 *  supported by the document (lowest probability shown); red shield = the text carries instructions aimed at
 *  an AI / the procurement software (probability shown). Informational: the judge only lowers confidence or
 *  adds a violation, never decides. */
export function Shield({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" className={`inline-block shrink-0 ${className}`} fill="currentColor">
      <path d="M8 1 2.5 3v4c0 3.4 2.3 6.3 5.5 7.5 3.2-1.2 5.5-4.1 5.5-7.5V3L8 1Z" />
    </svg>
  )
}

const GUARD_VERIFIED = 'guardrail.extraction_verified'
const GUARD_INJECTION = 'guardrail.injection_detected'

function probability(v: unknown): string | null {
  return typeof v === 'number' ? `p=${v.toFixed(2)}` : null
}

/** Negotiation events (T11) carry {supplier_id, round, offer} and get their own rendering. */
function isNegotiation(type: string): boolean {
  return type.startsWith('negotiation.') || type === 'supplier.counter_offer' || type === 'rescoring.started'
}

function offerOf(payload: Record<string, unknown>): NegotiationOffer | null {
  const o = payload.offer as Partial<NegotiationOffer> | null | undefined
  return o && typeof o.unit_price === 'string' && typeof o.lead_time_days === 'number' ? (o as NegotiationOffer) : null
}

/** Interrupt / replan events (T13, D22): the human's change and the engine's replan get a red accent. */
const REPLAN_TYPES = new Set(['requirement.changed', 'replan.started', 'replan.completed', 'negotiation.discarded'])

/** PO gate events (T15, G5): request/reject/discard get a distinct row; po.generated is the green finish line. */
const PO_TYPES = new Set(['po.requested', 'po.generated', 'po.rejected', 'po.discarded'])

const CHANGE_LABEL: Record<string, string> = { quantity: 'quantity', budget: 'budget', required_by: 'required by' }

function changesOf(payload: Record<string, unknown>): [string, { before: unknown; after: unknown }][] {
  const c = payload.changes
  if (!c || typeof c !== 'object') return []
  return Object.entries(c as Record<string, { before: unknown; after: unknown }>).filter(([, v]) => v && typeof v === 'object')
}

function fmtChange(field: string, v: unknown): string {
  if (typeof v === 'number') return v.toLocaleString()
  return field === 'budget' && typeof v === 'string' ? money(v) : String(v)
}

/** agent.finished carries `backend` = which LLM route answered (T10b, D11): the demo claim "every agent call
 *  runs through OpenClaw" is checked row by row. Agents without an LLM (history lookup) carry none. */
const BACKEND_LABEL: Record<string, { text: string; cls: string }> = {
  openclaw: { text: 'via OpenClaw', cls: 'bg-emerald-900/50 text-emerald-100' },
  gateway: { text: 'via gateway', cls: 'bg-amber-900/50 text-amber-100' },
  replay: { text: 'via replay', cls: 'bg-zinc-800 text-zinc-300' },
  template: { text: 'templated', cls: 'bg-zinc-800 text-zinc-400' },
  mock: { text: 'mock', cls: 'bg-zinc-800 text-zinc-400' },
}

function backendOf(event: WorkflowEvent): { text: string; cls: string } | null {
  if (event.type !== 'agent.finished' || typeof event.payload.backend !== 'string') return null
  const b = event.payload.backend as string
  return BACKEND_LABEL[b] ?? { text: `via ${b}`, cls: 'bg-zinc-800 text-zinc-300' }
}

function EventRow({ event }: { event: WorkflowEvent }) {
  const [open, setOpen] = useState(false)
  const a = ACTOR[event.actor]
  const transition = event.state_before !== event.state_after
  const agent = typeof event.payload.agent === 'string' ? (event.payload.agent as string) : null
  const backend = backendOf(event)
  const replan = REPLAN_TYPES.has(event.type)
  const po = PO_TYPES.has(event.type)
  const negotiation = !replan && !po && isNegotiation(event.type)
  const offer = negotiation ? offerOf(event.payload) : null
  const blocked = event.type === 'negotiation.policy_blocked'
  const counter = event.type === 'supplier.counter_offer'
  const violations = blocked && Array.isArray(event.payload.violations) ? (event.payload.violations as string[]) : []
  const replyText = counter && typeof event.payload.reply_text === 'string' ? (event.payload.reply_text as string) : null
  const generated = event.type === 'po.generated'
  const poNumber = generated ? ((event.payload.purchase_order as { po_number?: string } | undefined)?.po_number ?? null) : null
  const verified = event.type === GUARD_VERIFIED
  const injection = event.type === GUARD_INJECTION
  const unsupported = verified && Array.isArray(event.payload.unsupported) ? (event.payload.unsupported as string[]) : []
  const guardOk = verified && unsupported.length === 0
  const accent = generated
    ? 'border-l-2 border-l-emerald-400'
    : guardOk
      ? 'border-l-2 border-l-emerald-500'
      : injection || (verified && !guardOk)
        ? 'border-l-2 border-l-red-500'
    : replan || blocked || event.type === 'po.rejected' || event.type === 'po.discarded'
      ? 'border-l-2 border-l-red-500'
      : po
        ? 'border-l-2 border-l-amber-400'
        : counter
          ? 'border-l-2 border-l-pink-400'
          : negotiation
            ? 'border-l-2 border-l-amber-400'
            : ''
  const changes = event.type === 'requirement.changed' ? changesOf(event.payload) : []
  const reason = event.type === 'requirement.changed' && typeof event.payload.reason === 'string' ? (event.payload.reason as string) : ''
  const revisiting = event.type === 'replan.started' && Array.isArray(event.payload.revisiting) ? (event.payload.revisiting as string[]) : []
  const impact = event.type === 'replan.completed' ? (event.payload.impact as { recommended_before?: string | null; recommended_after?: string | null } | undefined) : undefined
  const discarded = event.type === 'negotiation.discarded'
  return (
    <li
      className={`rounded border px-3 py-2 text-sm ${
        generated
          ? 'border-emerald-600 bg-emerald-950/40'
          : event.type === 'requirement.changed'
            ? 'border-red-800 bg-red-950/30'
            : transition
              ? 'border-zinc-700 bg-zinc-900'
              : 'border-zinc-800/60 bg-zinc-950/40'
      } ${accent}`}
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${a.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 text-xs">
            <span className="mono text-zinc-600">#{event.seq}</span>
            <span className="mono text-zinc-500">{time(event.ts)}</span>
            <span className={`font-semibold ${a.text}`}>{agent ? `${a.label}:${agent}` : a.label}</span>
            <span className="mono text-zinc-400">{event.type}</span>
            {backend && <span className={`mono rounded px-1.5 py-0.5 text-[10px] ${backend.cls}`} title="LLM route that served this agent call">{backend.text}</span>}
            {transition && (
              <span className="mono ml-auto text-zinc-500">
                {event.state_before ?? '∅'} → <span className="text-zinc-200">{event.state_after}</span>
              </span>
            )}
          </div>
          {event.type === 'requirement.changed' ? (
            <div className="mt-0.5">
              <p className="font-semibold text-red-200">Requirement changed by human</p>
              <ul className="mono mt-0.5 flex flex-wrap gap-1.5 text-[11px]">
                {changes.map(([field, c]) => (
                  <li key={field} className="rounded bg-red-900/50 px-1.5 py-0.5 text-red-100">
                    {CHANGE_LABEL[field] ?? field} {fmtChange(field, c.before)} → <span className="font-semibold">{fmtChange(field, c.after)}</span>
                  </li>
                ))}
              </ul>
              {reason && <p className="mt-1 text-xs italic text-zinc-300">“{reason}”</p>}
            </div>
          ) : event.type === 'replan.started' ? (
            <div className="mt-0.5">
              <p className="text-zinc-200">Replanning without restart on the existing quotes — revisiting:</p>
              <ul className="mono mt-0.5 flex flex-wrap gap-1.5 text-[11px]">
                {revisiting.map((r) => (
                  <li key={r} className="rounded bg-sky-900/50 px-1.5 py-0.5 text-sky-100">
                    {r.replace('_', ' ')}
                  </li>
                ))}
              </ul>
            </div>
          ) : event.type === 'replan.completed' ? (
            <p className="mt-0.5 break-words">
              <span className={impact && impact.recommended_before !== impact.recommended_after ? 'font-semibold text-red-200' : 'text-zinc-200'}>{event.summary}</span>
            </p>
          ) : discarded ? (
            <p className="mt-0.5 break-words text-zinc-300">
              <span className="mr-1 rounded bg-red-900/50 px-1.5 py-0.5 text-[11px] text-red-100">draft discarded</span>
              {event.summary}
            </p>
          ) : generated ? (
            <div className="mt-0.5">
              <p className="font-semibold text-emerald-200">
                Purchase order generated{poNumber && <span className="mono ml-2 rounded bg-emerald-800/70 px-1.5 py-0.5 text-[11px] text-emerald-50">{poNumber}</span>}
              </p>
              <p className="mt-0.5 break-words text-xs text-zinc-300">{event.summary}</p>
            </div>
          ) : event.type === 'po.requested' ? (
            <p className="mt-0.5 break-words text-zinc-200">
              <span className="mr-1 rounded bg-amber-900/50 px-1.5 py-0.5 text-[11px] text-amber-100">awaiting human approval</span>
              {event.summary}
            </p>
          ) : event.type === 'po.rejected' ? (
            <p className="mt-0.5 break-words text-zinc-300">
              <span className="mr-1 rounded bg-red-900/50 px-1.5 py-0.5 text-[11px] text-red-100">PO rejected by human</span>
              {event.summary}
            </p>
          ) : event.type === 'po.discarded' ? (
            <p className="mt-0.5 break-words text-zinc-300">
              <span className="mr-1 rounded bg-red-900/50 px-1.5 py-0.5 text-[11px] text-red-100">PO preview discarded</span>
              {event.summary}
            </p>
          ) : verified ? (
            <p className="mt-0.5 break-words text-zinc-200">
              <span className={`mr-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${guardOk ? 'bg-emerald-900/50 text-emerald-100' : 'bg-red-900/50 text-red-100'}`}>
                <Shield /> judge {guardOk ? 'verified' : `unsupported: ${unsupported.join(', ')}`}
                {probability(event.payload.lowest_probability) && <span className="mono opacity-80">lowest {probability(event.payload.lowest_probability)}</span>}
              </span>
              {event.summary}
            </p>
          ) : injection ? (
            <p className="mt-0.5 break-words text-zinc-200">
              <span className="mr-1 inline-flex items-center gap-1 rounded bg-red-900/50 px-1.5 py-0.5 text-[11px] text-red-100">
                <Shield /> judge: injection {probability(event.payload.probability) && <span className="mono opacity-80">{probability(event.payload.probability)}</span>}
              </span>
              {event.summary}
            </p>
          ) : (
            <p className="mt-0.5 break-words text-zinc-200">{event.summary}</p>
          )}
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
