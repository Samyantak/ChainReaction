/* Pure Interpret derivations (no React imports, so Node can exercise
 * the same code the UI runs). Tolerant of old (nibabel) and new (M3)
 * changeset shapes: every helper returns null/[] when its inputs are
 * absent instead of throwing. */

export function tallyKinds(items) {
  const counts = {}
  for (const item of items || []) {
    const kind = item?.kind || 'unknown'
    counts[kind] = (counts[kind] || 0) + 1
  }
  return Object.entries(counts).sort(([a, na], [b, nb]) => nb - na || (a < b ? -1 : 1))
}

export function survivalRate(changeset, items) {
  const list = items || []
  const total = list.length
  if (changeset?.interpret_survival_rate != null) {
    const verified = list.filter((i) => i.verified).length
    return { verified, total, rate: changeset.interpret_survival_rate }
  }
  if (total === 0) return { verified: 0, total: 0, rate: null }
  const verified = list.filter((i) => i.verified).length
  return { verified, total, rate: verified / total }
}

export function blindLine(changeset) {
  const table = changeset?.interpret_table_size || 0
  if (!table) return null
  const missed = (changeset?.interpret_missed || []).length
  const found = table - missed
  const blind = changeset?.interpret_blind_hits ?? 0
  return `Muse Spark alone ${blind}/${table} → with table extraction ${found}/${table}`
}

export function detMethodsLine(changeset) {
  const det = changeset?.interpret_det_by_method
  if (!det) return null
  const parts = Object.entries(det)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([method, n]) => `${method} ${n}`)
  return parts.length > 0 ? `detection by method: ${parts.join(' · ')}` : null
}

export function normalizeDropped(changeset) {
  const dropped = changeset?.dropped
  if (Array.isArray(dropped) && dropped.length > 0) {
    return dropped.map((d) => ({
      symbol: d?.symbol || '(unknown)',
      kind: d?.kind || null,
      reason: d?.reason || null,
    }))
  }
  const symbols = changeset?.dropped_symbols
  if (Array.isArray(symbols)) {
    return symbols.map((s) => ({ symbol: String(s), kind: null, reason: null }))
  }
  return []
}
