/* Pure New/History helpers (no React imports, so Node can exercise
 * the same code the UI runs). */

/* GitHub repo URL shape: https://github.com/owner/repo with an optional
 * trailing slash or .git. Returns an error string or null when valid. */
export function validateGithubUrl(value) {
  const url = (value || '').trim()
  if (!url) return 'Repo URL is required.'
  const m = url.match(/^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+?)(\.git)?\/?$/i)
  if (!m) return 'Use a GitHub URL like https://github.com/owner/repo.'
  return null
}

/* Short owner/repo (or the raw host path) for run lists. Never throws. */
export function repoShort(url) {
  if (!url) return ''
  try {
    const u = new URL(url)
    const path = u.pathname.replace(/\/$/, '').replace(/\.git$/, '')
    const parts = path.split('/').filter(Boolean)
    if (parts.length >= 2) return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`
    return parts.join('/') || u.host
  } catch {
    return url
  }
}

/* Decision preview line for the New-analysis form. Every branch is driven
 * by the POST /api/parse-decision response (nulls when unrecognized). */
export function decisionPreviewText(parsed) {
  if (!parsed || !parsed.package) return 'Free-form decision — the model interprets it at run time.'
  const from = parsed.from_version ? ` from ${parsed.from_version}` : ''
  return `Package ${parsed.package}${from} → ${parsed.to_version ?? '(no version)'}.`
}

/* Run duration from manifest timestamps ("1:23", "2h04", or null when the
 * run is still active or timestamps are missing). */
export function formatDuration(createdAt, finishedAt) {
  if (!createdAt || !finishedAt) return null
  const ms = new Date(finishedAt).getTime() - new Date(createdAt).getTime()
  if (!Number.isFinite(ms) || ms < 0) return null
  const s = Math.floor(ms / 1000)
  if (s < 3600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const h = Math.floor(s / 3600)
  return `${h}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`
}
