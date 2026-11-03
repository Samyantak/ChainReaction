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
