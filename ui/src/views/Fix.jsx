import React, { useEffect, useMemo, useState } from 'react'
import { useOptionalJson, useText } from '../api'
import { counterSeries, parseDiff } from '../fixUtils'
import { Chip, RedUnderline, Sheet, Status } from '../components'
import '../fix.css'

/* Fix renders fix.json when the run recorded it and a skipped state
 * otherwise. The `demo` prop (fixture object) exists only for
 * feature-page demo mode, labeled "example" — real runs never pass it. */

function DiffView({ runId, path, demoText }) {
  const { data: fetched } = useText(demoText != null ? null : `/api/runs/${runId}/artifacts/${path}`)
  const text = demoText != null ? demoText : fetched
  const rows = useMemo(() => parseDiff(text || ''), [text])
  if (!text) return <p className="view-empty">No diff recorded for this round.</p>
  return (
    <pre className="diff-box">
      {rows.map((r, i) => (
        <div key={i} className={`diff-row diff-${r.type}`}>
          {r.type === 'del' ? (
            <RedUnderline>{r.text || ' '}</RedUnderline>
          ) : (
            r.text || ' '
          )}
        </div>
      ))}
    </pre>
  )
}

function useCounter(series) {
  const reduced =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const [step, setStep] = useState(() => (reduced ? (series?.length ?? 1) - 1 : 0))
  useEffect(() => {
    if (!series || series.length < 2 || reduced) {
      setStep((series?.length ?? 1) - 1)
      return undefined
    }
    setStep(0)
    const timer = setInterval(() => {
      setStep((s) => {
        if (s + 1 >= series.length) {
          clearInterval(timer)
          return s
        }
        return s + 1
      })
    }, 650)
    return () => clearInterval(timer)
  }, [series?.join(','), reduced])
  return step
}

function TestCounter({ fix }) {
  const series = counterSeries(fix)
  const step = useCounter(series)
  if (!series) return null
  const initial = series[0]
  const failed = series[Math.min(step, series.length - 1)]
  const fixed = initial - failed
  const greenPct = initial > 0 ? Math.round((fixed / initial) * 100) : 100
  return (
    <div className="counter-block">
      <div className="mono counter-numbers">
        {failed} failing · {fixed} fixed
      </div>
      <div className="counter-bar" aria-hidden="true">
        <div className="counter-green" style={{ width: `${greenPct}%` }} />
      </div>
      <div className="mono counter-round">
        {step >= series.length - 1 ? 'final' : `round ${step} of ${series.length - 1}`}
      </div>
    </div>
  )
}

function RoundCard({ runId, round, demo }) {
  const probeOk = round.probe?.ok
  return (
    <Sheet className="round-card card">
      <div className="round-head">
        <strong>Round {round.n}</strong>
        <span className="spacer" />
        {probeOk != null && (
          <Status tone={probeOk ? 'ok' : 'flag'} text={probeOk ? 'probe passed' : 'probe failed'} />
        )}
      </div>
      <p className="round-target mono">{round.target}</p>
      {round.rationale && <p>{round.rationale}</p>}
      {(round.addresses || []).length > 0 && (
        <p className="mono round-addresses">{round.addresses.join(', ')}</p>
      )}
      {round.validation && round.validation.ok === false && (
        <p>
          <Status tone="flag" text={`edits rejected: ${round.validation.error || 'validation failed'}`} />
        </p>
      )}
      {(round.edits || []).length > 0 && (
        <div className="mono round-edits">
          {(round.edits || []).map((e, i) => (
            <div key={i}>{e.path}</div>
          ))}
        </div>
      )}
      <DiffView runId={runId} path={round.diff} demoText={demo ? round.diff_text : undefined} />
      {round.tests_after && (
        <p className="mono round-tests">
          targeted tests: {round.tests_after.passed ?? '?'} passed
          {round.tests_after.failed != null ? `, ${round.tests_after.failed} failed` : ''}
        </p>
      )}
      {round.duration_s != null && <p className="mono round-tests">{round.duration_s}s</p>}
    </Sheet>
  )
}

function FixBody({ runId, fix, demo }) {
  const [copied, setCopied] = useState(false)
  const counts = fix.counts || {}
  const guards = fix.guards || []
  const patchHref = fix.patch ? `/api/runs/${runId}/artifacts/${fix.patch}` : null
  const statusTone = fix.status === 'green' ? 'ok' : fix.status === 'partial' ? 'warn' : 'flag'
  // Demo mode has no patch bytes on the server; the fixture embeds the
  // round diffs so Download still produces the example patch offline.
  const demoPatch = demo
    ? (fix.rounds || []).map((r) => r.diff_text).filter(Boolean).join('\n')
    : null

  const copyApply = () => {
    const command = demo
      ? 'git apply example.patch'
      : `curl -fsSL -o fix.patch ${JSON.stringify(`${window.location.origin}${patchHref}`)} && git apply fix.patch`
    navigator.clipboard?.writeText(command).then(
      () => setCopied(true),
      () => setCopied(false),
    )
  }

  const downloadDemo = () => {
    const blob = new Blob([demoPatch], { type: 'text/x-patch' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'example.patch'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <>
      {demo && (
        <p>
          <Chip accent>example</Chip>{' '}
          <span className="demo-note">Fixture data for illustration — not from a run.</span>
        </p>
      )}
      <p>
        <Status tone={statusTone} text={fix.status || 'unknown'} />
        {fix.stop_reason && <span className="stop-reason"> · {fix.stop_reason}</span>}
      </p>

      <TestCounter fix={fix} />

      {(fix.rounds || []).map((r) => (
        <RoundCard key={r.n} runId={runId} round={r} demo={demo} />
      ))}

      <h3>Counts</h3>
      <Sheet className="card">
        <div className="mono">
          {[counts.fixed != null && `${counts.fixed} fixed`,
            counts.still_failing != null && `${counts.still_failing} still failing`,
            counts.newly_broken != null && `${counts.newly_broken} newly broken`,
            counts.baseline_excluded != null && `${counts.baseline_excluded} baseline excluded`,
          ]
            .filter(Boolean)
            .join(' · ') || 'No counts recorded.'}
        </div>
      </Sheet>

      {guards.length > 0 && (
        <>
          <h3>Guard checks</h3>
          <Sheet className="card">
            {guards.map((g, i) => (
              <div key={i} className="guard-row">
                <Status tone={g.ok ? 'ok' : 'flag'} text={g.ok ? 'pass' : 'VIOLATION'} />{' '}
                <span>{g.check}</span>
              </div>
            ))}
          </Sheet>
        </>
      )}

      {fix.before_env && (
        <>
          <h3>Before-env check</h3>
          <p>
            <Status tone={fix.before_env.ok ? 'ok' : 'flag'} text={fix.before_env.ok ? 'passed' : 'failed'} />{' '}
            {fix.before_env.detail && <span>{fix.before_env.detail}</span>}
          </p>
        </>
      )}

      {(demoPatch || (!demo && patchHref)) && (
        <div className="fix-actions">
          {demo ? (
            <button className="btn btn-secondary" type="button" onClick={downloadDemo}>
              Download patch
            </button>
          ) : (
            <a className="btn btn-secondary" href={patchHref} download>
              Download patch
            </a>
          )}
          <button className="btn btn-secondary" type="button" onClick={copyApply}>
            {copied ? 'Copied' : 'Copy as git apply command'}
          </button>
        </div>
      )}
    </>
  )
}

export default function FixView({ runId, snapshot, demo }) {
  const recorded = useOptionalJson(demo ? null : `/api/runs/${runId}/artifacts/fix.json`)
  const fix = demo || recorded
  const active = snapshot?.status === 'running' || snapshot?.status === 'queued'

  return (
    <div className="run-view fix-view">
      <h2>Fix</h2>
      {fix ? (
        <FixBody runId={runId} fix={fix} demo={!!demo} />
      ) : active ? (
        <p className="view-empty">The fix loop is running — rounds appear here.</p>
      ) : (
        <p className="view-empty">Fix skipped: not available yet (M5).</p>
      )}
    </div>
  )
}
