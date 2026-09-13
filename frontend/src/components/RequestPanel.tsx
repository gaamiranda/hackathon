import { useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from '../api/client'
import type { NormalizedQuote, Run } from '../api/types'
import { money } from '../format'
import { Button, ErrorLine, Panel } from './Panel'

const CRITICAL = ['unit_price', 'currency', 'moq', 'lead_time_days', 'quantity_quoted']
const ACCEPT = '.pdf,.xlsx,.txt,.eml'

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
    <Panel title="Request & Documents">
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
    </Panel>
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
