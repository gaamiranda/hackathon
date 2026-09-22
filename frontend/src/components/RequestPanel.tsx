import { useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from '../api/client'
import type { NormalizedQuote, ProcurementRequest, RawDocument, Run, WorkflowState } from '../api/types'
import { money } from '../format'
import { Button, ErrorLine, Panel } from './Panel'

const CRITICAL = ['unit_price', 'currency', 'moq', 'lead_time_days', 'quantity_quoted']
const ACCEPT = '.pdf,.xlsx,.txt,.eml'
/** States that accept POST /runs/{id}/interrupt (D22). */
const INTERRUPTIBLE: WorkflowState[] = ['RECOMMENDED', 'EXTRACTED', 'AWAITING_NEGOTIATION_APPROVAL', 'AWAITING_PO_APPROVAL']
/** The two scripted interrupt moments, in step with backend/scripts/seed_demo.py.
 *  A (§15): quantity 2,000 → 5,000, so Borealis (capacity 4,000) drops out and Cobalt takes over.
 *  B (T22): budget 9,500 → 8,700 with the quantity untouched, so Eiger goes over budget and Fjord takes over. */
const DEMO_PRESETS: { label: string; quantity?: number; budget: string; reason: string }[] = [
  { label: 'Demo A: 2,000 → 5,000 units, budget 75,000', quantity: 5000, budget: '75000.00', reason: 'Customer order upsized' },
  { label: 'Demo B: budget 9,500 → 8,700', budget: '8700.00', reason: 'Budget cut by finance' },
]

export function RequestPanel({ run, onRun, quoteByDoc }: { run: Run; onRun: (r: Run) => void; quoteByDoc: Record<string, string> }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const canUpload = run.state === 'CREATED' || run.state === 'EXTRACTED'
  const minConf = run.config.thresholds.min_confidence

  const call = async (label: string, fn: () => Promise<Run>) => {
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

  const upload = (files: FileList | File[]) => {
    const list = Array.from(files)
    if (list.length) void call('upload', () => api.uploadDocuments(run.run_id, list))
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (canUpload) upload(e.dataTransfer.files)
  }

  const quoteFor = (docId: string): NormalizedQuote | undefined => {
    // quote.doc_id is authoritative and survives a human renaming the quote (manual entry of the reference, T17);
    // the quote.extracted events (payload.doc_id → quote_id) cover hand-written quotes without one.
    const byDoc = run.quotes.find((q) => q.doc_id === docId)
    if (byDoc) return byDoc
    const qid = quoteByDoc[docId]
    if (qid) return run.quotes.find((q) => q.quote_id === qid)
    const i = run.documents.findIndex((d) => d.doc_id === docId) // fallback: upload order
    return run.quotes[i]
  }

  return (
    <Panel
      title="Request & Documents"
      right={
        <span className={`mono rounded px-1.5 py-0.5 text-xs font-semibold ${run.request.version > 1 ? 'bg-red-900/60 text-red-200' : 'bg-zinc-800 text-zinc-400'}`} title="request version (incremented by every interrupt)">
          v{run.request.version}
        </span>
      }
    >
      <dl className="grid grid-cols-[auto_1fr_auto_auto] gap-x-3 gap-y-0.5 text-xs">
        <dt className="text-zinc-500">Product</dt>
        <dd className="truncate text-zinc-200" title={run.request.product}>
          {run.request.product}
        </dd>
        <dt className="text-zinc-500">Quantity</dt>
        <dd className="mono whitespace-nowrap text-zinc-200">{run.request.quantity.toLocaleString()}</dd>
        <dt className="text-zinc-500">Budget</dt>
        <dd className="mono whitespace-nowrap text-zinc-200">{money(run.request.budget, run.request.currency)}</dd>
        <dt className="text-zinc-500">Due</dt>
        <dd className="mono whitespace-nowrap text-zinc-200" title="required by">
          {run.request.required_by}
        </dd>
        <dt className="text-zinc-500">Tax</dt>
        <dd className="mono text-zinc-200">{run.config.tax_rate_pct}%</dd>
      </dl>
      {run.request_history.length > 0 && <PreviousVersions history={run.request_history} current={run.request} />}

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => canUpload && input.current?.click()}
        className={`mt-3 rounded border-2 border-dashed text-center text-sm transition-colors ${run.documents.length > 0 ? 'px-3 py-1.5 text-xs' : 'p-4'} ${
          !canUpload ? 'cursor-not-allowed border-zinc-800 text-zinc-600' : dragging ? 'border-emerald-500 bg-emerald-950/30 text-emerald-200' : 'cursor-pointer border-zinc-700 text-zinc-400 hover:border-zinc-500'
        }`}
      >
        {busy === 'upload'
          ? 'Uploading & extracting…'
          : canUpload
            ? run.documents.length > 0
              ? 'Add more supplier quotes (.pdf, .xlsx, .txt, .eml)'
              : 'Drop supplier quotes here (.pdf, .xlsx, .txt, .eml) or click'
            : 'Uploads are closed once evaluation has started'}
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => e.target.files && upload(e.target.files)} />
      </div>

      {run.documents.length > 0 && (
        <ul className="mt-3 space-y-1">
          {run.documents.map((doc) => {
            const q = quoteFor(doc.doc_id)
            const low = q ? CRITICAL.filter((f) => (q.field_confidence[f] ?? 0) < minConf) : []
            return <QuoteLine key={doc.doc_id} doc={doc} quote={q} low={low} />
          })}
        </ul>
      )}

      <div className="mt-3 flex items-center gap-3">
        <Button onClick={() => call('evaluate', () => api.evaluate(run.run_id))} disabled={run.state !== 'EXTRACTED' || busy !== null}>
          {busy === 'evaluate' ? 'Evaluating…' : 'Evaluate quotes'}
        </Button>
        {run.state === 'CREATED' && <span className="text-xs text-zinc-500">Upload the supplier quotes first</span>}
        {run.state !== 'EXTRACTED' && run.state !== 'CREATED' && <span className="text-xs text-zinc-500">Evaluation is available once quotes are extracted</span>}
      </div>
      <ErrorLine error={error} />

      {INTERRUPTIBLE.includes(run.state) && <InjectChange run={run} onRun={onRun} />}
    </Panel>
  )
}

/** Mid-workflow interrupt (PLAN.md §1 step 8, D22): the human changes quantity / budget / deadline and the agents
 *  replan on the existing quotes without restarting. Nothing happens until the red button is clicked. */
function InjectChange({ run, onRun }: { run: Run; onRun: (r: Run) => void }) {
  const r = run.request
  const [quantity, setQuantity] = useState(String(r.quantity))
  const [budget, setBudget] = useState(r.budget)
  const [requiredBy, setRequiredBy] = useState(r.required_by)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const qty = Number(quantity)
  const changed = {
    quantity: Number.isInteger(qty) && qty > 0 && qty !== r.quantity,
    budget: budget.trim() !== '' && Number(budget) !== Number(r.budget),
    required_by: requiredBy !== '' && requiredBy !== r.required_by,
  }
  const anyChange = changed.quantity || changed.budget || changed.required_by
  const isActive = (preset: (typeof DEMO_PRESETS)[number]) => qty === (preset.quantity ?? r.quantity) && Number(budget) === Number(preset.budget)

  const applyPreset = (preset: (typeof DEMO_PRESETS)[number]) => {
    setQuantity(String(preset.quantity ?? r.quantity))
    setBudget(preset.budget)
    setReason(preset.reason)
    setError(null)
  }

  const inject = async () => {
    setBusy(true)
    setError(null)
    try {
      onRun(
        await api.interrupt(run.run_id, {
          ...(changed.quantity ? { quantity: qty } : {}),
          ...(changed.budget ? { budget: Number(budget).toFixed(2) } : {}),
          ...(changed.required_by ? { required_by: requiredBy } : {}),
          reason,
        }),
      )
      setReason('')
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const field = 'mono w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100 focus:border-red-500 focus:outline-none'
  return (
    <div className="mt-4 rounded border border-red-900/70 bg-red-950/20 p-3">
      <div className="flex items-baseline justify-between">
        <h3 className="whitespace-nowrap text-xs font-semibold uppercase tracking-wider text-red-300">Inject change</h3>
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">replan without restart</span>
      </div>
      <p className="mt-1 text-xs text-zinc-400">
        Change the requirement mid-workflow; the agents replan on the quotes already extracted
        {run.state === 'AWAITING_NEGOTIATION_APPROVAL' ? ' and the unsent negotiation draft is discarded.' : run.state === 'AWAITING_PO_APPROVAL' ? ' and the unapproved PO preview is discarded.' : '.'}
      </p>
      <div className="mt-2 space-y-1">
        {DEMO_PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => applyPreset(preset)}
            className={`w-full rounded border px-2 py-1 text-left text-xs transition-colors ${isActive(preset) ? 'border-red-600 bg-red-900/40 text-red-100' : 'border-zinc-700 text-zinc-300 hover:border-red-700 hover:bg-red-950/40'}`}
            title="fills the fields below; nothing is sent until you click Inject"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
        <label className="space-y-0.5">
          <span className="text-zinc-500">Quantity{changed.quantity && <span className="text-red-300"> *</span>}</span>
          <input className={field} type="number" min={1} step={1} value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </label>
        <label className="space-y-0.5">
          <span className="text-zinc-500">Budget ({r.currency}){changed.budget && <span className="text-red-300"> *</span>}</span>
          <input className={field} type="number" min={0} step="0.01" value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        <label className="space-y-0.5">
          <span className="text-zinc-500">Required by{changed.required_by && <span className="text-red-300"> *</span>}</span>
          <input className={field} type="date" value={requiredBy} onChange={(e) => setRequiredBy(e.target.value)} />
        </label>
      </div>
      <input className={`${field} mt-2`} placeholder="Reason (logged on the requirement.changed event)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="mt-2 flex items-center gap-3">
        <Button tone="danger" disabled={!anyChange || busy} onClick={() => void inject()}>
          {busy ? 'Replanning…' : 'Inject change'}
        </Button>
        {!anyChange && <span className="text-xs text-zinc-500">change at least one field</span>}
      </div>
      <ErrorLine error={error} />
    </div>
  )
}

function PreviousVersions({ history, current }: { history: ProcurementRequest[]; current: ProcurementRequest }) {
  const [open, setOpen] = useState(false)
  const versions = [...history].reverse() // newest previous version first
  const diff = (older: ProcurementRequest, newer: ProcurementRequest) =>
    (['quantity', 'budget', 'required_by'] as const).filter((k) => String(older[k]) !== String(newer[k]))
  return (
    <div className="mt-2 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="text-zinc-500 hover:text-zinc-300">
        {open ? '▾' : '▸'} {history.length} previous version{history.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ul className="mt-1 space-y-1">
          {versions.map((v, i) => {
            const next = i === 0 ? current : versions[i - 1]
            const changedKeys = diff(v, next)
            return (
              <li key={v.version} className="rounded border border-zinc-800 bg-zinc-950/60 px-2 py-1">
                <span className="mono mr-2 rounded bg-zinc-800 px-1 text-zinc-400">v{v.version}</span>
                <span className="mono text-zinc-300">
                  {v.quantity.toLocaleString()} units · {money(v.budget, v.currency)} · by {v.required_by}
                </span>
                {changedKeys.length > 0 && <span className="ml-2 text-zinc-500">→ v{next.version} changed {changedKeys.join(', ')}</span>}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** One line per extracted quote — supplier · unit price · lead · MOQ · confidence dot — expanding on click to the
 *  full field grid, so "Inject change" stays on screen at 1440×900. */
function QuoteLine({ doc, quote: q, low }: { doc: RawDocument; quote: NormalizedQuote | undefined; low: string[] }) {
  const [open, setOpen] = useState(false)
  const minConf = q ? Math.min(...CRITICAL.map((f) => q.field_confidence[f] ?? 0)) : 0
  const dot = !q ? 'bg-red-500' : low.length > 0 ? 'bg-amber-400' : 'bg-emerald-500'
  const dotTitle = !q ? 'no quote extracted' : low.length > 0 ? `low confidence: ${low.join(', ')}` : `all critical fields confident (lowest ${(minConf * 100).toFixed(0)}%)`
  return (
    <li className="rounded border border-zinc-800 bg-zinc-950/60 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-zinc-900/60">
        <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} title={dotTitle} />
        <span className="truncate font-medium text-zinc-200">{q ? q.supplier_name : doc.filename}</span>
        {q && (
          <span className="mono ml-auto flex shrink-0 gap-1.5 text-[11px] text-zinc-400">
            <span className={low.includes('unit_price') || low.includes('currency') ? 'text-amber-300' : 'text-zinc-200'} title={`unit price, ${q.currency}`}>
              {money(q.unit_price)}/u
            </span>
            <span className={low.includes('lead_time_days') ? 'text-amber-300' : ''} title="lead time">
              {q.lead_time_days} d
            </span>
            <span className={low.includes('moq') ? 'text-amber-300' : ''} title="minimum order quantity">
              MOQ {q.moq.toLocaleString()}
            </span>
          </span>
        )}
        {!q && <span className="ml-auto text-amber-300">No quote extracted</span>}
        <span className="mono rounded bg-zinc-800 px-1 text-[10px] uppercase text-zinc-500">{doc.source}</span>
        <span className="text-zinc-600">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-zinc-800/80 px-2 py-2">
          <div className="mono mb-1.5 truncate text-[10px] text-zinc-500" title={doc.filename}>
            {doc.filename}
          </div>
          {q ? (
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
              <Field label="Supplier" value={`${q.supplier_name} · ${q.supplier_id}`} />
              <Field label="Quote ref" value={q.quote_id} />
              <Field label="Unit price" value={money(q.unit_price, q.currency)} low={low.includes('unit_price') || low.includes('currency')} />
              <Field label="Quantity" value={q.quantity_quoted.toLocaleString()} low={low.includes('quantity_quoted')} />
              <Field label="Lead time" value={`${q.lead_time_days} d`} low={low.includes('lead_time_days')} />
              <Field label="MOQ" value={q.moq.toLocaleString()} low={low.includes('moq')} />
              <Field label="Shipping" value={money(q.shipping_cost)} />
              <Field label="Stated total" value={money(q.llm_stated_total)} />
              {q.capacity_units !== null && <Field label="Capacity" value={q.capacity_units.toLocaleString()} />}
              {q.negotiated_offer && <Field label="Negotiated" value={`${q.negotiated_offer.unit_price}/unit · ${q.negotiated_offer.lead_time_days} d`} />}
              {low.length > 0 && <p className="col-span-2 mt-1 text-amber-300">Low confidence: {low.join(', ')}</p>}
            </div>
          ) : (
            <p className="text-amber-300">No quote could be extracted from this document — see the timeline, or replace the file from the review dialog.</p>
          )}
        </div>
      )}
    </li>
  )
}

function Field({ label, value, low }: { label: string; value: string; low?: boolean }) {
  return (
    <>
      <span className="text-zinc-500">{label}</span>
      <span className={`mono ${low ? 'rounded bg-amber-900/60 px-1 text-amber-200' : 'text-zinc-200'}`}>{value}</span>
    </>
  )
}
