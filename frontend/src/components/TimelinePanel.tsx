import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { EventActor, NegotiationOffer, WorkflowEvent, WorkflowState } from '../api/types'
import type { StreamStatus } from '../hooks/useEventStream'
import { money, time } from '../format'
import { LANES, LANE_BY_KEY, laneOf, type LaneKey } from '../labels'
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
  disconnected: { cls: 'bg-red-500', label: 'stream disconnected – retrying' },
  gone: { cls: 'bg-zinc-500', label: 'run no longer exists on the backend' },
}

/** All events, only the highlighted moments, or one lane (driven by the AgentLanes strip). */
export type TimelineFilter = 'all' | 'moments' | LaneKey

const NEAR_BOTTOM_PX = 40

export function TimelinePanel({
  events,
  status,
  state,
  names,
  filter,
  onFilter,
}: {
  events: WorkflowEvent[]
  status: StreamStatus
  state: WorkflowState
  /** supplier_id → supplier name, so moments say "Cobalt Industrial" before "sup_c". */
  names: Record<string, string>
  filter: TimelineFilter
  onFilter: (f: TimelineFilter) => void
}) {
  const body = useRef<HTMLDivElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  // Auto-scroll follows the newest event until the user scrolls up; "jump to latest" resumes it.
  const [paused, setPaused] = useState(false)

  const visible = useMemo(() => {
    if (filter === 'all') return events
    if (filter === 'moments') return events.filter((e) => momentOf(e, names) !== null)
    return events.filter((e) => laneOf(e) === filter)
  }, [events, filter, names])

  const jump = useCallback(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
    setPaused(false)
  }, [])

  useEffect(() => {
    if (!paused) bottom.current?.scrollIntoView({ block: 'end' })
  }, [visible.length, paused, filter])

  const onScroll = () => {
    const el = body.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    setPaused(!atBottom)
  }

  const momentCount = useMemo(() => events.filter((e) => momentOf(e, names) !== null).length, [events, names])
  const chip = (active: boolean, extra = '') =>
    `rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${active ? 'bg-zinc-100 text-zinc-900' : `bg-zinc-800/80 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 ${extra}`}`

  return (
    <Panel
      title="Timeline"
      className="min-h-0 flex-1"
      bodyClassName="px-3 py-2"
      bodyRef={body}
      onBodyScroll={onScroll}
      right={
        <div className="flex items-center gap-3">
          <StateBadge state={state} />
          <span className="flex items-center gap-1.5 text-xs text-zinc-400">
            <span className={`inline-block h-2 w-2 rounded-full ${STATUS[status].cls}`} />
            {STATUS[status].label}
          </span>
        </div>
      }
      subheader={
        <div className="flex flex-wrap items-center gap-1 border-b border-zinc-800/80 px-3 py-1.5">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-zinc-600">Show</span>
          <button type="button" className={chip(filter === 'all')} onClick={() => onFilter('all')}>
            All · {events.length}
          </button>
          <button type="button" className={chip(filter === 'moments')} onClick={() => onFilter('moments')} title="the guardrail and decision moments only">
            Moments · {momentCount}
          </button>
          <span className="mx-1 h-3 w-px bg-zinc-800" />
          {LANES.map((l) => (
            <button key={l.key} type="button" className={chip(filter === l.key)} onClick={() => onFilter(filter === l.key ? 'all' : l.key)}>
              {l.short}
            </button>
          ))}
        </div>
      }
      overlay={
        paused && (
          <button
            type="button"
            onClick={jump}
            className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-emerald-600 bg-zinc-900/95 px-3 py-1 text-xs font-medium text-emerald-200 shadow-lg hover:bg-zinc-800"
          >
            ↓ Jump to latest
          </button>
        )
      }
    >
      {events.length === 0 && <p className="text-sm text-zinc-500">Waiting for events…</p>}
      {events.length > 0 && visible.length === 0 && (
        <p className="text-sm text-zinc-500">
          Nothing in this view yet.{' '}
          <button type="button" className="text-emerald-400 hover:underline" onClick={() => onFilter('all')}>
            Show all events
          </button>
        </p>
      )}
      <ol className="space-y-1">
        {visible.map((e) => {
          const m = momentOf(e, names)
          return m ? <MomentCard key={e.seq} event={e} moment={m} /> : <EventRow key={e.seq} event={e} />
        })}
      </ol>
      <div ref={bottom} />
    </Panel>
  )
}

// ---------------------------------------------------------------------------------------------------- moments

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

type Tone = 'red' | 'amber' | 'emerald' | 'pink' | 'zinc'

interface Moment {
  icon: ReactNode
  headline: string
  tone: Tone
  /** Secondary line(s); supplier text is untrusted (G4) and is rendered as plain text only. */
  detail?: ReactNode
}

const TONE: Record<Tone, { card: string; icon: string; head: string }> = {
  red: { card: 'border-red-700 bg-red-950/40 ring-1 ring-red-500/30', icon: 'bg-red-900/70 text-red-100', head: 'text-red-100' },
  amber: { card: 'border-amber-700 bg-amber-950/40 ring-1 ring-amber-500/30', icon: 'bg-amber-900/70 text-amber-100', head: 'text-amber-100' },
  emerald: { card: 'border-emerald-600 bg-emerald-950/40 ring-1 ring-emerald-500/30', icon: 'bg-emerald-800/70 text-emerald-50', head: 'text-emerald-100' },
  pink: { card: 'border-pink-700 bg-pink-950/40 ring-1 ring-pink-500/30', icon: 'bg-pink-900/70 text-pink-100', head: 'text-pink-100' },
  zinc: { card: 'border-zinc-600 bg-zinc-800/60 ring-1 ring-zinc-500/30', icon: 'bg-zinc-700 text-zinc-100', head: 'text-zinc-100' },
}

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

function offerOf(payload: Record<string, unknown>): NegotiationOffer | null {
  const o = payload.offer as Partial<NegotiationOffer> | null | undefined
  return o && typeof o.unit_price === 'string' && typeof o.lead_time_days === 'number' ? (o as NegotiationOffer) : null
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}

const FAILED_HEADLINE: Record<string, string> = {
  guard_trip: 'LLM output rejected by the number guard; using deterministic text',
  parse_error: 'LLM output unusable; using deterministic text',
  llm_unavailable: 'LLM gateway unavailable; using deterministic text',
}

/**
 * The moments a judge must not miss (T16): the guardrails firing, the human gates, the supplier's move, the
 * interrupt and its outcome, and the finish line. Everything else renders as an ordinary row.
 */
export function momentOf(e: WorkflowEvent, names: Record<string, string>): Moment | null {
  const p = e.payload
  const name = (sid: unknown) => (typeof sid === 'string' ? (names[sid] ?? sid) : '—')
  switch (e.type) {
    case 'calc.mismatch':
      return { tone: 'red', icon: '≠', headline: 'Math check failed — workflow stopped for human review', detail: e.summary }
    case 'quote.math_confirmed':
      return { tone: 'emerald', icon: '✓', headline: "Human confirmed the engine's total — workflow resumed", detail: e.summary }
    case 'extraction.needs_human':
      return { tone: 'amber', icon: '?', headline: 'Low-confidence fields — human extraction required', detail: e.summary }
    case 'guardrail.injection_detected':
      return {
        tone: 'red',
        icon: <Shield />,
        headline: 'Prompt injection detected in supplier content — treated as data',
        detail: e.summary,
      }
    case 'negotiation.policy_blocked': {
      const violations = Array.isArray(p.violations) ? (p.violations as string[]) : []
      return {
        tone: 'red',
        icon: '⛔',
        headline: `Outbound message blocked: ${violations[0] ?? e.summary}`,
        detail: (
          <>
            <span className="text-zinc-400">{p.source === 'human_edit' ? 'Edited by human — ' : "Agent's draft — "}</span>
            {name(p.supplier_id)}
            {violations.length > 1 && <span className="text-zinc-400"> · {violations.length - 1} more violation(s)</span>}
          </>
        ),
      }
    }
    case 'supplier.counter_offer': {
      const offer = offerOf(p)
      return {
        tone: 'pink',
        icon: '↩',
        headline: offer
          ? `Counter-offer from ${name(p.supplier_id)}: ${offer.unit_price}/unit · ${offer.lead_time_days} d`
          : `${name(p.supplier_id)} declined to counter`,
        detail: str(p.reply_text) ? (
          <span className="block whitespace-pre-wrap break-words border-l border-pink-900 pl-2 italic text-zinc-400">{str(p.reply_text)}</span>
        ) : undefined,
      }
    }
    case 'requirement.changed': {
      const parts = changesOf(p).map(([f, c]) => `${CHANGE_LABEL[f] ?? f} ${fmtChange(f, c.before)} → ${fmtChange(f, c.after)}`)
      return {
        tone: 'red',
        icon: '⚡',
        headline: `Requirement changed: ${parts.join(', ') || e.summary} — replanning without restart`,
        detail: str(p.reason) ? `“${str(p.reason)}”` : undefined,
      }
    }
    case 'replan.completed': {
      const impact = p.impact as { recommended_before?: string | null; recommended_after?: string | null } | undefined
      const before = impact?.recommended_before ?? null
      const after = impact?.recommended_after ?? null
      const flipped = before !== after
      const label = (sid: string | null) => (sid ? name(sid) : 'none eligible')
      return {
        tone: flipped ? 'red' : 'zinc',
        icon: '⇄',
        headline: flipped ? `Recommendation changed: ${label(before)} → ${label(after)}` : `Recommendation unchanged: ${label(after)}`,
        detail: e.summary,
      }
    }
    case 'agent.failed': {
      const reason = str(p.reason)
      return {
        tone: 'amber',
        icon: '⚠',
        headline: reason && FAILED_HEADLINE[reason] && p.agent === 'decision' ? FAILED_HEADLINE[reason] : e.summary,
        detail: str(p.detail) ?? str(p.error) ?? undefined,
      }
    }
    case 'po.generated': {
      const po = (p.purchase_order as { po_number?: string } | undefined)?.po_number
      return { tone: 'emerald', icon: '✓', headline: `Purchase order ${po ?? ''} generated after human approval`, detail: e.summary }
    }
    default:
      return null
  }
}

function MomentCard({ event, moment }: { event: WorkflowEvent; moment: Moment }) {
  const [open, setOpen] = useState(false)
  const t = TONE[moment.tone]
  const a = ACTOR[event.actor]
  return (
    <li className={`rounded-md border px-3 py-2 ${t.card}`}>
      <div className="flex items-start gap-2.5">
        <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm font-bold ${t.icon}`}>{moment.icon}</span>
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-semibold leading-snug ${t.head}`}>{moment.headline}</p>
          {moment.detail && <div className="mt-0.5 break-words text-xs text-zinc-300">{moment.detail}</div>}
          <div className="mono mt-1 flex flex-wrap items-baseline gap-2 text-[10px] text-zinc-500">
            <span>#{event.seq}</span>
            <span>{time(event.ts)}</span>
            <span className={a.text}>{a.label}</span>
            <span>{event.type}</span>
            {event.state_before !== event.state_after && (
              <span>
                {event.state_before ?? '∅'} → <span className="text-zinc-300">{event.state_after}</span>
              </span>
            )}
            {Object.keys(event.payload).length > 0 && (
              <button onClick={() => setOpen(!open)} className="ml-auto hover:text-zinc-300">
                {open ? '▾ payload' : '▸ payload'}
              </button>
            )}
          </div>
          {open && <pre className="mono mt-1 max-h-64 overflow-auto rounded bg-black/50 p-2 text-[11px] leading-snug text-zinc-300">{JSON.stringify(event.payload, null, 2)}</pre>}
        </div>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------------------------------- ordinary rows

const GUARD_VERIFIED = 'guardrail.extraction_verified'

function probability(v: unknown): string | null {
  return typeof v === 'number' ? `p=${v.toFixed(2)}` : null
}

/** Negotiation events (T11) carry {supplier_id, round, offer} and get chips. */
function isNegotiation(type: string): boolean {
  return type.startsWith('negotiation.') || type === 'rescoring.started'
}

/** Startup recovery (T19): the backend restarted while this run was mid-transition and moved it to the
 *  nearest resting state; the human re-triggers evaluation. Amber = attention, not an error. */
const RECOVERED = 'run.recovered'

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

const TAG: Record<string, { text: string; cls: string }> = {
  'po.requested': { text: 'awaiting human approval', cls: 'bg-amber-900/50 text-amber-100' },
  'po.rejected': { text: 'PO rejected by human', cls: 'bg-red-900/50 text-red-100' },
  'po.discarded': { text: 'PO preview discarded', cls: 'bg-red-900/50 text-red-100' },
  'negotiation.discarded': { text: 'draft discarded', cls: 'bg-red-900/50 text-red-100' },
  [RECOVERED]: { text: 'backend restarted', cls: 'bg-amber-900/50 text-amber-100' },
}

function EventRow({ event }: { event: WorkflowEvent }) {
  const [open, setOpen] = useState(false)
  const a = ACTOR[event.actor]
  const transition = event.state_before !== event.state_after
  const lane = laneOf(event)
  const laneLabel = event.actor === 'agent' && lane ? LANE_BY_KEY[lane].short : lane === 'judge' ? 'judge' : a.label
  const backend = backendOf(event)
  const negotiation = isNegotiation(event.type)
  const offer = negotiation ? offerOf(event.payload) : null
  const verified = event.type === GUARD_VERIFIED
  const unsupported = verified && Array.isArray(event.payload.unsupported) ? (event.payload.unsupported as string[]) : []
  const guardOk = verified && unsupported.length === 0
  const replanStart = event.type === 'replan.started'
  const revisiting = replanStart && Array.isArray(event.payload.revisiting) ? (event.payload.revisiting as string[]) : []
  const tag = TAG[event.type]
  const fallback = event.type === 'agent.finished' && event.payload.fallback === true
  const accent = guardOk
    ? 'border-l-2 border-l-emerald-500'
    : verified
      ? 'border-l-2 border-l-red-500'
      : replanStart || event.type === 'po.rejected' || event.type === 'po.discarded' || event.type === 'negotiation.discarded'
        ? 'border-l-2 border-l-red-500'
        : event.type === 'po.requested' || event.type === RECOVERED
          ? 'border-l-2 border-l-amber-400'
          : negotiation
            ? 'border-l-2 border-l-amber-400/70'
            : ''
  return (
    <li className={`rounded border px-2 py-1 text-xs ${transition ? 'border-zinc-700 bg-zinc-900' : 'border-zinc-800/60 bg-zinc-950/40'} ${accent}`}>
      <div className="flex items-baseline gap-2 text-[10px]">
        <span className={`relative top-px inline-block h-1.5 w-1.5 shrink-0 rounded-full ${lane === 'judge' ? 'bg-emerald-400' : a.dot}`} />
        <span className="mono text-zinc-600">#{event.seq}</span>
        <span className="mono text-zinc-500">{time(event.ts)}</span>
        <span className={`font-semibold ${lane === 'judge' ? 'text-emerald-300' : a.text}`}>{laneLabel}</span>
        <span className="mono truncate text-zinc-500">{event.type}</span>
        {backend && <span className={`mono rounded px-1 ${backend.cls}`} title="LLM route that served this agent call">{backend.text}</span>}
        {fallback && (
          <span className="rounded bg-amber-900/50 px-1 text-amber-100" title="the LLM answer was rejected; deterministic text was used (G6)">
            fallback
          </span>
        )}
        {transition && (
          <span className="mono ml-auto shrink-0 text-zinc-500">
            {event.state_before ?? '∅'} → <span className="text-zinc-200">{event.state_after}</span>
          </span>
        )}
        {Object.keys(event.payload).length > 0 && (
          <button onClick={() => setOpen(!open)} className={`shrink-0 text-zinc-600 hover:text-zinc-300 ${transition ? '' : 'ml-auto'}`} title="payload">
            {open ? '▾' : '▸'}
          </button>
        )}
      </div>
      <p className="mt-0.5 break-words leading-snug text-zinc-200">
        {tag && <span className={`mr-1 rounded px-1 py-px text-[10px] ${tag.cls}`}>{tag.text}</span>}
        {verified && (
          <span className={`mr-1 inline-flex items-center gap-1 rounded px-1 py-px text-[10px] ${guardOk ? 'bg-emerald-900/50 text-emerald-100' : 'bg-red-900/50 text-red-100'}`}>
            <Shield /> judge {guardOk ? 'verified' : `unsupported: ${unsupported.join(', ')}`}
            {probability(event.payload.lowest_probability) && <span className="mono opacity-80">lowest {probability(event.payload.lowest_probability)}</span>}
          </span>
        )}
        {event.summary}
      </p>
      {replanStart && revisiting.length > 0 && (
        <ul className="mono mt-0.5 flex flex-wrap gap-1 text-[10px]">
          {revisiting.map((r) => (
            <li key={r} className="rounded bg-sky-900/50 px-1 py-px text-sky-100">
              {r.replace('_', ' ')}
            </li>
          ))}
        </ul>
      )}
      {negotiation && (offer || typeof event.payload.round === 'number') && (
        <div className="mono mt-0.5 flex flex-wrap gap-1 text-[10px]">
          {typeof event.payload.supplier_id === 'string' && <span className="rounded bg-zinc-800 px-1 py-px text-zinc-300">{event.payload.supplier_id}</span>}
          {typeof event.payload.round === 'number' && <span className="rounded bg-zinc-800 px-1 py-px text-zinc-300">round {event.payload.round}</span>}
          {offer && (
            <span className="rounded bg-amber-900/50 px-1 py-px text-amber-100">
              ask {offer.unit_price}/unit · {offer.lead_time_days} d
            </span>
          )}
          {typeof event.payload.verdict === 'string' && <span className="rounded bg-sky-900/50 px-1 py-px text-sky-100">verdict: {event.payload.verdict}</span>}
          {typeof event.payload.status === 'string' && <span className="rounded bg-emerald-900/50 px-1 py-px text-emerald-100">{event.payload.status}</span>}
          {event.payload.edited === true && <span className="rounded bg-amber-900/50 px-1 py-px text-amber-100">edited by human</span>}
        </div>
      )}
      {open && <pre className="mono mt-1 max-h-64 overflow-auto rounded bg-black/50 p-2 text-[11px] leading-snug text-zinc-300">{JSON.stringify(event.payload, null, 2)}</pre>}
    </li>
  )
}
