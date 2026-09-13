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
      // The backend sends no `event:` line, so every message arrives here; the type is inside the JSON.
      source.onmessage = (raw: MessageEvent) => {
        const event = JSON.parse(raw.data as string) as WorkflowEvent
        if (event.seq <= lastSeq.current) return
        lastSeq.current = event.seq
        setEvents((prev) => [...prev, event])
        onEventRef.current?.(event)
      }
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
