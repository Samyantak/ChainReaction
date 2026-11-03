import { useEffect, useState } from 'react'

/* Hash router (M6 §A): no FastAPI changes needed, no extra dependency.
 * Routes: #/ #/features/:id #/new #/runs/:id/:view #/history */

const RUN_VIEWS = ['setup', 'interpret', 'investigate', 'graph', 'verify', 'fix', 'report']

export function parseHash(hash) {
  const path = (hash || '#/').replace(/^#/, '') || '/'
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return { name: 'landing', params: {} }
  if (parts[0] === 'features' && parts[1]) {
    return { name: 'feature', params: { id: decodeURIComponent(parts[1]) } }
  }
  if (parts[0] === 'new') return { name: 'new', params: {} }
  if (parts[0] === 'history') return { name: 'history', params: {} }
  if (parts[0] === 'runs' && parts[1]) {
    const view = parts[2] && RUN_VIEWS.includes(parts[2]) ? parts[2] : 'setup'
    return { name: 'run', params: { id: decodeURIComponent(parts[1]), view } }
  }
  return { name: 'notfound', params: { path } }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function navigate(path) {
  window.location.hash = `#${path}`
}

export { RUN_VIEWS }
