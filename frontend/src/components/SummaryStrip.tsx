import { Link } from 'react-router-dom'
import type { Health, RunOverview, WorkflowState } from '../api/types'
import { money } from '../format'
import { gateLabel } from '../labels'
import { StateBadge } from './StateBadge'
import { Shield } from './TimelinePanel'

/**
 * One glance under the header (T16): where the run is, what was asked, who is recommended, and — loudest of all —
 * whether the workflow is waiting for a human. Figures come from GET /runs/{id}/summary; the state is the live one.
 */
export function SummaryStrip({
  runId,
  overview,
  state,
  currency,
  health,
  onOpenGate,
}: {
  runId: string
  overview: RunOverview | null
  state: WorkflowState
  currency: string
  health: Health | null
  onOpenGate: () => void
}) {
  const o = overview
  return (
    <div className="flex items-center gap-3 overflow-hidden whitespace-nowrap border-b border-zinc-800 bg-zinc-900/40 px-4 py-1.5 text-sm">
      <Link to="/" className="shrink-0 text-xs text-zinc-400 hover:text-zinc-200" title={`run ${runId}`}>
        ← runs
      </Link>
      <StateBadge state={state} />
      {o && (
        <>
          <span className="flex shrink-0 items-baseline gap-1.5 text-zinc-300">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">Request</span>
            <span className="mono">{o.quantity.toLocaleString()}</span> × {o.product}
            <span
              className={`mono rounded px-1.5 text-xs font-semibold ${o.version > 1 ? 'bg-red-900/60 text-red-200' : 'bg-zinc-800 text-zinc-400'}`}
              title="request version (incremented by every requirement change)"
            >
              v{o.version}
            </span>
          </span>
          <span className="flex min-w-0 items-baseline gap-1.5 text-zinc-300">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">Recommended</span>
            {o.recommended_supplier_id ? (
              <>
                <span className="truncate font-semibold text-emerald-300" title={o.recommended_supplier_id}>
                  {o.recommended_name ?? o.recommended_supplier_id}
                </span>
                {o.total_score !== null && (
                  <span className="mono shrink-0 text-zinc-200" title="engine total score (0–100)">
                    {o.total_score.toFixed(1)}
                  </span>
                )}
                {o.landed_cost && (
                  <span className="mono shrink-0 text-zinc-400" title={`landed cost in ${currency}, incl. shipping, discount and tax`}>
                    {money(o.landed_cost)}
                  </span>
                )}
              </>
            ) : (
              <span className="text-zinc-500">{state === 'RECOMMENDED' ? 'none eligible' : 'not yet'}</span>
            )}
          </span>
          {o.po_number && (
            <span className="mono shrink-0 rounded bg-emerald-800/70 px-1.5 py-0.5 text-xs font-semibold text-emerald-50" title="purchase order number">
              {o.po_number}
            </span>
          )}
          {o.pending_human_kind && (
            <button
              type="button"
              onClick={onOpenGate}
              className="live-dot flex shrink-0 items-center gap-2 rounded-full border border-amber-400 bg-amber-500/20 px-3 py-1 text-xs font-bold uppercase tracking-wider text-amber-200 shadow-[0_0_14px_rgba(251,191,36,0.35)] hover:bg-amber-500/30"
              title="the workflow is stopped until a human acts — click to open the review"
            >
              <span className="inline-block h-2 w-2 rounded-full bg-amber-300" />
              Waiting for human: {gateLabel(o.pending_human_kind)}
            </button>
          )}
        </>
      )}
      <span className="ml-auto flex shrink-0 items-center gap-4">
        {o && (
          <span className="mono hidden text-[10px] text-zinc-600 2xl:inline" title="documents · quotes · negotiations · events">
            {o.counts.documents} docs · {o.counts.quotes} quotes · {o.counts.negotiations} threads · {o.counts.events} events
          </span>
        )}
        {health && (
          <span
            className="mono text-xs text-zinc-500"
            title={`${health.mode === 'mock' ? 'MODE=mock: deterministic mock agents, no LLM calls. ' : ''}${
              health.llm_backend === 'openclaw' ? `OpenClaw gateway ${health.openclaw}; direct gateway is the fallback` : 'organiser LLM gateway, called directly'
            }`}
          >
            {health.mode === 'mock' && <span className="mr-1.5 rounded bg-zinc-800 px-1 text-[10px] uppercase text-zinc-400">mock</span>}
            LLM via{' '}
            <span className={health.llm_backend === 'openclaw' ? (health.openclaw === 'reachable' ? 'text-emerald-300' : 'text-red-300') : 'text-zinc-300'}>
              {health.llm_backend === 'openclaw' ? `OpenClaw (${health.openclaw})` : 'gateway'}
            </span>
          </span>
        )}
        {health && (
          <span className="mono flex items-center gap-1 text-xs text-zinc-500" title={health.judge_model ? `model ${health.judge_model}` : 'deterministic mock judge'}>
            <Shield className={health.guardrail_judge === 'jev' ? 'text-emerald-400' : 'text-zinc-500'} />
            Judge: <span className={health.guardrail_judge === 'jev' ? 'text-emerald-300' : 'text-zinc-300'}>{health.guardrail_judge === 'jev' ? 'TypeSafe Jev' : 'mock'}</span>
          </span>
        )}
      </span>
    </div>
  )
}
