import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { API_URL, api, ApiError } from '../api/client'
import type { Run, RunOverview, WorkflowEvent } from '../api/types'
import { AgentLanes } from '../components/AgentLanes'
import { DecisionPanel } from '../components/DecisionPanel'
import { HumanGate } from '../components/HumanGate'
import { Button } from '../components/Panel'
import { RequestPanel } from '../components/RequestPanel'
import { SummaryStrip } from '../components/SummaryStrip'
import { TimelinePanel, type TimelineFilter } from '../components/TimelinePanel'
import { useEventStream } from '../hooks/useEventStream'
import { useHealth } from '../hooks/useHealth'
import type { LaneKey } from '../labels'

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
  'po.requested',
  'po.generated',
  'po.rejected',
  'po.discarded',
])

export function RunPage() {
  const { id } = useParams<{ id: string }>()
  const [run, setRun] = useState<Run | null>(null)
  const [overview, setOverview] = useState<RunOverview | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const health = useHealth()
  const [filter, setFilter] = useState<TimelineFilter>('all')
  // A negotiation draft / PO preview can be set aside to reach the Request panel; the summary strip reopens it.
  const [gateMinimised, setGateMinimised] = useState<string | null>(null)

  const refetch = useCallback(() => {
    if (!id) return
    api
      .getRun(id)
      .then((r) => {
        setRun(r)
        setError(null)
      })
      .catch((e: ApiError) => setError(e))
  }, [id])

  useEffect(refetch, [refetch])

  // The header strip follows every fresh Run (refetch or an action's response): updated_at moves on each one.
  useEffect(() => {
    if (!id || !run) return
    api.getOverview(id).then(setOverview).catch(() => undefined)
  }, [id, run?.updated_at, run?.state]) // eslint-disable-line react-hooks/exhaustive-deps

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

  const names = useMemo(() => Object.fromEntries((run?.quotes ?? []).map((q) => [q.supplier_id, q.supplier_name])), [run?.quotes])

  if (error && !run) {
    const notFound = error.status === 404
    const unreachable = error.status === 0
    return (
      <div className="mx-auto mt-16 max-w-lg rounded-lg border border-zinc-800 bg-zinc-900/60 p-6 text-sm">
        <h2 className="text-base font-semibold text-zinc-100">{notFound ? 'Run not found' : unreachable ? 'Backend unreachable' : 'Could not load this run'}</h2>
        <p className="mt-2 text-zinc-400">
          {notFound
            ? `There is no run "${id}" on this backend. It may have been created before a restart without persistence, or the link is wrong.`
            : unreachable
              ? `Nothing is answering at ${API_URL}. Start the backend (just run) or check VITE_API_URL.`
              : error.message}
        </p>
        <div className="mt-4 flex gap-2">
          <Link to="/" className="rounded border border-zinc-700 px-3 py-1.5 text-zinc-200 hover:bg-zinc-800">
            ← All runs
          </Link>
          {!notFound && <Button onClick={refetch}>Retry</Button>}
        </div>
      </div>
    )
  }
  if (!run) return <div className="p-6 text-sm text-zinc-500">Loading run…</div>

  const laneFilter: LaneKey | null = filter === 'all' || filter === 'moments' ? null : filter

  return (
    <div className="flex h-full flex-col">
      <SummaryStrip runId={run.run_id} overview={overview} state={run.state} currency={run.request.currency} health={health} onOpenGate={() => setGateMinimised(null)} />
      {error && <div className="border-b border-red-900 bg-red-950/50 px-4 py-1 text-xs text-red-200">{error.message}</div>}
      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)_380px] min-[1400px]:grid-cols-[350px_minmax(0,1fr)_400px] gap-3 p-3">
        <RequestPanel run={run} onRun={setRun} quoteByDoc={quoteByDoc} />
        <div className="flex min-h-0 flex-col gap-2">
          <AgentLanes events={events} state={run.state} pending={run.pending_human} selected={laneFilter} onSelect={(k) => setFilter(k ?? 'all')} />
          <TimelinePanel events={events} status={status} state={run.state} names={names} filter={filter} onFilter={setFilter} />
        </div>
        <DecisionPanel run={run} onRun={setRun} events={events} />
      </div>
      <HumanGate run={run} onRun={setRun} minimised={gateMinimised} onMinimise={setGateMinimised} />
    </div>
  )
}
