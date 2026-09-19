import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CalcMismatchDetail, NegotiationApprovalDetail, Run } from '../api/types'
import { money } from '../format'
import { Button, ErrorLine } from './Panel'

/** Human gates (PLAN.md G1/G5/G6): rendered from run.pending_human.kind. */
export function HumanGate({ run, onRun }: { run: Run; onRun: (r: Run) => void }) {
  const pending = run.pending_human
  // A negotiation draft can be set aside to reach the Request panel (e.g. to inject a requirement change, D22);
  // the pending draft stays on the backend and the bar below reopens it. Keyed per draft so a new one pops up.
  const [minimised, setMinimised] = useState<string | null>(null)
  if (!pending) return null
  const draftKey = pending.kind === 'negotiation_approval' ? `${pending.details.supplier_id}-${pending.details.round}` : null
  if (draftKey && minimised === draftKey) {
    return (
      <div className="fixed bottom-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-amber-700 bg-zinc-900 px-4 py-2 text-sm shadow-2xl">
        <span className="text-amber-300">Negotiation draft awaiting your approval</span>
        <span className="text-zinc-500">— nothing is sent while it waits</span>
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
  } else body = <ExtractionGate run={run} onRun={onRun} />
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/70 p-6">
      <div className="w-full max-w-2xl rounded-lg border border-amber-700 bg-zinc-900 p-5 shadow-2xl">
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-xs font-semibold uppercase tracking-widest text-amber-400">Human review required · {pending.kind}</span>
          {draftKey && (
            <button type="button" onClick={() => setMinimised(draftKey)} className="text-xs text-zinc-500 hover:text-zinc-300" title="set the draft aside (e.g. to inject a requirement change); it stays pending">
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

/** Integer fields go back as numbers; money/percent stay strings; the backend re-validates. */
function coerce(patch: Record<string, string>): Record<string, unknown> {
  const ints = new Set(['moq', 'lead_time_days', 'quantity_quoted', 'capacity_units'])
  return Object.fromEntries(Object.entries(patch).map(([k, v]) => [k, ints.has(k) ? Number(v) : v]))
}
