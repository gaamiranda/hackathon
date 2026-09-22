import type { WorkflowState } from '../api/types'
import { stateLabel } from '../labels'

const TONE: Partial<Record<WorkflowState, string>> = {
  CREATED: 'bg-zinc-800 text-zinc-300',
  EXTRACTING: 'bg-sky-900/60 text-sky-200',
  EXTRACTED: 'bg-sky-900/60 text-sky-200',
  NEEDS_HUMAN_EXTRACTION: 'bg-amber-900/70 text-amber-200 ring-1 ring-amber-500',
  CALC_MISMATCH: 'bg-red-900/70 text-red-200 ring-1 ring-red-500',
  VALIDATING: 'bg-sky-900/60 text-sky-200',
  ENRICHING: 'bg-violet-900/60 text-violet-200',
  SCORING: 'bg-violet-900/60 text-violet-200',
  RECOMMENDED: 'bg-emerald-900/70 text-emerald-200 ring-1 ring-emerald-500',
  NEGOTIATION_DRAFTED: 'bg-violet-900/60 text-violet-200',
  AWAITING_NEGOTIATION_APPROVAL: 'bg-amber-900/70 text-amber-200 ring-1 ring-amber-500',
  NEGOTIATING: 'bg-pink-900/60 text-pink-200',
  COUNTER_RECEIVED: 'bg-pink-900/60 text-pink-200',
  RE_SCORING: 'bg-violet-900/60 text-violet-200',
  AWAITING_PO_APPROVAL: 'bg-amber-900/70 text-amber-200 ring-1 ring-amber-500',
  PO_GENERATED: 'bg-emerald-600/80 text-white ring-1 ring-emerald-300',
  REPLANNING: 'bg-red-900/70 text-red-200 ring-1 ring-red-500',
}

/** Friendly state name; the raw workflow state stays in the tooltip for anyone reading the event log. */
export function StateBadge({ state, size = 'sm' }: { state: WorkflowState; size?: 'sm' | 'lg' }) {
  const cls = TONE[state] ?? 'bg-zinc-800 text-zinc-300'
  return (
    <span
      title={`state ${state}`}
      className={`inline-block whitespace-nowrap rounded px-2 font-semibold tracking-wide ${size === 'lg' ? 'py-1 text-sm' : 'py-0.5 text-xs'} ${cls}`}
    >
      {stateLabel(state)}
    </span>
  )
}
