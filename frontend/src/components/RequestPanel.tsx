import { useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from '../api/client'
import type { NormalizedQuote, ProcurementRequest, Run, WorkflowState } from '../api/types'
import { money } from '../format'
import { Button, ErrorLine, Panel } from './Panel'

const CRITICAL = ['unit_price', 'currency', 'moq', 'lead_time_days', 'quantity_quoted']
const ACCEPT = '.pdf,.xlsx,.txt,.eml'
/** States that accept POST /runs/{id}/interrupt (D22). */
const INTERRUPTIBLE: WorkflowState[] = ['RECOMMENDED', 'EXTRACTED', 'AWAITING_NEGOTIATION_APPROVAL']
/** The §15 demo moment: Borealis (capacity 4,000) drops out, Cobalt takes over. */
const DEMO_PRESET = { quantity: 5000, budget: '75000.00', reason: 'Customer order upsized' }

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
    // The Run holds no doc→quote mapping; it comes from quote.extracted events (payload.doc_id → quote_id).
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
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-zinc-500">Product</dt>
        <dd>{run.request.product}</dd>
        <dt className="text-zinc-500">Quantity</dt>
        <dd className="mono">{run.request.quantity.toLocaleString()}</dd>
        <dt className="text-zinc-500">Required by</dt>
        <dd className="mono">{run.request.required_by}</dd>
        <dt className="text-zinc-500">Budget</dt>
        <dd className="mono">{money(run.request.budget, run.request.currency)}</dd>
        <dt className="text-zinc-500">Tax</dt>
        <dd className="mono">{run.config.tax_rate_pct}%</dd>
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
        className={`mt-4 rounded border-2 border-dashed p-4 text-center text-sm transition-colors ${
          !canUpload ? 'cursor-not-allowed border-zinc-800 text-zinc-600' : dragging ? 'border-emerald-500 bg-emerald-950/30 text-emerald-200' : 'cursor-pointer border-zinc-700 text-zinc-400 hover:border-zinc-500'
        }`}
      >
        {busy === 'upload' ? 'Uploading & extracting…' : canUpload ? 'Drop supplier quotes here (.pdf, .xlsx, .txt, .eml) or click' : `Uploads locked in state ${run.state}`}
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => e.target.files && upload(e.target.files)} />
      </div>

      <ul className="mt-4 space-y-2">
        {run.documents.map((doc) => {
          const q = quoteFor(doc.doc_id)
          const low = q ? CRITICAL.filter((f) => (q.field_confidence[f] ?? 0) < minConf) : []
          return (
            <li key={doc.doc_id} className="rounded border border-zinc-800 bg-zinc-950/60 p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="mono truncate text-zinc-300">{doc.filename}</span>
                <span className="mono ml-2 rounded bg-zinc-800 px-1.5 text-xs uppercase text-zinc-400">{doc.source}</span>
              </div>
              {q ? (
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
                  <Field label="Supplier" value={`${q.supplier_name} (${q.supplier_id})`} />
                  <Field label="Quote" value={q.quote_id} />
                  <Field label="Unit price" value={money(q.unit_price, q.currency)} low={low.includes('unit_price') || low.includes('currency')} />
                  <Field label="Quantity" value={q.quantity_quoted.toLocaleString()} low={low.includes('quantity_quoted')} />
                  <Field label="Lead time" value={`${q.lead_time_days} d`} low={low.includes('lead_time_days')} />
                  <Field label="MOQ" value={q.moq.toLocaleString()} low={low.includes('moq')} />
                  <Field label="Shipping" value={money(q.shipping_cost)} />
                  <Field label="Stated total" value={money(q.llm_stated_total)} />
                  {low.length > 0 && <p className="col-span-2 mt-1 text-amber-300">Low confidence: {low.join(', ')}</p>}
                </div>
              ) : (
                <p className="mt-1 text-xs text-amber-300">No quote extracted (see timeline / re-upload)</p>
              )}
            </li>
          )
        })}
      </ul>

      <div className="mt-4 flex items-center gap-3">
        <Button onClick={() => call('evaluate', () => api.evaluate(run.run_id))} disabled={run.state !== 'EXTRACTED' || busy !== null}>
          {busy === 'evaluate' ? 'Evaluating…' : 'Evaluate'}
        </Button>
        {run.state !== 'EXTRACTED' && run.state !== 'CREATED' && <span className="text-xs text-zinc-500">Evaluate is enabled in state EXTRACTED</span>}
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
  const presetActive = qty === DEMO_PRESET.quantity && Number(budget) === Number(DEMO_PRESET.budget)

  const applyPreset = () => {
    setQuantity(String(DEMO_PRESET.quantity))
    setBudget(DEMO_PRESET.budget)
    setReason(DEMO_PRESET.reason)
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
    <div className="mt-5 rounded border border-red-900/70 bg-red-950/20 p-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-red-300">Inject change</h3>
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">interrupt · replan without restart</span>
      </div>
      <p className="mt-1 text-xs text-zinc-400">
        Change the requirement mid-workflow. The agents revisit capacity, MOQ, pricing, lead time, budget and risk on the quotes already extracted
        {run.state === 'AWAITING_NEGOTIATION_APPROVAL' ? '; the unsent negotiation draft is discarded.' : '.'}
      </p>
      <button
        type="button"
        onClick={applyPreset}
        className={`mt-2 w-full rounded border px-2 py-1 text-left text-xs transition-colors ${presetActive ? 'border-red-600 bg-red-900/40 text-red-100' : 'border-zinc-700 text-zinc-300 hover:border-red-700 hover:bg-red-950/40'}`}
        title="fills the fields below; nothing is sent until you click Inject"
      >
        Demo: 2,000 → 5,000 units, budget 75,000
      </button>
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
          {busy ? 'Replanning…' : 'Inject requirement change'}
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

function Field({ label, value, low }: { label: string; value: string; low?: boolean }) {
  return (
    <>
      <span className="text-zinc-500">{label}</span>
      <span className={`mono ${low ? 'rounded bg-amber-900/60 px-1 text-amber-200' : 'text-zinc-200'}`}>{value}</span>
    </>
  )
}
