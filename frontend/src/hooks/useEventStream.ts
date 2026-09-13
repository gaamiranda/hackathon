import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ApiError } from '../api/client'
import type { WorkflowEvent } from '../api/types'

export type StreamStatus = 'connecting' | 'live' | 'disconnected' | 'gone'

const RECONNECT_MS = 2000

/**
 * Subscribes to GET /runs/{id}/events/stream with EventSource.
 * The server replays stored events on connect, so nothing else is fetched.
 * On error we reopen with since=<last seq> after a short delay; events are de-duplicated by seq.
 */
export function useEventStream(runId: string | undefined, onEvent?: (e: WorkflowEvent) => void) {
  const [events, setEvents] = useState<WorkflowEvent[]>([])
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const lastSeq = useRef(-1)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  const reset = useCallback(() => {
    lastSeq.current = -1
    setEvents([])
  }, [])

  useEffect(() => {
    if (!runId) return
    reset()
    let source: EventSource | null = null
    let timer: number | undefined
    let closed = false

    const open = () => {
      if (closed) return
      setStatus('connecting')
      source = new EventSource(api.streamUrl(runId, lastSeq.current))
      source.onopen = () => setStatus('live')
      // The backend sets `event: <type>` per message, so `onmessage` never fires; catch all via a wildcard listener.
      // EventSource has no wildcard, so we listen on the known types and fall back to a generic handler.
      const handle = (raw: MessageEvent) => {
        const event = JSON.parse(raw.data as string) as WorkflowEvent
        if (event.seq <= lastSeq.current) return
        lastSeq.current = event.seq
        setEvents((prev) => [...prev, event])
        onEventRef.current?.(event)
      }
      for (const type of EVENT_TYPES) source.addEventListener(type, handle)
      source.onerror = () => {
        source?.close()
        // EventSource hides the HTTP status; probe the run so a 404 (backend restarted, in-memory store lost)
        // becomes a final "gone" instead of an endless retry.
        api
          .getRun(runId)
          .then(() => {
            setStatus('disconnected')
            timer = window.setTimeout(open, RECONNECT_MS)
          })
          .catch((err: ApiError) => {
            if (err.status === 404) {
              setStatus('gone')
              return
            }
            setStatus('disconnected')
            timer = window.setTimeout(open, RECONNECT_MS)
          })
      }
    }
    open()

    return () => {
      closed = true
      window.clearTimeout(timer)
      source?.close()
    }
  }, [runId, reset])

  return { events, status }
}

/** Event contract (PLAN.md §6) + Week 2 placeholders. EventSource needs explicit listeners per `event:` name. */
export const EVENT_TYPES = [
  'run.created',
  'documents.added',
  'document.replaced',
  'agent.started',
  'agent.finished',
  'agent.failed',
  'quote.extracted',
  'extraction.needs_human',
  'extraction.completed',
  'quote.corrected',
  'extraction.resumed',
  'validation.started',
  'quotes.validated',
  'calc.mismatch',
  'quote.math_confirmed',
  'quote.rejected',
  'mismatch.resolved',
  'enrichment.started',
  'scoring.started',
  'quotes.scored',
  'recommendation.ranked',
  'recommendation.ready',
  // Week 2+
  'negotiation.drafted',
  'negotiation.approved',
  'negotiation.sent',
  'supplier.counter_offer',
  'requirement.changed',
  'replan.started',
  'replan.completed',
  'po.generated',
] as const
