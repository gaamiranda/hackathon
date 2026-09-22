import { createContext, useContext, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Health } from '../api/types'

export const HEALTH_POLL_MS = 15_000

/** GET /health now and every 15 s (T17): the LLM route status drives the degraded / manual-mode banner. */
export function useHealthPolling(): Health | null {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    let cancelled = false
    const tick = () => {
      api
        .health()
        .then((h) => !cancelled && setHealth(h))
        .catch(() => !cancelled && setHealth(null))
    }
    tick()
    const timer = window.setInterval(tick, HEALTH_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])
  return health
}

export const HealthContext = createContext<Health | null>(null)

/** The polled /health snapshot (null until the first answer or while the backend is unreachable). */
export function useHealth(): Health | null {
  return useContext(HealthContext)
}
