/* Pure graph derivations (no React/Cytoscape imports, so Node can
 * exercise the same code the UI runs). */

export const GRAPH_MAX_ZOOM = 1.15
export const GRAPH_MIN_FIT_ZOOM = 0.62

/* Edge look for the current selection. Every edge between visible nodes
 * is drawn (faint by default, so non-chain nodes never float loose);
 * the chain is always bold red; selecting a node lights its edges and
 * dims everything outside its neighborhood.
 * Returns 'hidden' | 'chain' | 'lit' | 'faint' | 'dim'. */
export function edgeState({ isChain, endsVisible, selected, sourceId, targetId }) {
  if (!endsVisible) return 'hidden'
  if (isChain) return 'chain'
  if (selected == null) return 'faint'
  return sourceId === selected || targetId === selected ? 'lit' : 'dim'
}

/* Legacy two-value form kept for callers that only need display/lit. */
export function describeEdge(args) {
  const s = edgeState(args)
  return [s === 'hidden' ? 'none' : 'element', s === 'lit']
}

/* Node visibility from the merged legend control: the status checkbox
 * must be on, behavior-tagged nodes additionally need BEHAVIOR on, and
 * the module search must match. */
export function nodeVisible({ status, behavior, id }, filters, query) {
  const statusOk = !!filters[status]
  const behaviorOk = !!filters.BEHAVIOR || !behavior
  const q = (query || '').trim().toLowerCase()
  const textOk = !q || String(id || '').toLowerCase().includes(q)
  return statusOk && behaviorOk && textOk
}

/* Node label: last two dotted segments (enough to tell siblings apart),
 * the package root keeps its full name. */
export function shortLabel(id) {
  const parts = String(id || '').split('.')
  return parts.length <= 2 ? parts.join('.') : parts.slice(-2).join('.')
}

/* Neighborhood of a node (itself plus direct importers/imports). */
export function neighborhood(edges, id) {
  const out = new Set(id ? [id] : [])
  if (!id) return out
  for (const e of edges || []) {
    if (e.source === id) out.add(e.target)
    if (e.target === id) out.add(e.source)
  }
  return out
}

/* Spine-and-ribs layout for graphs with a main chain: chain modules sit
 * on one horizontal line in tile order (break left, root right); every
 * other module hangs in a column under or over the chain module it
 * connects to (closest to the break wins), alternating sides so columns
 * stay short. Modules with no chain neighbor get their own columns at
 * the far right. Returns {id: {x, y}} or null without a chain. */
export function spineLayout(nodes, edges, chainOrder, { colGap = 235, rowGap = 50, maxPerSide = 10 } = {}) {
  const chain = (chainOrder || []).filter((id) => nodes.some((n) => n.id === id))
  if (chain.length < 2) return null
  const rank = new Map(chain.map((id, i) => [id, i]))
  const attach = new Map()
  for (const e of edges || []) {
    for (const [a, b] of [[e.source, e.target], [e.target, e.source]]) {
      if (rank.has(a) || !rank.has(b)) continue
      const cur = attach.get(a)
      if (cur == null || rank.get(b) < cur) attach.set(a, rank.get(b))
    }
  }
  const columns = chain.map(() => [])
  const loose = []
  for (const n of nodes) {
    if (rank.has(n.id)) continue
    if (attach.has(n.id)) columns[attach.get(n.id)].push(n.id)
    else loose.push(n.id)
  }
  if (loose.length) columns.push(loose)
  // Each chain column is a block of k sub-columns (k grows with its
  // module count), so big graphs widen instead of overlapping.
  const perSub = 2 * maxPerSide
  const pos = {}
  let x = 0
  columns.forEach((ids, i) => {
    const k = Math.max(1, Math.ceil(ids.length / perSub))
    const center = x + ((k - 1) * colGap) / 2
    if (i < chain.length) pos[chain[i]] = { x: center, y: 0 }
    ids.sort().forEach((id, j) => {
      const sub = Math.floor(j / perSub)
      const within = j % perSub
      const side = within % 2 === 0 ? 1 : -1
      const row = Math.floor(within / 2) + 1
      pos[id] = { x: center + (sub - (k - 1) / 2) * colGap, y: side * row * rowGap }
    })
    x += k * colGap
  })
  return pos
}
