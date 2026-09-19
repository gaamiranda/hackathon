import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Run, WorkflowEvent } from '../api/types'
import { DecisionPanel } from '../components/DecisionPanel'
import { HumanGate } from '../components/HumanGate'
import { RequestPanel } from '../components/RequestPanel'
import { TimelinePanel } from '../components/TimelinePanel'
import { useEventStream } from '../hooks/useEventStream'

/** Events after which the Run aggregate has changed in ways only GET /runs/{id} reveals. */
const REFETCH_ON = new Set([
  'recommendation.ready',
  'extraction.needs_human',
  'calc.mismatch',
  'extraction.completed',
  'negotiation.awaiting_approval',
  'negotiation.closed',
  'requirement.changed',
  'replan.completed',
])

export function RunPage() {
  const { id } = useParams<{ id: string }>()
  const [run, setRun] = useState<Run | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(() => {
    if (!id) return
    api
      .getRun(id)
      .then((r) => {
        setRun(r)
        setError(null)
      })
      .catch((e: ApiError) => setError(e.message))
  }, [id])

  useEffect(refetch, [refetch])

  const onEvent = useCallback(
    (e: WorkflowEvent) => {
      if (REFETCH_ON.has(e.type) || e.type.endsWith('.needs_human')) refetch()
      else if (e.state_after && e.state_before !== e.state_after) setRun((r) => (r ? { ...r, state: e.state_after! } : r))
    },
    [refetch],
  )
  const { events, status } = useEventStream(id, onEvent)

  // doc_id → quote_id from the audit log (the Run aggregate carries no mapping)
  const quoteByDoc = useMemo(() => {
    const map: Record<string, string> = {}
    for (const e of events) {
      if (e.type === 'quote.extracted' && typeof e.payload.doc_id === 'string' && typeof e.payload.quote_id === 'string') map[e.payload.doc_id] = e.payload.quote_id
    }
    return map
  }, [events])

  if (error && !run) {
    return (
      <div className="p-6 text-sm">
        <p className="text-red-300">{error}</p>
        <Link to="/" className="text-emerald-400 hover:underline">
          ← back to runs
        </Link>
      </div>
    )
  }
  if (!run) return <div className="p-6 text-sm text-zinc-500">Loading run…</div>

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <div className="flex items-center gap-3 border-b border-zinc-800 px-4 py-2 text-sm">
        <Link to="/" className="text-zinc-400 hover:text-zinc-200">
          ← runs
        </Link>
        <span className="mono text-zinc-500">{run.run_id}</span>
        <span className="text-zinc-300">
          {run.request.quantity.toLocaleString()} × {run.request.product}
        </span>
        {run.request.version > 1 && (
          <span className="mono rounded bg-red-900/60 px-1.5 text-xs font-semibold text-red-200" title="request version (incremented by every interrupt)">
            v{run.request.version}
          </span>
        )}
        {error && <span className="ml-auto text-xs text-red-300">{error}</span>}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[360px_minmax(0,1fr)_420px] gap-3 p-3">
        <RequestPanel run={run} onRun={setRun} quoteByDoc={quoteByDoc} />
        <TimelinePanel events={events} status={status} state={run.state} />
        <DecisionPanel run={run} onRun={setRun} events={events} />
      </div>
      <HumanGate run={run} onRun={setRun} />
    </div>
  )
}
