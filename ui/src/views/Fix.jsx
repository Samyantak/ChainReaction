import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useOptionalJson, useText } from '../api'
import { diffFiles, fixModel, formatMs, guardRows, parseDiff, splitDiff } from '../fixUtils'
import { Chip, DimensionRule, PathBreak, RedUnderline, Sheet, Status } from '../components'
import '../fix.css'

/* Fix renders fix.json exactly as the fix loop writes it, or a clear
 * skipped/running state. The `demo` prop (fixture object, labeled
 * "example") exists only for feature-page demo mode — real runs never
 * pass it, and a real fix.json always wins. */

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/* ---------- diff viewer ---------- */

function DiffBody({ text, mode }) {
  const rows = useMemo(() => parseDiff(text || ''), [text])
  if (mode === 'split') {
    const pairs = splitDiff(rows)
    return (
      <div className="diff-split" role="table" aria-label="Side-by-side diff">
        {pairs.map((p, i) =>
          p.hunk ? (
            <div key={i} className="split-hunk mono" role="row">
              {p.hunk}
            </div>
          ) : (
            <div key={i} className="split-row" role="row">
              <SplitCell row={p.left} side="old" />
              <SplitCell row={p.right} side="new" />
            </div>
          ),
        )}
      </div>
    )
  }
  return (
    <pre className="diff-box">
      {rows.map((r, i) => (
        <div key={i} className={`diff-row diff-${r.type}`}>
          <span className="ln" aria-hidden="true">
            {r.type === 'meta' || r.type === 'hunk' ? '' : r.type === 'add' ? '' : r.oldNo}
          </span>
          <span className="ln" aria-hidden="true">
            {r.type === 'meta' || r.type === 'hunk' ? '' : r.type === 'del' ? '' : r.newNo}
          </span>
          <span className="sign" aria-hidden="true">
            {r.type === 'add' ? '+' : r.type === 'del' ? '−' : ' '}
          </span>
          <span className="code">
            {r.type === 'del' ? <RedUnderline>{r.text || ' '}</RedUnderline> : r.text || ' '}
          </span>
        </div>
      ))}
    </pre>
  )
}

function SplitCell({ row, side }) {
  if (!row) return <div className={`split-cell split-empty split-${side}`} role="cell" />
  const kind = row.type === 'ctx' ? 'ctx' : side === 'old' ? 'del' : 'add'
  const no = side === 'old' ? row.oldNo : row.newNo
  return (
    <div className={`split-cell split-${side} diff-${kind}`} role="cell">
      <span className="ln" aria-hidden="true">{no}</span>
      <span className="code">{kind === 'del' ? <RedUnderline>{row.text || ' '}</RedUnderline> : row.text || ' '}</span>
    </div>
  )
}

function DiffPanel({ runId, path, demoText, title }) {
  const { data: fetched } = useText(demoText != null || !path ? null : `/api/runs/${runId}/artifacts/${path}`)
  const text = demoText != null ? demoText : fetched
  const [mode, setMode] = useState('unified')
  const files = useMemo(() => diffFiles(text || ''), [text])
  if (!path && demoText == null) return null
  if (!text) return <p className="view-empty">Loading diff…</p>
  return (
    <div className="diff-panel">
      <div className="diff-toolbar">
        <span className="mono diff-title">
          {title}
          {files.length > 0 && <span className="diff-files"> · {files.join(', ')}</span>}
        </span>
        <span className="spacer" />
        <div className="seg" role="group" aria-label="Diff layout">
          <button type="button" aria-pressed={mode === 'unified'} onClick={() => setMode('unified')}>
            Unified
          </button>
          <button type="button" aria-pressed={mode === 'split'} onClick={() => setMode('split')}>
            Side by side
          </button>
        </div>
      </div>
      <DiffBody text={text} mode={mode} />
    </div>
  )
}

/* ---------- re-standing dominoes ---------- */

function RoundTiles({ rounds, selected, onSelect }) {
  const [stood, setStood] = useState(() => (reducedMotion() ? rounds.length : 0))
  useEffect(() => {
    if (reducedMotion()) return undefined
    setStood(0)
    let i = 0
    const t = setInterval(() => {
      i += 1
      setStood(i)
      if (i >= rounds.length) clearInterval(t)
    }, 220)
    return () => clearInterval(t)
  }, [rounds.length])
  return (
    <div className="round-tiles" role="tablist" aria-label="Fix rounds">
      {rounds.map((r, i) => {
        const up = i < stood && r.progress
        return (
          <button
            key={r.n}
            type="button"
            role="tab"
            aria-selected={selected === i}
            className={`round-tile${r.progress ? ' tile-progress' : ' tile-stuck'}${up ? ' tile-up' : ''}${
              selected === i ? ' tile-selected' : ''
            }`}
            onClick={() => onSelect(i)}
          >
            <span className="tile-top mono">ROUND {r.n}</span>
            <span className="tile-site mono">
              <PathBreak text={r.site || '—'} />
            </span>
            <span className="tile-rule" aria-hidden="true" />
            <span className={`tile-out status-${r.outcomeTone}`}>
              {r.progress ? '✓ progress' : '✗ no progress'}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/* ---------- one round ---------- */

function RoundDetail({ runId, round, demo, onPrev, onNext, hasPrev, hasNext }) {
  return (
    <Sheet className="round-detail card">
      <div className="round-detail-head">
        <span className="mono sheet-no">ROUND {round.n}</span>
        <Chip tone={round.kind === 'probe' ? 'flag' : 'mute'}>
          {round.kind === 'probe' ? 'import probe' : 'test failure'}
        </Chip>
        {round.durationMs ? <span className="mono muted">{formatMs(round.durationMs)}</span> : null}
        <span className="spacer" />
        <button type="button" className="btn btn-secondary btn-small" onClick={onPrev} disabled={!hasPrev}>
          ← Prev
        </button>
        <button type="button" className="btn btn-secondary btn-small" onClick={onNext} disabled={!hasNext}>
          Next →
        </button>
      </div>

      <div className="round-grid">
        <section className="round-col">
          <h4>What broke</h4>
          {round.site && (
            <p>
              <Chip accent>{round.site.replace(/\\/g, '/')}</Chip>
            </p>
          )}
          {round.test && <p className="mono round-test">{round.test.replace(/\\/g, '/')}</p>}
          {round.error && <pre className="round-error">{round.error}</pre>}
          {round.items.length > 0 && (
            <p className="round-items">
              <span className="muted">change items · </span>
              {round.items.map((it) => (
                <Chip key={it} sky>
                  {it}
                </Chip>
              ))}
            </p>
          )}
        </section>

        <section className="round-col">
          <h4>What Muse Spark changed</h4>
          {round.rationale ? <blockquote className="round-rationale">{round.rationale}</blockquote> : null}
          {round.files.length > 0 && (
            <p className="round-files">
              {round.files.map((f) => (
                <Chip key={f}>{f.replace(/\\/g, '/')}</Chip>
              ))}
            </p>
          )}
          {round.rejected.length > 0 && (
            <div className="round-rejected">
              <Status tone="flag" text={`${round.rejected.length} edit(s) rejected before applying`} />
              <ul>
                {round.rejected.map((v, i) => (
                  <li key={i} className="mono">
                    {v}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {round.attempts > 1 && <p className="muted mono">{round.attempts} attempts</p>}
        </section>

        <section className="round-col">
          <h4>What the re-run showed</h4>
          <p>
            <Status tone={round.outcomeTone} text={round.outcome} />
          </p>
          <dl className="round-results mono">
            {round.targeted && (
              <>
                <dt>targeted · after</dt>
                <dd>{round.targeted.line || '—'}</dd>
              </>
            )}
            {round.before && (
              <>
                <dt>targeted · before</dt>
                <dd>
                  {round.before.line || '—'}
                  {round.before.newlyBroken ? ` · ${round.before.newlyBroken} newly broken` : ''}
                </dd>
              </>
            )}
            {round.full && (
              <>
                <dt>full suite · after</dt>
                <dd>{round.full.line}</dd>
              </>
            )}
          </dl>
        </section>
      </div>

      <DiffPanel
        runId={runId}
        path={round.diffPath}
        demoText={demo ? round.diffText : undefined}
        title={`round-${round.n}.diff`}
      />
    </Sheet>
  )
}

/* ---------- summary blocks ---------- */

function Measure({ value, label, note, tone }) {
  return (
    <div className="measure">
      <DimensionRule />
      <div className={`measure-value${tone ? ` tone-${tone}` : ''}`}>{value}</div>
      <div className="measure-label">{label}</div>
      {note ? <div className="measure-note mono">{note}</div> : null}
    </div>
  )
}

function Guards({ guards }) {
  const rows = guardRows(guards)
  return (
    <Sheet className="card guard-card">
      <h3>Honesty checks on the final patch</h3>
      <ul className="guard-list">
        {rows.map((g) => (
          <li key={g.label}>
            <Status tone={g.ok ? 'ok' : 'flag'} text={g.label} />
            {g.detail.map((d, i) => (
              <div key={i} className="mono guard-detail">
                {d}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {(guards.violations || [])
        .filter((v) => !rows.some((r) => r.detail.includes(v)))
        .map((v, i) => (
          <p key={i}>
            <Status tone="flag" text={v} />
          </p>
        ))}
    </Sheet>
  )
}

function Upstream({ up }) {
  if (!up) return null
  if (up.skipped) {
    return (
      <Sheet className="card upstream-card">
        <h3>Compared with the upstream fix</h3>
        <p className="muted">Not compared: {up.skipped}.</p>
      </Sheet>
    )
  }
  const col = (title, list, tone) => (
    <div className="up-col">
      <div className={`up-head mono tone-${tone}`}>
        {title} · {list.length}
      </div>
      {list.length ? (
        list.map((f) => (
          <div key={f} className="mono up-file">
            <PathBreak text={f} />
          </div>
        ))
      ) : (
        <div className="muted mono up-file">none</div>
      )}
    </div>
  )
  return (
    <Sheet className="card upstream-card">
      <h3>
        Compared with the upstream fix{' '}
        {up.url && (
          <a href={up.url} target="_blank" rel="noreferrer" className="mono up-link">
            {up.url.replace('https://github.com/', '')}
          </a>
        )}
      </h3>
      <p className="muted">Shown as overlap, never claimed as a match.</p>
      <div className="up-cols">
        {col('both touched', up.overlap, 'ok')}
        {col('only upstream', up.missed, 'flag')}
        {col('only ChainReaction', up.extra, 'sky')}
      </div>
      {up.prTests && (
        <p className="mono up-tests">
          upstream's tests with this patch: {up.prTests.line}
          {up.prTests.method ? <span className="muted"> · {up.prTests.method}</span> : null}
        </p>
      )}
    </Sheet>
  )
}

function PatchActions({ runId, model, demo, demoPatch }) {
  const [copied, setCopied] = useState(false)
  const [show, setShow] = useState(false)
  const href = model.patch ? `/api/runs/${runId}/artifacts/${model.patch}` : null
  if (!href && !demoPatch) return null
  const copyApply = () => {
    const command = demo
      ? 'git apply example.patch'
      : `curl -fsSL -o fix.patch ${JSON.stringify(`${window.location.origin}${href}`)} && git apply fix.patch`
    navigator.clipboard?.writeText(command).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1800)
      },
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
    <Sheet className="card patch-card">
      <div className="patch-head">
        <div>
          <h3>Final patch</h3>
          <p className="muted mono">
            {model.touched.length} file{model.touched.length === 1 ? '' : 's'} ·{' '}
            {model.touched.map((f) => f.replace(/\\/g, '/')).join(', ')}
          </p>
        </div>
        <div className="patch-actions">
        {demo ? (
          <button className="btn btn-primary" type="button" onClick={downloadDemo}>
            Download patch
          </button>
        ) : (
          <a className="btn btn-primary" href={href} download>
            Download patch
          </a>
        )}
        <button className="btn btn-secondary" type="button" onClick={copyApply}>
          {copied ? '✓ Copied' : 'Copy as git apply command'}
        </button>
        <button className="btn btn-secondary" type="button" aria-expanded={show} onClick={() => setShow((s) => !s)}>
          {show ? 'Hide patch' : 'View patch'}
        </button>
        </div>
      </div>
      {show && <DiffPanel runId={runId} path={model.patch} demoText={demo ? demoPatch : undefined} title="final.patch" />}
    </Sheet>
  )
}

function StillFailing({ model }) {
  const [open, setOpen] = useState(false)
  if (!model.stillFailingList.length) return null
  return (
    <Sheet className="card still-card">
      <button type="button" className="still-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Status tone="flag" text={`${model.stillFailing} decision-caused test failures remain`} />
        <span className="mono muted">{open ? 'hide' : 'show'}</span>
      </button>
      {open && (
        <ul className="still-list mono">
          {model.stillFailingList.map((t) => (
            <li key={t}>{t.replace(/\\/g, '/')}</li>
          ))}
        </ul>
      )}
    </Sheet>
  )
}

/* ---------- body ---------- */

function FixBody({ runId, fix, demo }) {
  const model = useMemo(() => fixModel(fix), [fix])
  const [sel, setSel] = useState(0)
  const detailRef = useRef(null)
  const demoPatch = demo ? (fix.rounds || []).map((r) => r.diff_text).filter(Boolean).join('') : null
  const select = (i) => {
    setSel(i)
    detailRef.current?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'nearest' })
  }
  const v = model.verdict
  const round = model.rounds[sel]

  return (
    <>
      {demo && (
        <p>
          <Chip accent>example</Chip>{' '}
          <span className="demo-note">Fixture data for illustration — not from a run.</span>
        </p>
      )}

      <div className="fix-verdict">
        <span className={`verdict-stamp verdict-${v.tone}`}>
          {v.tone === 'ok' ? '✓ ' : v.tone === 'flag' ? '✗ ' : ''}
          {v.label}
        </span>
        <div>
          <div className="verdict-note">{v.note}</div>
          <div className="mono muted">
            {model.stopText}
            {model.totalMs ? ` · ${formatMs(model.totalMs)} of test runs` : ''}
          </div>
        </div>
      </div>

      <div className="fix-measures">
        <Measure
          value={model.rounds.length}
          label="rounds"
          note={`${model.progressRounds} made progress`}
        />
        <Measure
          value={model.importBroken ? (model.importFixedAt ? `R${model.importFixedAt}` : '✗') : '—'}
          label={model.importBroken ? (model.importFixedAt ? 'import fixed in round' : 'import still broken') : 'import never broke'}
          tone={model.importBroken ? (model.importFixedAt ? 'ok' : 'flag') : null}
        />
        <Measure
          value={model.stillFailing}
          label="decision-caused failures left"
          note={
            model.errorFiles
              ? `${model.errorFiles.still} test files still fail to collect`
              : null
          }
          tone={model.stillFailing ? 'flag' : 'ok'}
        />
        <Measure
          value={model.newlyBroken}
          label="newly broken by the patch"
          note={model.baselineFailures != null ? `${model.baselineFailures} baseline failures excluded` : null}
          tone={model.newlyBroken ? 'flag' : 'ok'}
        />
      </div>

      {model.rounds.length > 0 && (
        <>
          <h3>Standing the dominoes back up</h3>
          <RoundTiles rounds={model.rounds} selected={sel} onSelect={select} />
          <div ref={detailRef}>
            {round && (
              <RoundDetail
                runId={runId}
                round={round}
                demo={demo}
                hasPrev={sel > 0}
                hasNext={sel < model.rounds.length - 1}
                onPrev={() => select(Math.max(0, sel - 1))}
                onNext={() => select(Math.min(model.rounds.length - 1, sel + 1))}
              />
            )}
          </div>
        </>
      )}

      <PatchActions runId={runId} model={model} demo={demo} demoPatch={demoPatch} />
      <StillFailing model={model} />
      <div className="fix-pair">
        <Guards guards={model.guards} />
        <Upstream up={model.upstream} />
      </div>
    </>
  )
}

export default function FixView({ runId, snapshot, demo }) {
  const recorded = useOptionalJson(demo ? null : `/api/runs/${runId}/artifacts/fix.json`)
  const fix = demo || recorded
  const active = snapshot?.status === 'running' || snapshot?.status === 'queued'
  const skipped = fix && (fix.status === 'skipped' || !(fix.rounds || []).length)

  return (
    <div className="run-view fix-view">
      <h2>Fix</h2>
      {fix && !skipped ? (
        <FixBody runId={runId} fix={fix} demo={!!demo} />
      ) : fix && skipped ? (
        <p className="view-empty">Fix skipped: {fix.stop_reason ? fix.stop_reason.replace(/-/g, ' ') : 'nothing to fix'}.</p>
      ) : active ? (
        <p className="view-empty">The fix loop is running — rounds appear here as they land.</p>
      ) : (
        <p className="view-empty">No fix loop recorded for this run.</p>
      )}
    </div>
  )
}
