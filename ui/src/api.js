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

export async function getText(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.text()
}

/* Optional fetches must never 404 in the console: the artifact
 * route answers ?optional=1 with an empty 200 when the file is
 * absent (JSON null, text ""). Unknown query params are ignored
 * elsewhere, so API routes are unaffected. */
export function withOptional(url) {
  if (!url) return url
  return url.includes('?') ? `${url}&optional=1` : `${url}?optional=1`
}

/* Plain-text twin of useApi, for .log / .md artifacts. */
export function useText(url) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!url) return
    let live = true
    setError(null)
    getText(withOptional(url))
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message || e)))
    return () => {
      live = false
    }
  }, [url])
  return { data, error, loading: !data && !error }
}

/* Optional JSON: a missing artifact is data=null, not an error.
 * Sections Session A hasn't produced yet hide instead of failing. */
export function useOptionalJson(url) {
  const { data } = useApi(withOptional(url))
  return data
}

/* Server status for the header key pill. Returns null until loaded. */
export function useStatus() {
  const { data } = useApi('/api/status')
  return data
}
