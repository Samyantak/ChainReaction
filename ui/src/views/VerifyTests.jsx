import React, { useMemo, useState } from 'react'
import { DimensionRule, FileIcon, PathBreak, Sheet } from '../components'
import { groupByFile, splitSignature, testOverlap } from '../verifyUtils'

/* Predicted vs actual failing tests and new failures by signature —
 * the proof half of Verify, made browsable instead of a wall of ids. */

const pr = (v) => (v == null ? '—' : Number(v).toFixed(2))

const TABS = [
  { key: 'hits', label: 'Predicted and failed', tone: 'ok', empty: 'None.' },
  { key: 'missed', label: 'Failed, not predicted', tone: 'flag', empty: 'None — every new failure was predicted.' },
  { key: 'falseAlarms', label: 'Predicted, still passed', tone: 'sky', empty: 'None — no false alarms.' },
]

function FileGroup({ file, names, tone, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)
  const SHOW = 40
  return (
    <li className="tfile">
      <button type="button" className="tfile-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true" className="tfile-caret">{open ? '▾' : '▸'}</span>
        <span className="mono tfile-name">
          <PathBreak text={file} />
        </span>
        <span className={`mono tfile-count tone-${tone}`}>{names.length}</span>
      </button>
      {open && (
        <ul className="tnames mono">
          {names.slice(0, SHOW).map((n, i) => (
            <li key={`${n}-${i}`}>{n}</li>
          ))}
          {names.length > SHOW && <li className="muted">+{names.length - SHOW} more in this file</li>}
        </ul>
      )}
    </li>
  )
}

export function TestPrediction({ scores }) {
  const overlap = useMemo(() => testOverlap(scores), [scores])
  const [tab, setTab] = useState('hits')
  const [q, setQ] = useState('')
  const groups = useMemo(() => (overlap ? groupByFile(overlap[tab], q) : []), [overlap, tab, q])
  if (!overlap) return null
  const n = { hits: overlap.hits.length, missed: overlap.missed.length, falseAlarms: overlap.falseAlarms.length }
  const total = n.hits + n.missed + n.falseAlarms || 1
  const current = TABS.find((t) => t.key === tab)
  return (
    <section>
      <h3>Predicted vs actual failing tests</h3>
      <div className="tp-top">
        <div className="measure">
          <DimensionRule />
          <div className="measure-value mono">{pr(scores.test_precision)}</div>
          <div className="measure-label">precision</div>
          <div className="measure-note mono">of tests predicted to fail, the share that did</div>
        </div>
        <div className="measure">
          <DimensionRule />
          <div className="measure-value mono">{pr(scores.test_recall)}</div>
          <div className="measure-label">recall</div>
          <div className="measure-note mono">of tests that newly failed, the share predicted</div>
        </div>
        <div className="tp-overlap">
          <div className="tp-bar" role="img" aria-label={`${n.hits} predicted and failed, ${n.missed} missed, ${n.falseAlarms} false alarms`}>
            <span className="tp-hit" style={{ width: `${(n.hits / total) * 100}%` }} />
            <span className="tp-miss" style={{ width: `${(n.missed / total) * 100}%` }} />
            <span className="tp-fa" style={{ width: `${(n.falseAlarms / total) * 100}%` }} />
          </div>
          <div className="mono tp-legend">
            <span><i className="key tp-hit" /> {n.hits} predicted and failed</span>
            <span><i className="key tp-miss" /> {n.missed} missed</span>
            <span><i className="key tp-fa" /> {n.falseAlarms} false alarms</span>
          </div>
          <div className="mono measure-source">
            <FileIcon /> scores.json · baseline failures excluded
          </div>
        </div>
      </div>

      <Sheet className="card tp-browser">
        <div className="tp-toolbar">
          <div className="tp-tabs" role="tablist" aria-label="Test outcome">
            {TABS.map((t) => (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={tab === t.key}
                className={`tp-tab tone-${t.tone}`}
                onClick={() => setTab(t.key)}
              >
                {t.label} <span className="mono">{n[t.key]}</span>
              </button>
            ))}
          </div>
          <label className="tp-search">
            <span className="visually-hidden">Filter tests</span>
            <input type="search" placeholder="Filter by file or test name" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        {groups.length === 0 ? (
          <p className="view-empty">{q ? 'No tests match this filter.' : current.empty}</p>
        ) : (
          <ul className="tfiles" role="tabpanel">
            {groups.map(([file, names], i) => (
              <FileGroup key={`${tab}-${file}`} file={file} names={names} tone={current.tone} defaultOpen={groups.length <= 3 && i === 0} />
            ))}
          </ul>
        )}
      </Sheet>
    </section>
  )
}

export function FailureGroups({ scores }) {
  const groups = scores?.failure_groups
  const [open, setOpen] = useState(null)
  if (!groups || groups.length === 0) return null
  const max = Math.max(...groups.map((g) => g.count || 0), 1)
  const total = groups.reduce((a, g) => a + (g.count || 0), 0)
  return (
    <section>
      <h3>Why the new failures fail</h3>
      <p className="muted fg-lede">
        {total} new failures, grouped by error signature. One removed symbol usually explains hundreds of tests.
      </p>
      <Sheet className="card fg-card">
        {groups.map((g, i) => {
          const [type, rest] = splitSignature(g.message)
          return (
            <div key={i} className="fg-row">
              <button type="button" className="fg-head" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                <span className="mono fg-count">{g.count}×</span>
                <span className="fg-bar" aria-hidden="true">
                  <span style={{ width: `${((g.count || 0) / max) * 100}%` }} />
                </span>
                <span className="fg-msg">
                  {type && <span className="mono fg-type">{type}:</span>} <span className="mono">{rest}</span>
                </span>
              </button>
              {open === i && (g.examples || []).length > 0 && (
                <ul className="fg-examples mono">
                  {g.examples.map((e) => (
                    <li key={e}>{String(e).replace(/\\/g, '/')}</li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </Sheet>
    </section>
  )
}
