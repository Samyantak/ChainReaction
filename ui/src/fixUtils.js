/* Pure Fix derivations (no React imports, so Node can exercise the
 * same code the UI runs). The view model follows fix.json exactly as
 * fixloop.finalize() writes it — status, stop_reason, seeded, rounds,
 * counts, guards {ok, violations}, patch, touched, upstream. */

export function parseDiff(text) {
  const rows = []
  let oldNo = 0
  let newNo = 0
  for (const line of String(text || '').split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---')) {
      rows.push({ type: 'meta', text: line })
    } else if (line.startsWith('@@')) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line)
      if (m) {
        oldNo = Number(m[1])
        newNo = Number(m[2])
      }
      rows.push({ type: 'hunk', text: line })
    } else if (line.startsWith('+')) {
      rows.push({ type: 'add', text: line.slice(1), newNo: newNo++ })
    } else if (line.startsWith('-')) {
      rows.push({ type: 'del', text: line.slice(1), oldNo: oldNo++ })
    } else if (line.startsWith('\\')) {
      rows.push({ type: 'meta', text: line })
    } else {
      rows.push({
        type: 'ctx',
        text: line.startsWith(' ') ? line.slice(1) : line,
        oldNo: oldNo++,
        newNo: newNo++,
      })
    }
  }
  if (rows.length > 0 && rows[rows.length - 1].text === '' && String(text || '').endsWith('\n')) {
    rows.pop()
  }
  return rows
}

/* Files a unified diff touches, in order ("b/" side, prefix dropped). */
export function diffFiles(text) {
  const files = []
  for (const line of String(text || '').split('\n')) {
    if (line.startsWith('+++ ')) {
      const path = line.slice(4).replace(/^b\//, '').trim()
      if (path && path !== '/dev/null' && !files.includes(path)) files.push(path)
    }
  }
  return files
}

/* Side-by-side pairing: each del run pairs with the add run after it;
 * context rows sit on both sides. Meta rows are dropped (the file name
 * heads the split view instead). */
export function splitDiff(rows) {
  const out = []
  let dels = []
  let adds = []
  const flush = () => {
    const n = Math.max(dels.length, adds.length)
    for (let i = 0; i < n; i += 1) {
      out.push({ left: dels[i] || null, right: adds[i] || null })
    }
    dels = []
    adds = []
  }
  for (const row of rows) {
    if (row.type === 'del') {
      if (adds.length) flush()
      dels.push(row)
    } else if (row.type === 'add') {
      adds.push(row)
    } else {
      flush()
      if (row.type === 'ctx') out.push({ left: row, right: row })
      else if (row.type === 'hunk') out.push({ hunk: row.text })
    }
  }
  flush()
  return out
}

const STOP_TEXT = {
  green: 'no decision-caused failures remain',
  'no-progress-twice': 'stopped after two rounds without progress',
  'max-rounds': 'round limit reached',
  'llm-error': 'stopped: the model call failed',
  'stage-timeout': 'stopped: stage time limit reached',
  'suite-timeout': 'stopped: the test suite timed out',
  'skipped-clean': 'skipped: no decision-caused failures to fix',
}

export function stopText(reason) {
  if (!reason) return ''
  return STOP_TEXT[reason] || String(reason).replace(/-/g, ' ')
}

const VERDICT = {
  green: { tone: 'ok', label: 'GREEN', note: 'every decision-caused failure fixed' },
  partial: { tone: 'warn', label: 'PARTIAL', note: 'some breaks fixed, some remain' },
  failed: { tone: 'flag', label: 'FAILED', note: 'no verified fix' },
  unverified: { tone: 'flag', label: 'UNVERIFIED', note: 'a guard check failed' },
  skipped: { tone: 'mute', label: 'SKIPPED', note: 'nothing to fix' },
}

export function verdict(status) {
  return VERDICT[status] || { tone: 'mute', label: String(status || 'unknown').toUpperCase(), note: '' }
}

const len = (v) => (Array.isArray(v) ? v.length : typeof v === 'number' ? v : 0)

function countsLine(counts) {
  if (!counts) return null
  const order = ['passed', 'failed', 'errors', 'skipped']
  const parts = order.filter((k) => counts[k] != null).map((k) => `${counts[k]} ${k}`)
  return parts.join(' · ') || null
}

/* One round: what broke, what changed, what the re-run showed. */
export function roundModel(round, next) {
  const failure = round?.failure || {}
  const attempts = round?.attempts || []
  const used = attempts[attempts.length - 1] || {}
  const rejected = attempts.flatMap((a) => (a.validation || []).map((v) => String(v)))
  const probe = round?.probe || {}
  let outcome
  let outcomeTone
  if (!(round?.edits || []).length) {
    outcome = 'no edit applied'
    outcomeTone = 'flag'
  } else if (failure.kind === 'probe' && probe.ok === false && probe.site && probe.site !== failure.site) {
    outcome = `import moved on → next break at ${probe.site}`
    outcomeTone = 'ok'
  } else if (failure.kind === 'probe' && probe.ok) {
    outcome = 'import now succeeds'
    outcomeTone = 'ok'
  } else if (round?.progress) {
    outcome = 'targeted tests pass'
    outcomeTone = 'ok'
  } else {
    outcome = 'no progress — same failure remains'
    outcomeTone = 'flag'
  }
  const targeted = round?.targeted
  const before = round?.before
  const full = round?.full
  return {
    n: round?.n,
    kind: failure.kind || 'probe',
    site: failure.site || null,
    test: failure.test || null,
    error: failure.error || '',
    items: round?.items || [],
    files: round?.edits || [],
    rationale: used.rationale || '',
    addresses: used.addresses || [],
    attempts: attempts.length,
    rejected,
    diffPath: (round?.edits || []).length ? `fixes/round-${round.n}.diff` : null,
    diffText: round?.diff_text ?? null,
    diffLines: used.diff_lines ?? null,
    progress: !!round?.progress,
    outcome,
    outcomeTone,
    probeOk: probe.ok ?? null,
    targeted: targeted
      ? {
          line: countsLine(targeted.counts),
          errorFiles: len(targeted.error_files),
          clean: !len(targeted.failed) && !len(targeted.error_files),
        }
      : null,
    before: before
      ? { line: countsLine(before.counts), newlyBroken: len(before.newly_broken) }
      : null,
    full: full ? { line: countsLine(full.counts), total: full.total ?? null } : null,
    durationMs: round?.duration_ms ?? null,
    nextSite: next?.failure?.site || null,
  }
}

export function fixModel(fix) {
  if (!fix) return null
  const rounds = (fix.rounds || []).map((r, i, all) => roundModel(r, all[i + 1]))
  const counts = fix.counts || {}
  const importFixedAt = (() => {
    const hit = (fix.rounds || []).find((r) => r?.failure?.kind === 'probe' && r?.probe?.ok)
    return hit ? hit.n : null
  })()
  // Round evidence wins: a round that targets an import-probe failure
  // proves the import was broken even if probe_start says otherwise.
  const importBroken = rounds.some((r) => r.kind === 'probe') || fix?.probe_start?.ok === false
  const guards = Array.isArray(fix.guards)
    ? { ok: fix.guards.every((g) => g.ok), violations: fix.guards.filter((g) => !g.ok).map((g) => g.check) }
    : { ok: fix.guards?.ok !== false, violations: fix.guards?.violations || [] }
  const up = fix.upstream || {}
  return {
    status: fix.status || fix.final_status,
    verdict: verdict(fix.status || fix.final_status),
    stopReason: fix.stop_reason || '',
    stopText: stopText(fix.stop_reason),
    rounds,
    progressRounds: rounds.filter((r) => r.progress).length,
    importBroken,
    importFixedAt,
    stillFailing: len(counts.still_failing),
    fixed: len(counts.fixed),
    newlyBroken: len(counts.newly_broken),
    baselineFailures: counts.baseline_failures ?? null,
    errorFiles: counts.error_files
      ? {
          fixed: len(counts.error_files.fixed),
          still: len(counts.error_files.still_failing),
          newly: len(counts.error_files.newly_broken),
        }
      : null,
    stillFailingList: Array.isArray(counts.still_failing) ? counts.still_failing : [],
    guards,
    patch: fix.patch || null,
    touched: fix.touched || [],
    totalMs: fix.total_cmd_ms ?? null,
    upstream: up.ran
      ? {
          url: up.pr_url || null,
          overlap: up.overlap || [],
          missed: up.missed || [],
          extra: up.extra || [],
          prTests: up.pr_tests
            ? { line: countsLine(up.pr_tests.counts), method: up.pr_tests.method || '' }
            : null,
        }
      : up.reason
        ? { skipped: up.reason }
        : null,
  }
}

/* The four honesty checks fixloop.guard_checks() enforces, matched to
 * its violation messages so each row can turn red on its own. */
export const GUARD_CHECKS = [
  { label: 'no skip / xfail markers added', match: /skip|xfail/i },
  { label: 'no test functions removed', match: /remov.*test|test.*remov/i },
  { label: 'no exceptions silenced', match: /except|silenc/i },
  { label: 'no dependency or packaging edits', match: /dependency|packaging/i },
]

export function guardRows(guards) {
  const violations = guards?.violations || []
  return GUARD_CHECKS.map((g) => {
    const hits = violations.filter((v) => g.match.test(v))
    return { label: g.label, ok: hits.length === 0, detail: hits }
  })
}

export function formatMs(ms) {
  if (ms == null) return ''
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}
