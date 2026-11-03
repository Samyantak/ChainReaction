import { useEffect, useState } from 'react'

/* Small fetch helpers. Every hook returns { data, error, loading } and
 * never throws during render — views handle all three states. */

export async function getJSON(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.json()
}

export async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = ''
    try {
      detail = (await res.json()).detail || ''
    } catch {
      detail = await res.text()
    }
    throw new Error(detail || `${url}: HTTP ${res.status}`)
  }
  return res.json()
}

export function useApi(url) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!url) return
    let live = true
    setError(null)
    getJSON(url)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message || e)))
    return () => {
      live = false
    }
  }, [url])
  return { data, error, loading: !data && !error }
}

/* Server status for the header key pill. Returns null until loaded. */
export function useStatus() {
  const { data } = useApi('/api/status')
  return data
}
