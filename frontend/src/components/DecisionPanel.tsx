import { useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { NegotiationOffer, NegotiationStatus, NegotiationThread, NegotiationTurn, PurchaseOrder, ReplanImpact, Run, Scorecard, ScoringWeights, WorkflowEvent } from '../api/types'
import { money, time } from '../format'
import { ComparisonTable } from './ComparisonTable'
import { Button, ErrorLine, Panel } from './Panel'

const STATUS: Record<NegotiationStatus, string> = {
  open: 'bg-amber-900/70 text-amber-200 ring-1 ring-amber-500',
  accepted: 'bg-emerald-900/70 text-emerald-200 ring-1 ring-emerald-500',
  rejected: 'bg-red-900/70 text-red-200',
  escalated: 'bg-red-900/70 text-red-200 ring-1 ring-red-500',
  closed: 'bg-zinc-800 text-zinc-300',
}

export function DecisionPanel({ run, onRun, events }: { run: Run; onRun: (r: Run) => void; events: WorkflowEvent[] }) {
  const names = Object.fromEntries(run.quotes.map((q) => [q.supplier_id, q.supplier_name]))
  const quotes = Object.fromEntries(run.quotes.map((q) => [q.supplier_id, q]))
  const rec = run.recommendation
  const threads = Object.values(run.negotiations)
  const [busy, setBusy] = useState<'negotiate' | 'request-po' | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Decision = ranking, rationale and actions; Compare = the engine-only matrix (T17), usable with the LLM down.
  const [tab, setTab] = useState<'decision' | 'compare'>('decision')
  const generated = run.state === 'PO_GENERATED'
  const topCard = rec?.recommended_supplier_id ? run.scorecards.find((c) => c.supplier_id === rec.recommended_supplier_id) : undefined
  const canRequestPo = run.state === 'RECOMMENDED' && !!topCard?.eligible && run.config.approvals.po_generation

  // Scorecards from the previous scoring pass (quotes.scored events) → before/after under "What changed".
  const previous = useMemo(() => {
    const scored = events.filter((e) => e.type === 'quotes.scored' && Array.isArray(e.payload.scorecards))
    if (scored.length < 2) return null
    return Object.fromEntries((scored[scored.length - 2].payload.scorecards as Scorecard[]).map((c) => [c.supplier_id, c]))
  }, [events])

  const call = async (label: 'negotiate' | 'request-po', fn: () => Promise<Run>) => {
    setBusy(label)
    setError(null)
    try {
      onRun(await fn())
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(null)
    }
  }

  const tabs = (
    <div className="flex gap-1 text-[11px]">
      {(['decision', 'compare'] as const).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => setTab(t)}
          className={`rounded px-2 py-0.5 font-medium uppercase tracking-wider transition-colors ${tab === t ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
          title={t === 'compare' ? 'every validated figure side by side — engine numbers only, no AI text' : 'ranking, rationale and actions'}
        >
          {t}
        </button>
      ))}
    </div>
  )
  if (tab === 'compare') {
    return (
      <Panel title="Decision" right={tabs}>
        <ComparisonTable runId={run.run_id} updatedAt={run.updated_at} />
      </Panel>
    )
  }

  return (
    <Panel title="Decision" right={tabs}>
      {run.replan_impact && <ReplanBanner impact={run.replan_impact} names={names} />}
      {generated && run.purchase_order && <PurchaseOrderCard po={run.purchase_order} runId={run.run_id} />}
      {run.replan_impact && <ReplanImpactCard impact={run.replan_impact} names={names} explanation={rec?.change_explanation ?? null} />}

      {run.scorecards.length === 0 ? (
        <p className="text-sm text-zinc-500">No scorecards yet. Upload quotes and evaluate.</p>
      ) : (
        <ol className="space-y-2">
          {run.scorecards.map((c, i) => {
            const top = rec?.recommended_supplier_id === c.supplier_id
            const negotiated = quotes[c.supplier_id]?.negotiated_offer
            return (
              <li key={c.supplier_id} className={`rounded border p-3 text-sm ${top ? 'border-emerald-700 bg-emerald-950/40' : 'border-zinc-800 bg-zinc-950/40'}`}>
                <div className="flex items-baseline gap-2">
                  <span className="mono text-zinc-500">#{i + 1}</span>
                  <span className={`truncate ${top ? 'font-semibold text-emerald-300' : ''}`}>{names[c.supplier_id] ?? c.supplier_id}</span>
                  <span className="mono text-xs text-zinc-500">{c.supplier_id}</span>
                  {top && <span className="rounded bg-emerald-800/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-100">recommended</span>}
                  <span className="mono ml-auto text-lg" title="total score (0–100)">
                    {c.total_score.toFixed(1)}
                  </span>
                </div>
                <div className="mono mt-1 flex gap-4 text-xs text-zinc-400">
                  <span>
                    landed <span className="text-zinc-200">{money(c.landed_cost)}</span>
                  </span>
                  <span>
                    lead <span className="text-zinc-200">{c.lead_time_days} d</span>
                  </span>
                  <span className="ml-auto">{c.eligible ? 'eligible' : <span className="text-red-300">ineligible</span>}</span>
                </div>
                {negotiated && (
                  <div className="mono mt-1 text-xs text-amber-300" title="costed with the negotiated offer; the quoted figures are kept for audit">
                    negotiated {negotiated.unit_price}/unit · {negotiated.lead_time_days} d (quoted {quotes[c.supplier_id].unit_price}/unit · {quotes[c.supplier_id].lead_time_days} d)
                  </div>
                )}
                {c.eligible ? (
                  <ScoreBars breakdown={c.score_breakdown} weights={run.config.weights} />
                ) : (
                  <div className="mt-1 text-xs text-red-300">{c.ineligibility_reasons.join('; ')}</div>
                )}
              </li>
            )
          })}
        </ol>
      )}

      {rec && (
        <div className="mt-4 space-y-3 text-sm">
          <div>
            <h3 className="text-xs uppercase tracking-wider text-zinc-500">Recommendation</h3>
            <p className="mt-1 text-emerald-200">
              {rec.recommended_supplier_id ? (
                <>
                  {names[rec.recommended_supplier_id] ?? rec.recommended_supplier_id} <span className="mono text-xs text-zinc-500">{rec.recommended_supplier_id}</span>
                </>
              ) : (
                'No eligible supplier'
              )}
            </p>
          </div>
          <div>
            <h3 className="text-xs uppercase tracking-wider text-zinc-500">Rationale</h3>
            <p className="mt-1 leading-relaxed text-zinc-200">{rec.rationale}</p>
          </div>
          {rec.change_explanation && !run.replan_impact && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-zinc-500">What changed</h3>
              <p className="mt-1 leading-relaxed text-amber-100">{rec.change_explanation}</p>
              <table className="mono mt-2 w-full text-xs">
                <thead className="text-[10px] uppercase text-zinc-500">
                  <tr>
                    <th className="text-left font-normal">supplier</th>
                    <th className="text-right font-normal">landed {previous ? 'before → after' : ''}</th>
                    <th className="text-right font-normal">score {previous ? 'before → after' : ''}</th>
                  </tr>
                </thead>
                <tbody>
                  {run.scorecards.map((c) => {
                    const b = previous?.[c.supplier_id]
                    const cheaper = b && b.landed_cost !== c.landed_cost
                    return (
                      <tr key={c.supplier_id} className="border-t border-zinc-800/60">
                        <td className="py-0.5 text-zinc-300">{names[c.supplier_id] ?? c.supplier_id}</td>
                        <td className="py-0.5 text-right">
                          {b && <span className="text-zinc-500">{money(b.landed_cost)} → </span>}
                          <span className={cheaper ? 'text-emerald-300' : 'text-zinc-200'}>{money(c.landed_cost)}</span>
                        </td>
                        <td className="py-0.5 text-right">
                          {b && <span className="text-zinc-500">{b.total_score.toFixed(1)} → </span>}
                          <span className="text-zinc-200">{c.total_score.toFixed(1)}</span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {rec.escalation && (
            <p className="rounded border border-amber-800 bg-amber-950/50 p-2 text-amber-200">
              Escalation: {rec.escalation.reason.replace(/_/g, ' ')} — a human must decide (relax the requirement, add quotes, or re-negotiate).
            </p>
          )}
        </div>
      )}

      {rec && canRequestPo && (
        <div className="mt-4 rounded border border-emerald-800/70 bg-emerald-950/30 p-3 text-sm">
          <h3 className="text-xs uppercase tracking-wider text-emerald-400">Purchase order</h3>
          <p className="mt-1 text-zinc-400">
            Raise a purchase order for {names[rec.recommended_supplier_id!] ?? rec.recommended_supplier_id} at the engine's landed cost{' '}
            <span className="mono text-zinc-200">{money(topCard!.landed_cost, run.request.currency)}</span>. You will review the numbers and approve before anything is generated —
            no agent can do this step.
          </p>
          <div className="mt-2">
            <Button disabled={busy !== null} onClick={() => void call('request-po', () => api.requestPo(run.run_id))}>
              {busy === 'request-po' ? 'Preparing…' : 'Request purchase order'}
            </Button>
          </div>
        </div>
      )}

      {rec && run.state === 'RECOMMENDED' && threads.length === 0 && (
        <div className="mt-4 rounded border border-zinc-800 bg-zinc-950/40 p-3 text-sm">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500">Negotiation</h3>
          <p className="mt-1 text-zinc-400">
            The Negotiation Agent will draft a message to the top {run.config.negotiation.negotiate_top_n} eligible suppliers, one at a time, up to{' '}
            {run.config.negotiation.max_rounds} rounds each. Nothing is sent without your approval.
          </p>
          <div className="mt-2">
            <Button tone={canRequestPo ? 'ghost' : 'primary'} disabled={busy !== null} onClick={() => void call('negotiate', () => api.negotiate(run.run_id))}>
              {busy === 'negotiate' ? 'Drafting…' : 'Start negotiation'}
            </Button>
          </div>
        </div>
      )}
      <ErrorLine error={error} />

      {threads.length > 0 && (
        <div className="mt-4 space-y-3">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500">Negotiation</h3>
          {threads.map((t) => (
            <ThreadCard key={t.supplier_id} thread={t} name={names[t.supplier_id] ?? t.supplier_id} />
          ))}
        </div>
      )}
    </Panel>
  )
}

/** The generated purchase order (PO_GENERATED): every figure is the engine's; the document exists only because a
 *  human clicked "Approve Final Supplier & Generate PO" (G5). */
function PurchaseOrderCard({ po, runId }: { po: PurchaseOrder; runId: string }) {
  const line = po.line_items[0]
  const cur = po.currency
  return (
    <div className="mb-4 rounded border border-emerald-600 bg-emerald-950/40 p-3 text-sm ring-1 ring-emerald-500/40">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-emerald-300">Purchase order generated</h3>
        <span className="mono text-[10px] text-zinc-500">request v{po.request_version}</span>
      </div>
      <div className="mono mt-1 text-lg font-semibold text-emerald-100">{po.po_number}</div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt className="text-zinc-500">Supplier</dt>
        <dd className="text-zinc-100">
          {po.supplier.name} <span className="mono text-zinc-500">{po.supplier.supplier_id}</span>
        </dd>
        <dt className="text-zinc-500">Line</dt>
        <dd className="mono text-zinc-100">
          {line.quantity.toLocaleString()} × {line.description} @ {line.unit_price} {cur}
          {po.negotiated && <span className="ml-1.5 rounded bg-amber-900/60 px-1 py-0.5 text-[10px] text-amber-200">negotiated</span>}
        </dd>
        <dt className="text-zinc-500">Lead time</dt>
        <dd className="mono text-zinc-100">{po.lead_time_days} d</dd>
        {po.payment_terms && (
          <>
            <dt className="text-zinc-500">Payment</dt>
            <dd className="text-zinc-100">{po.payment_terms}</dd>
          </>
        )}
      </dl>
      <TotalsTable totals={po.totals} currency={cur} />
      <p className="mono mt-2 text-[11px] text-emerald-300/90">
        ✓ approved by {po.approved_by} at {po.approved_at ? time(po.approved_at) : '—'}
      </p>
      <a
        href={api.poPdfUrl(runId)}
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-block rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
      >
        Download PO (PDF)
      </a>
    </div>
  )
}

/** Engine totals block shared by the PO gate and the PO card. */
export function TotalsTable({ totals, currency }: { totals: PurchaseOrder['totals']; currency: string }) {
  const rows: [string, string, boolean][] = [
    ['Subtotal', totals.subtotal, false],
    ['Discount', totals.discount, false],
    ['Shipping', totals.shipping, false],
    ['Tax', totals.tax, false],
    ['Total (landed cost)', totals.total, true],
  ]
  return (
    <table className="mono mt-2 w-full text-xs">
      <tbody>
        {rows.map(([label, value, total]) => (
          <tr key={label} className={total ? 'border-t border-emerald-700 font-semibold text-emerald-100' : 'text-zinc-300'}>
            <td className="py-0.5 text-zinc-500">{total ? <span className="text-emerald-200">{label}</span> : label}</td>
            <td className="py-0.5 text-right">{total ? money(value, currency) : money(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const FIELD_LABEL: Record<string, string> = { quantity: 'Quantity', budget: 'Budget', required_by: 'Required by' }

function changeValue(field: string, v: unknown): string {
  if (typeof v === 'number') return v.toLocaleString()
  if (field === 'budget' && typeof v === 'string') return money(v)
  return String(v)
}

function supplierLabel(names: Record<string, string>, sid: string | null): string {
  return sid ? (names[sid] ?? sid) : 'none eligible'
}

/** The one line a viewer must see first after an interrupt: did the recommendation move? Sits at the very top of
 *  the panel, above the PO card and the impact table. */
function ReplanBanner({ impact, names }: { impact: ReplanImpact; names: Record<string, string> }) {
  const flipped = impact.recommended_before !== impact.recommended_after
  return (
    <div
      className={`mb-3 rounded-md px-3 py-2 text-sm font-semibold ${flipped ? 'bg-red-900/50 text-red-100 ring-1 ring-red-500' : 'bg-zinc-800/80 text-zinc-200 ring-1 ring-zinc-600'}`}
      title={`request v${impact.from_version} → v${impact.to_version}`}
    >
      <div className="text-[10px] font-normal uppercase tracking-wider opacity-80">After the requirement change · v{impact.from_version} → v{impact.to_version}</div>
      {flipped ? (
        <>
          Recommendation changed: {supplierLabel(names, impact.recommended_before)} → {supplierLabel(names, impact.recommended_after)}
        </>
      ) : (
        <>Recommendation unchanged: {supplierLabel(names, impact.recommended_after)}</>
      )}
    </div>
  )
}

const DIMENSION: { key: keyof ScoringWeights; label: string }[] = [
  { key: 'price', label: 'Price' },
  { key: 'lead_time', label: 'Lead time' },
  { key: 'reliability', label: 'Reliability' },
  { key: 'risk', label: 'Risk' },
]

/** score_breakdown holds weight × dimension score (0–100) and sums to the total; each bar shows the dimension
 *  score as a share of what that dimension could contribute at most, with the contribution as the number. */
function ScoreBars({ breakdown, weights }: { breakdown: Record<string, number>; weights: ScoringWeights }) {
  return (
    <div className="mt-1.5 grid grid-cols-4 gap-2">
      {DIMENSION.map(({ key, label }) => {
        const max = weights[key] * 100
        const value = breakdown[key] ?? 0
        const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
        return (
          <div key={key} title={`${label}: ${value.toFixed(1)} of ${max.toFixed(0)} (weight ${weights[key]})`}>
            <div className="flex items-baseline justify-between text-[10px]">
              <span className="text-zinc-500">{label}</span>
              <span className="mono text-zinc-300">{value.toFixed(1)}</span>
            </div>
            <div className="mt-0.5 h-1.5 rounded bg-zinc-800">
              <div className="h-1.5 rounded bg-emerald-500/80" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Structured outcome of an interrupt (D22): what the human changed, what it did to every supplier, and why the
 *  recommendation moved. Numbers are the engine's; the explanation is the Decision Agent's summary of the diff. */
function ReplanImpactCard({ impact, names, explanation }: { impact: ReplanImpact; names: Record<string, string>; explanation: string | null }) {
  return (
    <div className="mb-4 rounded border border-red-800/70 bg-red-950/20 p-3 text-sm">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-red-300">Replan impact</h3>
        <span className="mono text-[10px] text-zinc-500">
          request v{impact.from_version} → v{impact.to_version}
        </span>
      </div>
      <ul className="mono mt-2 space-y-0.5 text-xs">
        {Object.entries(impact.changes).map(([field, c]) => (
          <li key={field}>
            <span className="text-zinc-500">{FIELD_LABEL[field] ?? field}</span> <span className="text-zinc-400">{changeValue(field, c.before)}</span>
            <span className="text-zinc-500"> → </span>
            <span className="font-semibold text-red-200">{changeValue(field, c.after)}</span>
          </li>
        ))}
      </ul>
      {/* before → after per supplier; "before" values sit on their own line so the table fits the 420px column */}
      <table className="mono mt-2 w-full table-fixed text-xs">
        <colgroup>
          <col className="w-[34%]" />
          <col className="w-[18%]" />
          <col className="w-[30%]" />
          <col className="w-[18%]" />
        </colgroup>
        <thead className="text-[10px] uppercase text-zinc-500">
          <tr>
            <th className="text-left font-normal">supplier</th>
            <th className="text-left font-normal">eligible</th>
            <th className="text-right font-normal">landed</th>
            <th className="text-right font-normal">score</th>
          </tr>
        </thead>
        <tbody>
          {impact.per_supplier.map((s) => {
            const lost = s.eligible_before && !s.eligible_after
            const gained = !s.eligible_before && s.eligible_after
            return (
              <tr key={s.supplier_id} className="border-t border-zinc-800/60 align-top">
                <td className="py-1 pr-1 text-zinc-300">
                  <div className="truncate" title={names[s.supplier_id] ?? s.supplier_id}>
                    {names[s.supplier_id] ?? s.supplier_id}
                  </div>
                  <div className="text-[10px] text-zinc-600">{s.supplier_id}</div>
                </td>
                <td className="py-1">
                  <div className="text-zinc-500">{s.eligible_before ? 'yes' : 'no'} →</div>
                  <div className={lost ? 'font-semibold text-red-300' : gained ? 'font-semibold text-emerald-300' : 'text-zinc-200'}>{s.eligible_after ? 'yes' : 'no'}</div>
                </td>
                <td className="py-1 text-right">
                  <div className="text-zinc-500">{money(s.landed_before)} →</div>
                  <div className="text-zinc-200">{money(s.landed_after)}</div>
                </td>
                <td className="py-1 text-right">
                  <div className="text-zinc-500">{s.score_before.toFixed(1)} →</div>
                  <div className={s.score_after > s.score_before ? 'text-emerald-300' : s.score_after < s.score_before ? 'text-red-300' : 'text-zinc-200'}>{s.score_after.toFixed(1)}</div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {impact.per_supplier.some((s) => s.reasons.length > 0) && (
        <ul className="mt-1.5 space-y-0.5 text-[11px]">
          {impact.per_supplier
            .filter((s) => s.reasons.length > 0)
            .map((s) => (
              <li key={s.supplier_id} className={s.eligible_before && !s.eligible_after ? 'text-red-300' : 'text-zinc-400'}>
                {names[s.supplier_id] ?? s.supplier_id}: {s.reasons.join('; ')}
              </li>
            ))}
        </ul>
      )}
      {explanation && (
        <div className="mt-2">
          <h4 className="text-[10px] uppercase tracking-wider text-zinc-500">Change explanation</h4>
          <p className="mt-0.5 leading-relaxed text-amber-100">{explanation}</p>
        </div>
      )}
    </div>
  )
}

function offerText(o: NegotiationOffer | null | undefined): string {
  return o ? `${o.unit_price}/unit · ${o.lead_time_days} d` : '—'
}

function ThreadCard({ thread, name }: { thread: NegotiationThread; name: string }) {
  const improved =
    thread.original_offer && thread.current_offer && (thread.current_offer.unit_price !== thread.original_offer.unit_price || thread.current_offer.lead_time_days !== thread.original_offer.lead_time_days)
  return (
    <div className="rounded border border-zinc-800 bg-zinc-950/40 p-3 text-sm">
      <div className="flex items-baseline gap-2">
        <span className="truncate font-medium">{name}</span>
        <span className="mono text-xs text-zinc-500">{thread.supplier_id}</span>
        <span className={`mono ml-auto rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${STATUS[thread.status]}`}>{thread.status}</span>
      </div>
      <div className="mono mt-1.5 flex gap-4 text-xs text-zinc-400">
        <span>
          original <span className="text-zinc-200">{offerText(thread.original_offer)}</span>
        </span>
        <span>
          current <span className={improved ? 'text-emerald-300' : 'text-zinc-200'}>{offerText(thread.current_offer)}</span>
        </span>
        <span className="ml-auto">
          {thread.turns.filter((t) => t.role === 'buyer').length}/{thread.boundaries.max_rounds} rounds
        </span>
      </div>
      <ol className="mt-2 space-y-1.5">
        {thread.turns.map((t, i) => (
          <TurnBubble key={i} turn={t} />
        ))}
        {thread.turns.length === 0 && <li className="text-xs text-zinc-500">No message sent yet.</li>}
      </ol>
    </div>
  )
}

function TurnBubble({ turn }: { turn: NegotiationTurn }) {
  const buyer = turn.role === 'buyer'
  return (
    <li className={`flex ${buyer ? 'justify-start' : 'justify-end'}`}>
      <div className={`max-w-[88%] rounded-lg border px-3 py-2 ${buyer ? 'border-amber-900/60 bg-amber-950/30' : 'border-pink-900/60 bg-pink-950/30'}`}>
        <div className="flex items-baseline gap-2 text-[10px] uppercase tracking-wide">
          <span className={buyer ? 'text-amber-300' : 'text-pink-300'}>{buyer ? 'buyer' : 'supplier'}</span>
          <span className="mono normal-case text-zinc-500">{time(turn.ts)}</span>
          {buyer && turn.approved_by_human && (
            <span className="mono normal-case text-emerald-400" title="sent only after a human clicked Approve & Send (G5)">
              ✓ approved by human
            </span>
          )}
        </div>
        {/* Supplier text is untrusted (G4): rendered as escaped plain text only, never HTML/markdown. */}
        <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-200">{turn.message}</p>
        {turn.offer && (
          <div className={`mono mt-1 inline-block rounded px-1.5 py-0.5 text-[11px] ${buyer ? 'bg-amber-900/50 text-amber-100' : 'bg-pink-900/50 text-pink-100'}`}>
            {buyer ? 'ask' : 'offer'} {offerText(turn.offer)}
          </div>
        )}
      </div>
    </li>
  )
}
