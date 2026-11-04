/* Pure Fix derivations (no React imports, so Node can exercise the
 * same code the UI runs). */

export function parseDiff(text) {
  const rows = []
  for (const line of String(text || '').split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---')) {
      rows.push({ type: 'meta', text: line })
    } else if (line.startsWith('@@')) {
      rows.push({ type: 'hunk', text: line })
    } else if (line.startsWith('+')) {
      rows.push({ type: 'add', text: line.slice(1) })
    } else if (line.startsWith('-')) {
      rows.push({ type: 'del', text: line.slice(1) })
    } else if (line.startsWith('\\')) {
      rows.push({ type: 'meta', text: line })
    } else {
      rows.push({ type: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line })
    }
  }
  if (rows.length > 0 && rows[rows.length - 1].text === '' && String(text || '').endsWith('\n')) {
    rows.pop()
  }
  return rows
}

/* Red-to-green counter series: initial failures, then each round's
 * remaining failures. Null when the fixture/report lacks counts. */
export function counterSeries(fix) {
  const before = fix?.tests_before?.failed
  if (before == null) return null
  const series = [before]
  for (const round of fix?.rounds || []) {
    const failed = round?.tests_after?.failed
    if (failed == null) return null
    series.push(failed)
  }
  return series
}
