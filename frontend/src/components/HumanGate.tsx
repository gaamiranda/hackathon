import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CalcMismatchDetail, Run } from '../api/types'
import { money } from '../format'
import { Button, ErrorLine } from './Panel'

/** Human gates (PLAN.md G1/G6): rendered from run.pending_human.kind. */
export function HumanGate({ run, onRun }: { run: Run; onRun: (r: Run) => void }) {
  const pending = run.pending_human
  if (!pending) return null
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/70 p-6">
      <div className="w-full max-w-2xl rounded-lg border border-amber-700 bg-zinc-900 p-5 shadow-2xl">
        <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-amber-400">Human review required · {pending.kind}</div>
        <p className="mb-4 text-sm text-zinc-300">{pending.message}</p>
        {pending.kind === 'calc_mismatch' ? <MismatchGate run={run} onRun={onRun} /> : <ExtractionGate run={run} onRun={onRun} />}
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
  const details = run.pending_human!.details as { fields?: Record<string, string[]>; failed_documents?: Record<string, string> }
  const fields = details.fields ?? {}
  const failed = details.failed_documents ?? {}
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

/** Integer fields go back as numbers; money/percent stay strings; the backend re-validates. */
function coerce(patch: Record<string, string>): Record<string, unknown> {
  const ints = new Set(['moq', 'lead_time_days', 'quantity_quoted', 'capacity_units'])
  return Object.fromEntries(Object.entries(patch).map(([k, v]) => [k, ints.has(k) ? Number(v) : v]))
}
