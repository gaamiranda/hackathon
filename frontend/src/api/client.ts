import type { Comparison, CreateRunInput, Health, InterruptInput, NegotiationThread, PurchaseOrder, Run, RunOverview, RunSummary, WorkflowEvent } from './types'

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  code: string | null
  /** Set on 422 policy_violation (edited negotiation message failed the outbound filter, G3). */
  violations: string[]
  constructor(status: number, code: string | null, message: string, violations: string[] = []) {
    super(message)
    this.status = status
    this.code = code
    this.violations = violations
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, init)
  } catch (err) {
    throw new ApiError(0, 'network', `Cannot reach backend at ${API_URL} (${(err as Error).message})`)
  }
  if (!res.ok) {
    let code: string | null = null
    let message = `${res.status} ${res.statusText}`
    let violations: string[] = []
    try {
      const body = (await res.json()) as { code?: string; message?: string; detail?: unknown; violations?: unknown }
      code = body.code ?? null
      message = body.message ?? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body))
      if (Array.isArray(body.violations)) violations = body.violations.filter((v): v is string => typeof v === 'string')
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message, violations)
  }
  return (await res.json()) as T
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => request<Health>('/health'),

  listRuns: () => request<RunSummary[]>('/runs'),

  createRun: (input: CreateRunInput) =>
    request<{ run_id: string; run: Run }>('/runs', json('POST', { request: input })),

  getRun: (runId: string) => request<Run>(`/runs/${runId}`),

  /** Header strip figures (T16); refetched on the same triggers as getRun. */
  getOverview: (runId: string) => request<RunOverview>(`/runs/${runId}/summary`),

  /** Manual comparison matrix (T17): engine figures per supplier; 409 not_validated before the engine has run. */
  getComparison: (runId: string) => request<Comparison>(`/runs/${runId}/comparison`),

  uploadDocuments: (runId: string, files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f, f.name))
    return request<Run>(`/runs/${runId}/documents`, { method: 'POST', body: form })
  },

  uploadEmailText: (runId: string, emailText: string, filename = 'email.txt') =>
    request<Run>(`/runs/${runId}/documents`, json('POST', { email_text: emailText, filename })),

  replaceDocument: (runId: string, docId: string, file: File) => {
    const form = new FormData()
    form.append('files', file, file.name)
    return request<Run>(`/runs/${runId}/documents/${docId}/replace`, { method: 'POST', body: form })
  },

  evaluate: (runId: string) => request<Run>(`/runs/${runId}/evaluate`, { method: 'POST' }),

  correctQuote: (runId: string, quoteId: string, patch: Record<string, unknown>) =>
    request<Run>(`/runs/${runId}/quotes/${quoteId}/correct`, json('POST', { patch })),

  confirmMath: (runId: string, quoteId: string, useComputed: boolean) =>
    request<Run>(`/runs/${runId}/quotes/${quoteId}/confirm-math`, json('POST', { use_computed: useComputed })),

  /** RECOMMENDED → agent drafts for the top eligible supplier → AWAITING_NEGOTIATION_APPROVAL. */
  negotiate: (runId: string) => request<Run>(`/runs/${runId}/negotiate`, { method: 'POST' }),

  /** Human gate (G5). Pass `message` only when the draft was edited; the backend re-filters it (422 policy_violation). */
  approveNegotiation: (runId: string, supplierId: string, message?: string) =>
    request<Run>(`/runs/${runId}/negotiation/${supplierId}/approve`, json('POST', message === undefined ? {} : { message })),

  /** Mid-workflow requirement change → REPLANNING → RECOMMENDED with run.replan_impact (409 illegal_transition | no_change). */
  interrupt: (runId: string, body: InterruptInput) => request<Run>(`/runs/${runId}/interrupt`, json('POST', body)),

  listNegotiations: (runId: string) => request<Record<string, NegotiationThread>>(`/runs/${runId}/negotiations`),

  /** RECOMMENDED → AWAITING_PO_APPROVAL with run.po_preview (409 no_recommendation | supplier_ineligible). */
  requestPo: (runId: string) => request<Run>(`/runs/${runId}/request-po`, { method: 'POST' }),

  /** Human gate (G5): the only call that creates a purchase order. 409 totals_changed if the engine's figures moved. */
  approvePo: (runId: string, approvedBy: string) => request<Run>(`/runs/${runId}/approve-po`, json('POST', { approved_by: approvedBy })),

  /** AWAITING_PO_APPROVAL → RECOMMENDED; the preview is dropped. */
  rejectPo: (runId: string, reason: string) => request<Run>(`/runs/${runId}/reject-po`, json('POST', { reason })),

  /** 404 until the PO has been generated. */
  getPo: (runId: string) => request<PurchaseOrder>(`/runs/${runId}/po`),

  /** Download link for the generated PO (application/pdf; 404 until generated). */
  poPdfUrl: (runId: string) => `${API_URL}/runs/${runId}/po.pdf`,

  listEvents: (runId: string, since = -1) => request<WorkflowEvent[]>(`/runs/${runId}/events?since=${since}`),

  /** URL for EventSource; the backend replays events with seq > since, then streams live ones. */
  streamUrl: (runId: string, since = -1) => `${API_URL}/runs/${runId}/events/stream?since=${since}`,
}
