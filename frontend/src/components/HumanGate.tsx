import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CalcMismatchDetail, ExtractionDetail, ManualExtractionDetail, NegotiationApprovalDetail, PoApprovalDetail, Run } from '../api/types'
import { money } from '../format'
import { gateLabel } from '../labels'
import { TotalsTable } from './DecisionPanel'
import { Button, ErrorLine } from './Panel'

/** Human gates (PLAN.md G1/G5/G6): rendered from run.pending_human.kind.
 *  A negotiation draft / PO preview can be set aside to reach the Request panel (e.g. to inject a requirement
 *  change, D22); the pending item stays on the backend and the bar below — or the summary strip's "waiting for
 *  human" pill — reopens it. `minimised` is keyed per draft so a new one pops up on its own. */
export function HumanGate({
  run,
  onRun,
  minimised,
  onMinimise,
}: {
  run: Run
  onRun: (r: Run) => void
  minimised: string | null
  onMinimise: (key: string | null) => void
}) {
  const pending = run.pending_human
  const setMinimised = onMinimise
  if (!pending) return null
  const draftKey =
    pending.kind === 'negotiation_approval'
      ? `${pending.details.supplier_id}-${pending.details.round}`
      : pending.kind === 'po_approval'
        ? `po-${pending.details.supplier_id}-${pending.details.request_version}`
        : null
  if (draftKey && minimised === draftKey) {
    const po = pending.kind === 'po_approval'
    return (
      <div className="fixed bottom-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-amber-700 bg-zinc-900 px-4 py-2 text-sm shadow-2xl">
        <span className="text-amber-300">{po ? 'Purchase order awaiting your approval' : 'Negotiation draft awaiting your approval'}</span>
        <span className="text-zinc-500">{po ? '— nothing is generated while it waits' : '— nothing is sent while it waits'}</span>
        <Button tone="ghost" onClick={() => setMinimised(null)}>
          Reopen
        </Button>
      </div>
    )
  }
  let body
  if (pending.kind === 'calc_mismatch') body = <MismatchGate run={run} onRun={onRun} />
  else if (pending.kind === 'negotiation_approval') {
    const d = pending.details as unknown as NegotiationApprovalDetail
    // Keyed per draft so the textarea resets when the next round (or supplier) comes up.
    body = <NegotiationGate key={`${d.supplier_id}-${d.round}`} run={run} onRun={onRun} detail={d} />
  } else if (pending.kind === 'po_approval') {
    body = <PoGate key={draftKey} run={run} onRun={onRun} detail={pending.details as unknown as PoApprovalDetail} />
  } else body = <ExtractionGate run={run} onRun={onRun} />
  const manual = pending.kind === 'extraction' && (pending.details as unknown as ExtractionDetail).reason === 'llm_unavailable'
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/70 p-6">
      <div className={`max-h-[92vh] w-full overflow-y-auto rounded-lg border bg-zinc-900 p-5 shadow-2xl ${manual ? 'max-w-6xl border-red-700' : 'max-w-2xl border-amber-700'}`}>
        <div className="mb-1 flex items-baseline justify-between">
          <span className={`text-xs font-semibold uppercase tracking-widest ${manual ? 'text-red-400' : 'text-amber-400'}`} title={`pending_human.kind = ${pending.kind}`}>
            Human review required · {manual ? 'Manual extraction' : gateLabel(pending.kind)}
          </span>
          {draftKey && (
            <button type="button" onClick={() => setMinimised(draftKey)} className="text-xs text-zinc-500 hover:text-zinc-300" title="set aside (e.g. to inject a requirement change); it stays pending">
              set aside ▾
            </button>
          )}
        </div>
        <p className="mb-4 text-sm text-zinc-300">{pending.message}</p>
        {body}
      </div>
    </div>
  )
}

function useAction(onRun: (r: Run) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (fn: () => Promise<Run>) => {
    setBusy(true)
    setError(null)
    try {
      onRun(await fn())
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, act }
}

function MismatchGate({ run, onRun }: { run: Run; onRun: (r: Run) => void }) {
  const { busy, error, act } = useAction(onRun)
  const details = run.pending_human!.details as Record<string, CalcMismatchDetail>
  const names = Object.fromEntries(run.quotes.map((q) => [q.quote_id, q.supplier_name]))
  return (
    <div className="space-y-3">
      {run.pending_human!.quote_ids.map((qid) => {
        const d = details[qid]
        return (
          <div key={qid} className="rounded border border-zinc-700 bg-zinc-950/60 p-3 text-sm">
            <div className="mb-2 flex items-baseline justify-between">
              <span className="font-medium">{names[qid] ?? d?.supplier_id}</span>
              <span className="mono text-xs text-zinc-500">{qid}</span>
            </div>
            <div className="mono grid grid-cols-2 gap-2">
              <div className="rounded bg-zinc-900 p-2">
                <div className="text-[10px] uppercase text-zinc-500">Document states</div>
                <div className="text-red-300">{money(d?.stated_total)}</div>
              </div>
              <div className="rounded bg-zinc-900 p-2">
                <div className="text-[10px] uppercase text-zinc-500">Engine computed (pre-tax)</div>
                <div className="text-emerald-300">{money(d?.computed_pre_tax_total)}</div>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <Button disabled={busy} onClick={() => act(() => api.confirmMath(run.run_id, qid, true))}>
                Use computed total
              </Button>
              <Button tone="danger" disabled={busy} onClick={() => act(() => api.confirmMath(run.run_id, qid, false))}>
                Withdraw quote
              </Button>
            </div>
          </div>
        )
      })}
      <ErrorLine error={error} />
    </div>
  )
}

function ExtractionGate({ run, onRun }: { run: Run; onRun: (r: Run) => void }) {
  const { busy, error, act } = useAction(onRun)
  const details = run.pending_human!.details as unknown as ExtractionDetail
  const fields = details.fields ?? {}
  const failed = details.failed_documents ?? {}
  // doc_id → manual details (LLM unavailable, T17) re-keyed by quote so each quote picks its form
  const manualByQuote = Object.fromEntries(Object.entries(details.manual ?? {}).map(([docId, m]) => [m.quote_id, { docId, ...m }]))
  const [values, setValues] = useState<Record<string, Record<string, string>>>(() =>
    Object.fromEntries(
      Object.entries(fields).map(([qid, names]) => {
        const q = run.quotes.find((x) => x.quote_id === qid)
        return [qid, Object.fromEntries(names.map((f) => [f, q ? String((q as unknown as Record<string, unknown>)[f] ?? '') : '']))]
      }),
    ),
  )
  const input = 'mono w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm focus:border-emerald-500 focus:outline-none'
  return (
    <div className="space-y-3">
      {Object.entries(fields).map(([qid, names]) => {
        const q = run.quotes.find((x) => x.quote_id === qid)
        const manual = manualByQuote[qid]
        if (manual) {
          const doc = run.documents.find((d) => d.doc_id === manual.docId)
          return (
            <ManualQuoteForm
              key={qid}
              quoteId={qid}
              fields={names}
              quote={q}
              text={doc?.text ?? manual.text_excerpt}
              detail={manual}
              busy={busy}
              onSubmit={(patch) => act(() => api.correctQuote(run.run_id, qid, patch))}
            />
          )
        }
        return (
          <div key={qid} className="rounded border border-zinc-700 bg-zinc-950/60 p-3 text-sm">
            <div className="mb-2 flex items-baseline justify-between">
              <span className="font-medium">{q?.supplier_name ?? qid}</span>
              <span className="mono text-xs text-zinc-500">{qid}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {names.map((f) => (
                <label key={f} className="text-xs text-zinc-400">
                  {f} <span className="text-amber-400">(confidence {((q?.field_confidence[f] ?? 0) * 100).toFixed(0)}%)</span>
                  <input className={input} value={values[qid]?.[f] ?? ''} onChange={(e) => setValues({ ...values, [qid]: { ...values[qid], [f]: e.target.value } })} />
                </label>
              ))}
            </div>
            {q?.raw_excerpt && <p className="mono mt-2 line-clamp-3 text-[11px] text-zinc-500">{q.raw_excerpt}</p>}
            <div className="mt-3">
              <Button disabled={busy} onClick={() => act(() => api.correctQuote(run.run_id, qid, coerce(values[qid] ?? {})))}>
                Confirm fields
              </Button>
            </div>
          </div>
        )
      })}
      {Object.keys(failed).length > 0 && (
        <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-200">
          <div className="font-medium">Documents that could not be parsed</div>
          <ul className="mt-2 space-y-2 text-xs">
            {Object.entries(failed).map(([docId, err]) => (
              <li key={docId} className="rounded bg-zinc-950/60 p-2">
                <div className="mono">{run.documents.find((d) => d.doc_id === docId)?.filename ?? docId}</div>
                <div className="mono mt-0.5 break-words text-red-300/80">{err}</div>
                <label className="mt-2 inline-block cursor-pointer rounded border border-zinc-600 px-2 py-1 text-zinc-200 hover:bg-zinc-800">
                  {busy ? 'Uploading…' : 'Replace file…'}
                  <input
                    type="file"
                    accept=".pdf,.xlsx,.txt,.eml"
                    className="hidden"
                    disabled={busy}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) void act(() => api.replaceDocument(run.run_id, docId, f))
                    }}
                  />
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      <ErrorLine error={error} />
    </div>
  )
}

type FieldKind = 'money' | 'int' | 'pct' | 'text' | 'currency' | 'date'

/** How each fillable NormalizedQuote field is typed in (manual mode, T17). The order shown is the backend's: critical first. */
const FIELD_SPEC: Record<string, { label: string; kind: FieldKind; placeholder?: string }> = {
  unit_price: { label: 'Unit price', kind: 'money', placeholder: '12.80' },
  currency: { label: 'Currency', kind: 'currency', placeholder: 'USD' },
  moq: { label: 'MOQ (units)', kind: 'int', placeholder: '1000' },
  lead_time_days: { label: 'Lead time (days)', kind: 'int', placeholder: '10' },
  quantity_quoted: { label: 'Quantity quoted', kind: 'int', placeholder: '2000' },
  supplier_name: { label: 'Supplier name', kind: 'text', placeholder: 'as printed on the document' },
  supplier_id: { label: 'Supplier id', kind: 'text', placeholder: 'derived from the name if empty' },
  quote_id: { label: 'Quote reference', kind: 'text', placeholder: 'e.g. BOR-2026-0418' },
  payment_terms: { label: 'Payment terms', kind: 'text', placeholder: 'Net 30' },
  shipping_cost: { label: 'Shipping cost', kind: 'money', placeholder: '0' },
  discount_pct: { label: 'Discount %', kind: 'pct', placeholder: '0' },
  validity_date: { label: 'Valid until', kind: 'date' },
  capacity_units: { label: 'Capacity (units)', kind: 'int' },
  llm_stated_total: { label: 'Total printed on the document', kind: 'money', placeholder: 'left empty = no total printed' },
}
const CRITICAL = new Set(['unit_price', 'currency', 'moq', 'lead_time_days', 'quantity_quoted'])
/** Prefilled only when the human already confirmed the field (confidence 1.0); the placeholder quote's zeros stay blank. */
const isConfirmed = (quote: Run['quotes'][number] | undefined, f: string) => (quote?.field_confidence[f] ?? 0) >= 1

/**
 * Manual extraction (T17, PLAN.md G6): the LLM never saw this document, so the person reads the text on the left and
 * types every field on the right. Critical fields are required; the rest default like the Document Agent would
 * (shipping 0, discount 0, no validity date). Nothing here is AI-generated: values go straight to the engine.
 */
function ManualQuoteForm({
  quoteId,
  fields,
  quote,
  text,
  detail,
  busy,
  onSubmit,
}: {
  quoteId: string
  fields: string[]
  quote: Run['quotes'][number] | undefined
  text: string
  detail: ManualExtractionDetail
  busy: boolean
  onSubmit: (patch: Record<string, unknown>) => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => {
        if (isConfirmed(quote, f)) return [f, String((quote as unknown as Record<string, unknown>)[f] ?? '')]
        return [f, f === 'currency' ? 'USD' : '']
      }),
    ),
  )
  const missing = fields.filter((f) => CRITICAL.has(f) && values[f].trim() === '')
  const input = 'mono w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none'
  const inputProps = (kind: FieldKind) =>
    kind === 'int'
      ? { type: 'number', min: 0, step: 1 }
      : kind === 'date'
        ? { type: 'date' }
        : kind === 'currency'
          ? { maxLength: 3, pattern: '[A-Z]{3}' }
          : kind === 'pct'
            ? { inputMode: 'decimal' as const, pattern: String.raw`^\d+(\.\d+)?$` }
            : kind === 'money'
              ? { inputMode: 'decimal' as const, pattern: String.raw`^\d+(\.\d{1,2})?$` }
              : {}
  return (
    <div className="rounded border border-red-900/70 bg-zinc-950/60 p-3 text-sm">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="font-medium text-zinc-100">
          {detail.filename} <span className="ml-1 rounded bg-red-900/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-200">AI unavailable · manual entry</span>
        </span>
        <span className="mono truncate text-xs text-zinc-500" title={detail.detail}>
          {quoteId}
        </span>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-3">
        <div className="min-h-0">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">Document text (read-only)</div>
          {/* Supplier text is untrusted (G4): escaped plain text only. */}
          <pre className="mono max-h-[56vh] overflow-y-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950 p-2 text-[11px] leading-relaxed text-zinc-300">{text}</pre>
        </div>
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
            Quote fields <span className="text-red-300">* critical</span>
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5">
            {fields.map((f) => {
              const spec = FIELD_SPEC[f] ?? { label: f, kind: 'text' as FieldKind }
              const critical = CRITICAL.has(f)
              return (
                <label key={f} className={`text-xs ${critical ? 'text-zinc-300' : 'text-zinc-500'}`}>
                  {spec.label}
                  {critical && <span className="text-red-300"> *</span>}
                  <input
                    className={`${input} ${critical && values[f].trim() === '' ? 'border-red-800' : ''}`}
                    value={values[f]}
                    placeholder={spec.placeholder}
                    disabled={busy}
                    {...inputProps(spec.kind)}
                    onChange={(e) => setValues({ ...values, [f]: spec.kind === 'currency' ? e.target.value.toUpperCase() : e.target.value })}
                  />
                </label>
              )
            })}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button disabled={busy || missing.length > 0} onClick={() => onSubmit(manualPatch(values))}>
              {busy ? 'Saving…' : 'Save quote'}
            </Button>
            <span className="text-[11px] text-zinc-500">{missing.length > 0 ? `fill ${missing.map((f) => FIELD_SPEC[f]?.label ?? f).join(', ')}` : 'confidence becomes 100% on every saved field; the engine does the rest'}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Typed-in values → correct-quote patch: empty fields are left out (backend defaults), integers go as numbers,
 *  money and percentages stay strings (never floats), the currency is upper-cased. */
function manualPatch(values: Record<string, string>): Record<string, unknown> {
  const patch: Record<string, unknown> = {}
  for (const [k, raw] of Object.entries(values)) {
    const v = raw.trim()
    if (v === '') continue
    const kind = FIELD_SPEC[k]?.kind ?? 'text'
    patch[k] = kind === 'int' ? Number(v) : kind === 'currency' ? v.toUpperCase() : v
  }
  return patch
}

/**
 * Negotiation send gate (G5): the agent's draft is shown in an editable textarea and nothing leaves
 * without a click. An edited message is re-checked by the backend's outbound filter (G3); a 422
 * policy_violation keeps the modal open and lists the reasons under the textarea.
 */
function NegotiationGate({ run, onRun, detail }: { run: Run; onRun: (r: Run) => void; detail: NegotiationApprovalDetail }) {
  const [text, setText] = useState(detail.draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [violations, setViolations] = useState<string[]>([])
  const edited = text !== detail.draft
  const quote = run.quotes.find((q) => q.supplier_id === detail.supplier_id)
  const thread = run.negotiations[detail.supplier_id]
  const b = detail.boundaries
  const current = thread?.current_offer ?? thread?.original_offer

  const send = async () => {
    setBusy(true)
    setError(null)
    setViolations([])
    try {
      // Only an edited text travels; otherwise the backend sends the draft it already holds.
      onRun(await api.approveNegotiation(run.run_id, detail.supplier_id, edited ? text : undefined))
    } catch (err) {
      const e = err as ApiError
      if (e.status === 422 && e.code === 'policy_violation') setViolations(e.violations.length ? e.violations : [e.message])
      else setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <div className="rounded border border-zinc-700 bg-zinc-950/60 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="font-medium">{quote?.supplier_name ?? detail.supplier_id}</span>
          <span className="mono text-xs text-zinc-500">
            {detail.supplier_id} · round {detail.round} of {b.max_rounds}
          </span>
        </div>
        <div className="mono grid grid-cols-3 gap-2 text-xs">
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Supplier's standing offer</div>
            <div className="text-zinc-200">{current ? `${current.unit_price}/unit · ${current.lead_time_days} d` : '—'}</div>
          </div>
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Our ask (target)</div>
            <div className="text-emerald-300">
              {detail.target_offer.unit_price}/unit · {detail.target_offer.lead_time_days} d
            </div>
          </div>
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Boundaries</div>
            <div className="text-zinc-300">
              ≤ {b.max_discount_ask_pct}% off · ≥ {b.min_lead_time_days} d · {b.max_rounds} rounds
            </div>
          </div>
        </div>
      </div>

      <label className="block text-xs text-zinc-400">
        Message to {quote?.supplier_name ?? detail.supplier_id}
        {edited && <span className="ml-2 text-amber-400">(edited — will be re-checked by the policy filter)</span>}
        <textarea
          className="mt-1 h-48 w-full resize-y rounded border border-zinc-700 bg-zinc-950 p-2 text-sm leading-relaxed text-zinc-100 focus:border-emerald-500 focus:outline-none"
          value={text}
          disabled={busy}
          onChange={(e) => {
            setText(e.target.value)
            setViolations([])
          }}
        />
      </label>

      {violations.length > 0 && (
        <div className="rounded border border-red-900 bg-red-950/60 px-3 py-2 text-red-200">
          <div className="text-xs font-semibold uppercase tracking-wide">Blocked by the outbound policy filter — not sent</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
            {violations.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2">
        <Button disabled={busy || text.trim() === ''} onClick={() => void send()}>
          {busy ? 'Sending…' : 'Approve & Send'}
        </Button>
        <Button
          tone="ghost"
          disabled={busy || !edited}
          onClick={() => {
            setText(detail.draft)
            setViolations([])
          }}
        >
          Reset to draft
        </Button>
      </div>
      <ErrorLine error={error} />
    </div>
  )
}

/**
 * Final gate (G5): the human confirms the supplier and the engine's numbers, and only this click creates a
 * purchase order. The preview shown here is exactly what the backend will number and sign; if the engine's
 * totals moved in between, approve-po answers 409 totals_changed and nothing is generated.
 */
function PoGate({ run, onRun, detail }: { run: Run; onRun: (r: Run) => void; detail: PoApprovalDetail }) {
  const { busy, error, act } = useAction(onRun)
  const [approver, setApprover] = useState('demo-user')
  const [reason, setReason] = useState('')
  const preview = run.po_preview
  const quote = run.quotes.find((q) => q.supplier_id === detail.supplier_id)
  const cur = preview?.currency ?? run.request.currency
  const quantity = preview?.line_items[0]?.quantity ?? run.request.quantity
  const available = Math.round((new Date(run.request.required_by).getTime() - new Date(run.request.created_at).getTime()) / 86_400_000)
  const onTime = detail.lead_time_days <= available
  const input = 'mono w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none'
  return (
    <div className="space-y-3 text-sm">
      <div className="rounded border border-zinc-700 bg-zinc-950/60 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="font-medium">{detail.supplier_name}</span>
          <span className="mono text-xs text-zinc-500">
            {detail.supplier_id} · request v{detail.request_version}
          </span>
        </div>
        <div className="mono grid grid-cols-3 gap-2 text-xs">
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Quantity</div>
            <div className="text-zinc-200">
              {quantity.toLocaleString()} × {run.request.product}
            </div>
          </div>
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Unit price</div>
            <div className="text-emerald-300">
              {detail.unit_price} {cur}
              {detail.negotiated && (
                <span className="ml-1.5 rounded bg-amber-900/60 px-1 py-0.5 text-[10px] text-amber-200" title={quote ? `quoted ${quote.unit_price}/unit` : undefined}>
                  negotiated
                </span>
              )}
            </div>
            {detail.negotiated && quote && <div className="text-[10px] text-zinc-500">quoted {quote.unit_price}</div>}
          </div>
          <div className="rounded bg-zinc-900 p-2">
            <div className="text-[10px] uppercase text-zinc-500">Lead time vs required by</div>
            <div className={onTime ? 'text-zinc-200' : 'text-red-300'}>
              {detail.lead_time_days} d {onTime ? '≤' : '>'} {available} d
            </div>
            <div className="text-[10px] text-zinc-500">due {run.request.required_by}</div>
          </div>
        </div>
        <TotalsTable totals={detail.totals} currency={cur} />
        {preview?.payment_terms && <p className="mt-1.5 text-[11px] text-zinc-500">Payment terms: {preview.payment_terms}</p>}
        <p className="mt-1.5 text-[11px] text-zinc-500">Every figure above is the deterministic engine's; the PO is numbered only after your approval.</p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs text-zinc-400">
          Approver name
          <input className={input} value={approver} disabled={busy} onChange={(e) => setApprover(e.target.value)} />
        </label>
        <label className="block text-xs text-zinc-400">
          Rejection reason (optional)
          <input className={input} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} placeholder="why not this supplier?" />
        </label>
      </div>

      <div className="flex gap-2">
        <Button disabled={busy || approver.trim() === ''} onClick={() => act(() => api.approvePo(run.run_id, approver.trim()))}>
          {busy ? 'Generating…' : 'Approve Final Supplier & Generate PO'}
        </Button>
        <Button tone="danger" disabled={busy} onClick={() => act(() => api.rejectPo(run.run_id, reason.trim()))}>
          Reject
        </Button>
      </div>
      <ErrorLine error={error} />
    </div>
  )
}

/** Integer fields go back as numbers; money/percent stay strings; the backend re-validates. */
function coerce(patch: Record<string, string>): Record<string, unknown> {
  const ints = new Set(['moq', 'lead_time_days', 'quantity_quoted', 'capacity_units'])
  return Object.fromEntries(Object.entries(patch).map(([k, v]) => [k, ints.has(k) ? Number(v) : v]))
}
