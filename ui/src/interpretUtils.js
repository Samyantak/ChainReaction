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

/* Text-fragment link that opens the source at the quote. Repairs the
 * "#section#:~:text=" form (a second '#' breaks the directive) and
 * builds one from the changeset's source URL when the item has none. */
export function quoteLink(item, fallbackUrl) {
  const link = item?.link || ''
  if (link && !/^[\w./-]+\.py:\d+$/.test(link)) {
    const i = link.indexOf('#:~:text=')
    if (i > 0 && link.slice(0, i).includes('#')) return link.slice(0, i) + link.slice(i + 1)
    return link
  }
  const base = item?.source_url || fallbackUrl || ''
  const quote = String(item?.quote || '').replace(/\s+/g, ' ').trim()
  if (!base || !/^https?:/.test(base)) return null
  if (!quote) return base
  const words = quote.split(' ').slice(0, 8).join(' ')
  const directive = `:~:text=${encodeURIComponent(words)}`
  return base.includes('#') ? `${base}${directive}` : `${base}#${directive}`
}

/* Code-aware items anchor to the repo's own source, not the guide. */
export function codeAnchor(item) {
  if (item?.code_file) return `${item.code_file}${item.code_line ? `:${item.code_line}` : ''}`
  if (/^[\w./-]+\.py:\d+$/.test(item?.link || '')) return item.link
  return null
}

export function filterItems(items, { q = '', kind = 'all', method = 'all', usedOnly = false, used = new Set() } = {}) {
  const needle = q.trim().toLowerCase()
  return (items || [])
    .filter((it) => kind === 'all' || (it.kind || 'unknown') === kind)
    .filter((it) => method === 'all' || (it.extraction || 'model') === method)
    .filter((it) => !usedOnly || used.has(it.symbol))
    .filter(
      (it) =>
        !needle ||
        String(it.symbol || '').toLowerCase().includes(needle) ||
        String(it.quote || '').toLowerCase().includes(needle) ||
        String(it.replacement || '').toLowerCase().includes(needle),
    )
    .sort((a, b) => Number(used.has(b.symbol)) - Number(used.has(a.symbol)))
}

export function tallyMethods(items) {
  const counts = {}
  for (const it of items || []) {
    const m = it?.extraction || 'model'
    counts[m] = (counts[m] || 0) + 1
  }
  return Object.entries(counts).sort(([, a], [, b]) => b - a)
}

/* Recall bar parts: what the model found blind, what deterministic
 * extraction added, what is still missing. Null without a table. */
export function recallParts(changeset) {
  const table = changeset?.interpret_table_size || 0
  if (!table) return null
  const missed = (changeset?.interpret_missed || []).length
  const blind = Math.min(changeset?.interpret_blind_hits ?? 0, table)
  const found = table - missed
  return { table, blind, added: Math.max(0, found - blind), missed }
}
