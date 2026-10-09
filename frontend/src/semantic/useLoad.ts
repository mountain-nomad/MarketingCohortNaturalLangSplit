import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage, type Query } from '../api/client.ts'

/**
 * GET `path` on mount and whenever `reload()` is called. Previous data stays visible while
 * a reload is in flight, so lists do not flicker after an action.
 */
export function useLoad<T>(path: string, query?: Query) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const queryKey = JSON.stringify(query ?? {})

  useEffect(() => {
    let cancelled = false
    api<T>(path, { query: JSON.parse(queryKey) as Query })
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [path, queryKey, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, reload }
}
