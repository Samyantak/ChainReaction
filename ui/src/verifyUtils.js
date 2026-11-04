/* Pure Verify-view derivations (no React imports, so Node can
 * exercise the same code the UI runs).
 *
 * - describeMatch: the Predicted-vs-actual badge. Never green when the
 *   after import failed without a captured target chain (flask-login:
 *   ImportError in site-packages, actual [] matching spine [] is a
 *   vacuous MATCH, not proof).
 * - describeVersions: BEFORE/AFTER package version lines. Generic runs
 *   read the decided package out of envs.json (decision.package +
 *   before/after freeze); older reports without envs.json fall back to
 *   the numpy-only report fields.
 */

export function describeMatch(spineMatch, firstAfter) {
  if (spineMatch == null) return null
  const failedEmpty =
    firstAfter != null &&
    firstAfter.ok === false &&
    (firstAfter.actual || []).length === 0
  if (spineMatch === true && failedEmpty) return 'not-compared'
  return spineMatch ? 'match' : 'differ'
}

function freezeLookup(freeze, pkg) {
  if (!freeze || !pkg) return null
  if (freeze[pkg] != null) return freeze[pkg]
  const lower = String(pkg).toLowerCase()
  for (const [k, v] of Object.entries(freeze)) {
    if (String(k).toLowerCase() === lower) return v
  }
  const alt = lower.replace(/_/g, '-')
  const alt2 = lower.replace(/-/g, '_')
  for (const [k, v] of Object.entries(freeze)) {
    const kl = String(k).toLowerCase()
    if (kl === alt || kl === alt2) return v
  }
  return null
}

export function describeVersions(report, envs) {
  const pkg = envs?.decision?.package || null
  if (pkg) {
    const beforeFreeze = envs?.before?.freeze || null
    const afterFreeze = envs?.after?.freeze || null
    const beforeVer =
      freezeLookup(beforeFreeze, pkg) ||
      envs?.decision?.from_version ||
      report?.before_numpy ||
      null
    const afterVer =
      freezeLookup(afterFreeze, pkg) ||
      envs?.decision?.to_version ||
      report?.numpy_version ||
      null
    return {
      before: beforeVer ? `${pkg} ${beforeVer}` : null,
      after: afterVer ? `${pkg} ${afterVer}` : null,
    }
  }
  const beforeVer = report?.before_numpy || null
  const afterVer = report?.numpy_version || null
  return {
    before: beforeVer ? `numpy ${beforeVer}` : null,
    after: afterVer ? `numpy ${afterVer}` : null,
  }
}

/* Test ids grouped by file, biggest groups first: [[file, [names]]]. */
export function groupByFile(ids, needle = '') {
  const q = String(needle || '').trim().toLowerCase()
  const groups = new Map()
  for (const raw of ids || []) {
    const id = String(raw).replace(/\\/g, '/')
    if (q && !id.toLowerCase().includes(q)) continue
    const cut = id.indexOf('::')
    const file = cut >= 0 ? id.slice(0, cut) : id
    const name = cut >= 0 ? id.slice(cut + 2) : ''
    if (!groups.has(file)) groups.set(file, [])
    groups.get(file).push(name || file)
  }
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length || (a[0] < b[0] ? -1 : 1))
}

/* Prediction overlap: true positives, missed, false alarms. */
export function testOverlap(scores) {
  if (!scores || !Array.isArray(scores.actual_new)) return null
  const actual = new Set(scores.actual_new)
  const predicted = new Set(scores.predicted_failing || [])
  return {
    hits: [...actual].filter((t) => predicted.has(t)).sort(),
    missed: [...actual].filter((t) => !predicted.has(t)).sort(),
    falseAlarms: [...predicted].filter((t) => !actual.has(t)).sort(),
  }
}

/* "AttributeError: `np.NaN` was removed…" -> ["AttributeError", "`np.NaN` was removed…"] */
export function splitSignature(message) {
  const m = /^\s*([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt))\s*:\s*([\s\S]*)$/.exec(String(message || ''))
  return m ? [m[1], m[2]] : [null, String(message || 'other')]
}
