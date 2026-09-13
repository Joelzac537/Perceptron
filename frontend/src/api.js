import { useCallback, useEffect, useRef, useState } from 'react'

/* Backend origin. Override with VITE_API in .env.local if the server is not on 8000. */
export const API = import.meta.env.VITE_API ?? 'http://127.0.0.1:8000'

async function get(path) {
  const response = await fetch(`${API}${path}`)
  if (!response.ok) throw new Error(`${path} → ${response.status}`)
  return response.json()
}

/* Poll one endpoint.
 *
 * Keeps the last good value on a failed refresh rather than blanking the panel: the
 * server restarting should not wipe the screen mid-demo. `error` surfaces separately so
 * the header can say so honestly instead of silently showing stale numbers.
 */
export function usePoll(path, intervalMs = 3000, enabled = true) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const alive = useRef(true)

  // Derived, not stored: a disabled or path-less hook is simply "not loading", and
  // setting that inside the effect would trigger a second render on every mount.
  const loading = enabled && !!path && data === null && error === null

  const refresh = useCallback(async () => {
    if (!path) return
    try {
      const next = await get(path)
      if (!alive.current) return
      setData(next)
      setError(null)
    } catch (exc) {
      if (alive.current) setError(exc.message)
    }
  }, [path])

  useEffect(() => {
    alive.current = true
    if (!enabled || !path) return undefined
    // set-state-in-effect does not apply: refresh is async, so every setState inside it
    // runs in a microtask after the fetch resolves, never synchronously during this
    // effect. This is the "subscribe to an external system" case the rule exempts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh()
    const timer = setInterval(refresh, intervalMs)
    return () => {
      alive.current = false
      clearInterval(timer)
    }
  }, [refresh, intervalMs, enabled, path])

  return { data, error, loading, refresh }
}

export async function injectEvent(body) {
  const response = await fetch(`${API}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return response.json()
}
