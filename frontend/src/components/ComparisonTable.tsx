import { useEffect, useState, type ReactNode } from 'react'
import { api, ApiError } from '../api/client'
import type { Comparison, ComparisonRow, QuoteChecks, ScoringWeights } from '../api/types'
import { money } from '../format'

/**
 * Manual comparison matrix (T17, PLAN.md G6): suppliers as columns, one row per figure the engine validated and
 * scored, from GET /runs/{id}/comparison. Contains no AI-generated text, so it is the view a buyer works from when
 * the LLM is down: checks as ✓/✗ (issues in the tooltip), negotiated values marked, ineligible columns dimmed,
 * the best value of every numeric row highlighted.
 */
export function ComparisonTable({ runId, updatedAt }: { runId: string; updatedAt: string }) {
  const [data, setData] = useState<Comparison | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  useEffect(() => {
    let cancelled = false
    api
      .getComparison(runId)
      .then((c) => {
        if (cancelled) return
        setData(c)
        setError(null)
      })
      .catch((e: ApiError) => {
        if (cancelled) return
        setError(e)
        if (e.status === 409) setData(null)
      })
    return () => {
      cancelled = true
    }
  }, [runId, updatedAt])

  if (error?.status === 409 || (!data && !error)) {
    return <p className="text-sm text-zinc-500">{error ? 'Available once the engine has validated the quotes: upload the documents and evaluate.' : 'Loading comparison…'}</p>
  }
  if (!data) return <p className="rounded border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-200">{error?.message}</p>
  const quotes = data.quotes
  if (quotes.length === 0) return <p className="text-sm text-zinc-500">No validated quotes.</p>
  const scored = quotes.some((q) => q.total_score !== null)
  const rows = buildRows(data.weights, scored)

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-zinc-500">
        Engine figures at <span className="mono text-zinc-300">{data.quantity.toLocaleString()}</span> units, budget{' '}
        <span className="mono text-zinc-300">{money(data.budget, data.currency)}</span>, due <span className="mono text-zinc-300">{data.required_by}</span> (request v{data.request_version}).
        Deterministic: nothing here came from a language model.
      </p>
      <div className="overflow-x-auto">
        <table className="mono w-full min-w-[340px] table-fixed border-collapse text-[11px]">
          <colgroup>
            <col className="w-[104px]" />
            {quotes.map((q) => (
              <col key={q.supplier_id} />
            ))}
          </colgroup>
          <thead>
            <tr className="align-bottom">
              <th className="pb-1 text-left text-[10px] font-normal uppercase tracking-wider text-zinc-500">figure</th>
              {quotes.map((q, i) => {
                const top = data.recommended_supplier_id === q.supplier_id
                const ineligible = q.eligible === false
                return (
                  <th key={q.supplier_id} className={`px-1 pb-1 text-right font-normal ${ineligible ? 'opacity-50' : ''}`} title={ineligible ? q.ineligibility_reasons.join('; ') : q.supplier_id}>
                    <div className={`truncate text-xs ${top ? 'font-semibold text-emerald-300' : 'text-zinc-200'}`} title={q.supplier_name}>
                      {scored && <span className="mr-1 text-zinc-500">#{i + 1}</span>}
                      {q.supplier_name}
                    </div>
                    <div className="truncate text-[10px] text-zinc-500">
                      {q.supplier_id}
                      {top && <span className="ml-1 rounded bg-emerald-800/70 px-1 text-[9px] font-semibold uppercase text-emerald-100">rec.</span>}
                      {ineligible && <span className="ml-1 rounded bg-red-900/70 px-1 text-[9px] font-semibold uppercase text-red-200">ineligible</span>}
                    </div>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              if (row.kind === 'section') {
                return (
                  <tr key={row.label}>
                    <td colSpan={quotes.length + 1} className="pt-2 pb-0.5 text-[9px] uppercase tracking-widest text-zinc-600">
                      {row.label}
                    </td>
                  </tr>
                )
              }
              const best = bestOf(row, quotes)
              return (
                <tr key={row.label} className={`border-t border-zinc-800/60 ${row.emphasis ? 'bg-zinc-900/60' : ''}`}>
                  <td className="py-0.5 pr-1 text-zinc-500" title={row.title}>
                    {row.label}
                  </td>
                  {quotes.map((q) => (
                    <td key={q.supplier_id} className={`px-1 py-0.5 text-right ${q.eligible === false ? 'opacity-50' : ''} ${best.has(q.supplier_id) ? 'text-emerald-300' : 'text-zinc-200'} ${row.emphasis ? 'font-semibold' : ''}`}>
                      {row.render(q)}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-zinc-600">
        <span className="text-emerald-300">green</span> = best value in the row{scored ? ' among eligible suppliers' : ''}; <span className="rounded bg-amber-900/60 px-1 text-amber-200">neg.</span> = costed with the negotiated
        offer (quoted value in the tooltip); ✗ tooltips list the engine's issues.
      </p>
    </div>
  )
}

type Row =
  | { kind: 'section'; label: string }
  | {
      kind: 'value'
      label: string
      title?: string
      /** Which way is better for the highlight; undefined = no highlight (text, neutral quantities). */
      best?: 'min' | 'max'
      value: (q: ComparisonRow) => number | null
      render: (q: ComparisonRow) => ReactNode
      emphasis?: boolean
    }

const num = (s: string | null | undefined): number | null => (s == null || s === '' ? null : Number(s))
const pct = (r: number | null) => (r === null ? '—' : `${(r * 100).toFixed(1)}%`)
const dash = (v: ReactNode) => v ?? '—'

function Negotiated({ value, quoted }: { value: ReactNode; quoted: string }) {
  return (
    <span title={`quoted ${quoted}`}>
      {value} <span className="rounded bg-amber-900/60 px-1 text-[9px] text-amber-200">neg.</span>
    </span>
  )
}

function Check({ ok, issues }: { ok: boolean; issues: string[] }) {
  return (
    <span className={ok ? 'text-emerald-300' : 'text-red-300'} title={ok ? 'passed' : issues.join('\n') || 'failed'}>
      {ok ? '✓' : '✗'}
    </span>
  )
}

const CHECKS: { key: keyof QuoteChecks; label: string; title: string }[] = [
  { key: 'moq_ok', label: 'MOQ ok', title: 'request quantity ≥ minimum order quantity' },
  { key: 'capacity_ok', label: 'capacity ok', title: 'quoted capacity covers the request quantity' },
  { key: 'lead_time_ok', label: 'lead time ok', title: 'lead time within the delivery window' },
  { key: 'budget_ok', label: 'budget ok', title: 'landed cost within budget' },
  { key: 'math_ok', label: 'math ok', title: 'printed total matches the engine (G1)' },
]

const DIMENSIONS: { key: keyof ScoringWeights; label: string }[] = [
  { key: 'price', label: 'price' },
  { key: 'lead_time', label: 'lead time' },
  { key: 'reliability', label: 'reliability' },
  { key: 'risk', label: 'risk' },
]

function buildRows(weights: ScoringWeights, scored: boolean): Row[] {
  const effectivePrice = (q: ComparisonRow) => (q.negotiated && q.negotiated_offer ? q.negotiated_offer.unit_price : q.unit_price)
  const effectiveLead = (q: ComparisonRow) => (q.negotiated && q.negotiated_offer ? q.negotiated_offer.lead_time_days : q.lead_time_days)
  const rows: Row[] = [
    { kind: 'section', label: 'quote' },
    {
      kind: 'value',
      label: 'unit price',
      best: 'min',
      value: (q) => num(effectivePrice(q)),
      render: (q) => (q.negotiated && q.negotiated_offer ? <Negotiated value={q.negotiated_offer.unit_price} quoted={q.unit_price} /> : q.unit_price),
    },
    { kind: 'value', label: 'currency', value: () => null, render: (q) => q.currency },
    { kind: 'value', label: 'qty quoted', value: () => null, render: (q) => q.quantity_quoted.toLocaleString() },
    { kind: 'value', label: 'MOQ', value: () => null, render: (q) => q.moq.toLocaleString() },
    {
      kind: 'value',
      label: 'lead time',
      best: 'min',
      value: (q) => effectiveLead(q),
      render: (q) => (q.negotiated && q.negotiated_offer ? <Negotiated value={`${q.negotiated_offer.lead_time_days} d`} quoted={`${q.lead_time_days} d`} /> : `${q.lead_time_days} d`),
    },
    { kind: 'value', label: 'shipping', best: 'min', value: (q) => num(q.shipping_cost), render: (q) => money(q.shipping_cost) },
    { kind: 'value', label: 'discount', best: 'max', value: (q) => num(q.discount_pct), render: (q) => `${q.discount_pct}%` },
    { kind: 'value', label: 'payment', value: () => null, render: (q) => <span className="block truncate" title={q.payment_terms ?? undefined}>{dash(q.payment_terms)}</span> },
    { kind: 'value', label: 'capacity', best: 'max', value: (q) => q.capacity_units, render: (q) => dash(q.capacity_units?.toLocaleString()) },
    { kind: 'section', label: 'engine costing (request quantity)' },
    { kind: 'value', label: 'subtotal', best: 'min', value: (q) => num(q.subtotal), render: (q) => money(q.subtotal) },
    { kind: 'value', label: 'discount', best: 'max', value: (q) => num(q.discount), render: (q) => money(q.discount) },
    { kind: 'value', label: 'pre-tax', best: 'min', value: (q) => num(q.pre_tax_total), render: (q) => money(q.pre_tax_total) },
    { kind: 'value', label: 'tax', best: 'min', value: (q) => num(q.tax), render: (q) => money(q.tax) },
    { kind: 'value', label: 'landed cost', best: 'min', emphasis: true, title: 'subtotal − discount + shipping + tax, in the request currency', value: (q) => num(q.landed_cost), render: (q) => money(q.landed_cost) },
    { kind: 'section', label: 'checks' },
    ...CHECKS.map<Row>((c) => ({ kind: 'value', label: c.label, title: c.title, value: () => null, render: (q) => <Check ok={q.checks[c.key]} issues={q.issues} /> })),
    { kind: 'section', label: 'supplier history' },
    { kind: 'value', label: 'on-time', best: 'max', value: (q) => q.on_time_rate, render: (q) => pct(q.on_time_rate) },
    { kind: 'value', label: 'defects', best: 'min', value: (q) => q.defect_rate, render: (q) => pct(q.defect_rate) },
  ]
  if (scored) {
    rows.push(
      { kind: 'section', label: 'score (0–100)' },
      {
        kind: 'value',
        label: 'eligible',
        value: () => null,
        render: (q) => (q.eligible === null ? '—' : <Check ok={q.eligible} issues={q.ineligibility_reasons} />),
      },
      ...DIMENSIONS.map<Row>((d) => ({
        kind: 'value',
        label: d.label,
        title: `weight ${weights[d.key]} → up to ${(weights[d.key] * 100).toFixed(0)} points`,
        best: 'max',
        value: (q) => q.score_breakdown?.[d.key] ?? null,
        render: (q) => (q.score_breakdown ? (q.score_breakdown[d.key] ?? 0).toFixed(1) : '—'),
      })),
      { kind: 'value', label: 'total score', best: 'max', emphasis: true, value: (q) => q.total_score, render: (q) => (q.total_score === null ? '—' : q.total_score.toFixed(1)) },
    )
  }
  return rows
}

/** supplier_ids holding the row's best value — among eligible suppliers once scored, all of them before. */
function bestOf(row: Row, quotes: ComparisonRow[]): Set<string> {
  const out = new Set<string>()
  if (row.kind !== 'value' || !row.best) return out
  const pool = quotes.some((q) => q.eligible !== null) ? quotes.filter((q) => q.eligible) : quotes
  const values = pool.map((q) => [q.supplier_id, row.value(q)] as const).filter((v): v is readonly [string, number] => v[1] !== null && Number.isFinite(v[1]))
  if (values.length < 2) return out
  const target = row.best === 'min' ? Math.min(...values.map((v) => v[1])) : Math.max(...values.map((v) => v[1]))
  for (const [sid, v] of values) if (v === target) out.add(sid)
  return out
}
