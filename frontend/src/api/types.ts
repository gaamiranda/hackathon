/**
 * Hand-written from backend/procureai/domain/schema/*.json (source of truth).
 * Money fields are JSON strings ("12.80"); never parse to float for display.
 */

export type Money = string
export type QuoteSource = 'pdf' | 'xlsx' | 'email'
export type EventActor = 'agent' | 'engine' | 'human' | 'supplier'

export type WorkflowState =
  | 'CREATED'
  | 'EXTRACTING'
  | 'EXTRACTED'
  | 'NEEDS_HUMAN_EXTRACTION'
  | 'VALIDATING'
  | 'CALC_MISMATCH'
  | 'ENRICHING'
  | 'SCORING'
  | 'RECOMMENDED'
  | 'NEGOTIATION_DRAFTED'
  | 'AWAITING_NEGOTIATION_APPROVAL'
  | 'NEGOTIATING'
  | 'COUNTER_RECEIVED'
  | 'RE_SCORING'
  | 'AWAITING_PO_APPROVAL'
  | 'PO_GENERATED'
  | 'REPLANNING'

export interface ProcurementRequest {
  id: string
  product: string
  quantity: number
  required_by: string
  budget: Money
  currency: string
  created_at: string
  version: number
}

export interface ScoringWeights {
  price: number
  lead_time: number
  reliability: number
  risk: number
}

export interface ProcurementConfig {
  weights: ScoringWeights
  thresholds: { max_defect_rate: number; min_confidence: number; tie_margin: number }
  negotiation: { max_discount_ask_pct: string; min_lead_time_days: number; max_rounds: number }
  approvals: { negotiation_send: boolean; po_generation: boolean }
  tax_rate_pct: string
}

export interface RawDocument {
  doc_id: string
  filename: string
  source: QuoteSource
  text: string
}

export interface NormalizedQuote {
  quote_id: string
  /** RawDocument this quote was extracted from; null for hand-written fixtures. */
  doc_id: string | null
  supplier_id: string
  supplier_name: string
  source: QuoteSource
  unit_price: Money
  currency: string
  quantity_quoted: number
  moq: number
  lead_time_days: number
  payment_terms: string | null
  shipping_cost: Money
  discount_pct: string
  validity_date: string | null
  capacity_units: number | null
  llm_stated_total: Money | null
  field_confidence: Record<string, number>
  raw_excerpt: string
}

export interface QuoteChecks {
  moq_ok: boolean
  lead_time_ok: boolean
  budget_ok: boolean
  math_ok: boolean
  capacity_ok: boolean
}

export interface ValidatedQuote extends NormalizedQuote {
  subtotal: Money
  discount: Money
  shipping: Money
  pre_tax_total: Money
  tax: Money
  landed_cost: Money
  checks: QuoteChecks
  issues: string[]
}

export interface Scorecard {
  supplier_id: string
  landed_cost: Money
  lead_time_days: number
  reliability_score: number
  risk_score: number
  total_score: number
  score_breakdown: Record<string, number>
  eligible: boolean
  ineligibility_reasons: string[]
}

export interface Recommendation {
  run_id: string
  request_version: number
  ranked: string[]
  recommended_supplier_id: string | null
  rationale: string
  change_explanation: string | null
  escalation: { reason: string; details: Record<string, unknown> } | null
}

export type PendingHumanKind = 'extraction' | 'calc_mismatch'

export interface CalcMismatchDetail {
  supplier_id: string
  computed_pre_tax_total: Money
  stated_total: Money
}

export interface PendingHuman {
  kind: PendingHumanKind
  quote_ids: string[]
  message: string
  /** calc_mismatch: { [quote_id]: CalcMismatchDetail }
   *  extraction:    { fields: { [quote_id]: string[] }, failed_documents: { [doc_id]: string } } */
  details: Record<string, unknown>
}

export interface Run {
  run_id: string
  request: ProcurementRequest
  config: ProcurementConfig
  state: WorkflowState
  documents: RawDocument[]
  quotes: NormalizedQuote[]
  validated: ValidatedQuote[]
  scorecards: Scorecard[]
  recommendation: Recommendation | null
  pending_human: PendingHuman | null
  created_at: string
  updated_at: string
}

export interface WorkflowEvent {
  run_id: string
  seq: number
  ts: string
  actor: EventActor
  type: string
  state_before: WorkflowState | null
  state_after: WorkflowState | null
  payload: Record<string, unknown>
  summary: string
}

export interface RunSummary {
  run_id: string
  state: WorkflowState
  product: string
  quantity: number
  created_at: string
}

export interface CreateRunInput {
  product: string
  quantity: number
  required_by: string
  budget: Money
  currency: string
}

export interface Health {
  mode: 'mock' | 'live'
  llm_gateway: string
  openclaw: string
  runs: number
}
