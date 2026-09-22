/**
 * User-facing copy for internal identifiers (T16 copy pass): friendly state names (the raw state goes in a
 * tooltip), human-gate names, and the seven War Room lanes. Nothing here changes behaviour.
 */
import type { PendingHumanKind, WorkflowEvent, WorkflowState } from './api/types'

export const STATE_LABEL: Record<WorkflowState, string> = {
  CREATED: 'Created',
  EXTRACTING: 'Extracting quotes',
  EXTRACTED: 'Quotes extracted',
  NEEDS_HUMAN_EXTRACTION: 'Needs human extraction',
  VALIDATING: 'Validating',
  CALC_MISMATCH: 'Math check failed',
  ENRICHING: 'Checking supplier history',
  SCORING: 'Scoring',
  RECOMMENDED: 'Recommendation ready',
  NEGOTIATION_DRAFTED: 'Negotiation drafted',
  AWAITING_NEGOTIATION_APPROVAL: 'Awaiting negotiation approval',
  NEGOTIATING: 'Negotiating',
  COUNTER_RECEIVED: 'Counter-offer received',
  RE_SCORING: 'Re-scoring',
  AWAITING_PO_APPROVAL: 'Awaiting PO approval',
  PO_GENERATED: 'Purchase order generated',
  REPLANNING: 'Replanning',
}

export function stateLabel(state: WorkflowState | string): string {
  return STATE_LABEL[state as WorkflowState] ?? state
}

/** States in which the engine is between resting states (the Engine lane pulses). */
export const ENGINE_BUSY = new Set<WorkflowState>(['VALIDATING', 'ENRICHING', 'SCORING', 'RE_SCORING', 'REPLANNING', 'EXTRACTING'])

export const GATE_LABEL: Record<PendingHumanKind, string> = {
  extraction: 'Extraction review',
  calc_mismatch: 'Math check',
  negotiation_approval: 'Approve message',
  po_approval: 'Approve PO',
}

export function gateLabel(kind: PendingHumanKind | string): string {
  return GATE_LABEL[kind as PendingHumanKind] ?? kind.replace(/_/g, ' ')
}

/** The team a judge should see: four LLM/lookup agents, the guardrail judge, the deterministic engine, the human. */
export type LaneKey = 'document' | 'supplier_intel' | 'decision' | 'negotiation' | 'judge' | 'engine' | 'human'

export const LANES: { key: LaneKey; label: string; short: string; dot: string; text: string }[] = [
  { key: 'document', label: 'Document Agent', short: 'Document', dot: 'bg-violet-400', text: 'text-violet-300' },
  { key: 'supplier_intel', label: 'Supplier Intelligence', short: 'Supplier Intel', dot: 'bg-violet-400', text: 'text-violet-300' },
  { key: 'decision', label: 'Decision Agent', short: 'Decision', dot: 'bg-violet-400', text: 'text-violet-300' },
  { key: 'negotiation', label: 'Negotiation Agent', short: 'Negotiation', dot: 'bg-violet-400', text: 'text-violet-300' },
  { key: 'judge', label: 'Guardrail Judge', short: 'Judge', dot: 'bg-emerald-400', text: 'text-emerald-300' },
  { key: 'engine', label: 'Engine', short: 'Engine', dot: 'bg-sky-400', text: 'text-sky-300' },
  { key: 'human', label: 'Human', short: 'Human', dot: 'bg-amber-400', text: 'text-amber-300' },
]

export const LANE_BY_KEY = Object.fromEntries(LANES.map((l) => [l.key, l])) as Record<LaneKey, (typeof LANES)[number]>

const AGENT_KEYS = new Set<string>(['document', 'supplier_intel', 'decision', 'negotiation'])

/**
 * Which lane an event belongs to, from the stream alone: guardrail.* → judge; agent events by payload.agent, or by
 * type family when the payload names none (quote.extracted, negotiation.drafted); supplier replies sit in the
 * negotiation lane because that is the conversation they belong to; engine and human by actor.
 */
export function laneOf(e: WorkflowEvent): LaneKey | null {
  if (e.type.startsWith('guardrail.')) return 'judge'
  if (e.actor === 'agent') {
    const agent = e.payload.agent
    if (typeof agent === 'string' && AGENT_KEYS.has(agent)) return agent as LaneKey
    if (e.type.startsWith('negotiation.')) return 'negotiation'
    if (e.type.startsWith('quote.') || e.type.startsWith('extraction.')) return 'document'
    return 'decision'
  }
  if (e.actor === 'supplier') return 'negotiation'
  if (e.actor === 'engine') return 'engine'
  if (e.actor === 'human') return 'human'
  return null
}
