import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useOptionalJson } from '../api'
import { Chip, DimensionRule, PathBreak, Sheet, Status } from '../components'
import '../investigate.css'

/* Investigate renders investigation.json when the run recorded it, the
 * live tool-call stream while the stage runs, and a skipped state
 * otherwise. Evidence chips on a finding jump to the exact tool call it
 * cites. The `demo` prop (fixture, labeled "example") exists only for
 * feature-page demo mode — real runs never pass it. */

const TOOL_GROUP = {
  list_changes: 'read',
  find_usages: 'search',
  trace_importers: 'trace',
  callers_of: 'trace',
  propagate: 'trace',
  tests_covering: 'search',
  read_file: 'read',
  run_import_probe: 'probe',
  run_tests: 'probe',
  run_snippet: 'probe',
  submit_findings: 'submit',
}

function ToolIcon({ name }) {
  const group = TOOL_GROUP[name] || 'read'
  const paths = {
    search: 'M7 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm3.5-1.5L14 14',
    trace: 'M2 3h4v3H2zM10 10h4v3h-4zM4 6v3h8v1',
    probe: 'M5 1h6M6 1v5l-4 7h12l-4-7V1',
    read: 'M3 1h7l3 3v11H3zM6 7h5M6 10h5',
    submit: 'M2 8l4 4 8-9',
  }
  return (
    <svg className={`tool-icon tool-${group}`} viewBox="0 0 16 16" aria-hidden="true">
      <path d={paths[group]} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function shortArgs(args) {
  const entries = Object.entries(args || {})
  const text = entries
    .map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.length}]` : typeof v === 'object' ? '{…}' : String(v)}`)
    .join(' ')
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

export function normalizeCalls(data) {
  const raw = data?.calls || data?.trace || data?.tool_calls || []
  return raw.map((c, i) => ({
    id: c.id || c.call_id || `call_${i + 1}`,
    name: c.name || c.tool || 'tool',
    args: c.args || c.arguments || {},
    summary: c.summary || c.result_summary || '',
    ok: c.ok !== false,
    truncated: !!c.truncated,
    durationMs: c.duration_ms ?? null,
    result: c.result ?? null,
  }))
}

/* Live stream: pair tool_call with its tool_result by id. */
function liveCalls(events) {
  const byId = new Map()
  for (const e of events || []) {
    const d = e.data || {}
    if (e.type === 'tool_call') {
      byId.set(d.id, { id: d.id, name: d.name, args: d.args || {}, pending: true })
    } else if (e.type === 'tool_result' && byId.has(d.id)) {
      Object.assign(byId.get(d.id), { summary: d.summary, ok: d.ok !== false, truncated: !!d.truncated, pending: false })
    }
  }
  return [...byId.values()]
}

function CallRow({ call, index, open, lit, onToggle, rowRef }) {
  return (
    <li ref={rowRef} className={`call${open ? ' call-open' : ''}${lit ? ' call-lit' : ''}${call.ok ? '' : ' call-fail'}`}>
      <button type="button" className="call-head" onClick={onToggle} aria-expanded={open}>
        <span className="call-n mono">{index + 1}</span>
        <ToolIcon name={call.name} />
        <span className="mono call-name">{call.name}</span>
        <span className="mono call-args">{shortArgs(call.args)}</span>
        <span className="spacer" />
        {call.durationMs != null && <span className="mono call-dur">{call.durationMs} ms</span>}
        {call.pending ? (
          <span className="status status-run">… running</span>
        ) : (
          <Status tone={call.ok ? 'ok' : 'flag'} text={call.ok ? 'ok' : 'failed'} />
        )}
      </button>
      {call.summary && <div className="call-summary">{call.summary}</div>}
      {open && (
        <div className="call-body">
          <div className="mono call-idline">
            {call.id}
            {call.truncated ? ' · result truncated' : ''}
          </div>
          <pre className="call-json">{JSON.stringify({ args: call.args, result: call.result }, null, 2)}</pre>
        </div>
      )}
    </li>
  )
}

function FindingCard({ finding, onEvidence, onHover, callIds }) {
  const verified = finding.verified !== false
  const reason = finding.reason || finding.verified_reason || null
  const conf = finding.confidence || (verified ? 'verified' : 'unverified')
  return (
    <Sheet
      className={`finding card${verified ? '' : ' finding-flagged'}`}
      onMouseEnter={() => onHover(finding.evidence || [])}
      onMouseLeave={() => onHover([])}
    >
      <div className="finding-head">
        <span className="mono finding-id">{finding.id}</span>
        <Chip tone={finding.kind === 'not_affected' ? 'mute' : 'flag'}>{String(finding.kind || '').replace(/_/g, ' ')}</Chip>
        <span className="spacer" />
        {verified ? (
          <Status tone={conf === 'confirmed' ? 'ok' : 'warn'} text={conf} />
        ) : (
          <Status tone="flag" text="flagged — not counted" />
        )}
      </div>
      <p className="finding-claim">{finding.claim}</p>
      {!verified && reason && <p className="finding-reason">✗ {reason}</p>}
      {(finding.sites || []).length > 0 && (
        <div className="finding-sites">
          {finding.sites.map((s) => (
            <Chip key={s} accent>
              <PathBreak text={s} />
            </Chip>
          ))}
        </div>
      )}
      {(finding.tests || []).length > 0 && (
        <div className="mono finding-tests">
          {finding.tests.length} covering test{finding.tests.length === 1 ? '' : 's'} ·{' '}
          {finding.tests[0].replace(/\\/g, '/')}
          {finding.tests.length > 1 ? ` +${finding.tests.length - 1}` : ''}
        </div>
      )}
      {(finding.evidence || []).length > 0 && (
        <div className="finding-evidence">
          <span className="mono muted">evidence</span>
          {finding.evidence.map((id) => (
            <button
              key={id}
              type="button"
              className={`evidence-chip mono${callIds.has(id) ? '' : ' evidence-missing'}`}
              onClick={() => onEvidence(id)}
              title={callIds.has(id) ? 'Jump to this tool call' : 'No such call in the trace'}
            >
              {id} {callIds.has(id) ? '↘' : '✗'}
            </button>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function Measures({ calls, findings, triage, status }) {
  const probes = calls.filter((c) => TOOL_GROUP[c.name] === 'probe').length
  const verified = findings.filter((f) => f.verified !== false).length
  const relevant = triage.filter((t) => t.relevant).length
  return (
    <div className="invest-measures">
      <div className="measure">
        <DimensionRule />
        <div className="measure-value">{calls.length}</div>
        <div className="measure-label">tool calls</div>
        <div className="measure-note mono">{probes} real probes / test runs</div>
      </div>
      <div className="measure">
        <DimensionRule />
        <div className={`measure-value ${verified === findings.length ? 'tone-ok' : 'tone-flag'}`}>
          {verified}/{findings.length}
        </div>
        <div className="measure-label">findings verified against the trace</div>
        <div className="measure-note mono">flagged findings never reach the scores</div>
      </div>
      <div className="measure">
        <DimensionRule />
        <div className="measure-value">
          {relevant}/{triage.length}
        </div>
        <div className="measure-label">change items judged relevant</div>
        <div className="measure-note mono">{status === 'incomplete' ? 'stage incomplete — static prediction used' : 'irrelevant items stop seeding breakage'}</div>
      </div>
    </div>
  )
}

function InvestigationBody({ data, demo }) {
  const calls = useMemo(() => normalizeCalls(data), [data])
  const findings = data.findings || []
  const triage = data.triage || []
  const callIds = useMemo(() => new Set(calls.map((c) => c.id)), [calls])
  const [open, setOpen] = useState(() => new Set())
  const [lit, setLit] = useState([])
  const rowRefs = useRef({})

  const jump = (id) => {
    if (!callIds.has(id)) return
    setOpen((s) => new Set(s).add(id))
    setLit([id])
    rowRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => setLit([]), 1600)
  }
  const toggle = (id) =>
    setOpen((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })

  return (
    <>
      {demo && (
        <p>
          <Chip accent>example</Chip>{' '}
          <span className="demo-note">Fixture data for illustration — not from a run.</span>
        </p>
      )}
      {data.summary && <p className="invest-summary">{data.summary}</p>}
      <Measures calls={calls} findings={findings} triage={triage} status={data.status} />

      <div className="invest-grid">
        <section>
          <h3>Findings ({findings.length})</h3>
          {findings.length === 0 && <p className="view-empty">No findings submitted.</p>}
          {findings.map((f) => (
            <FindingCard key={f.id} finding={f} callIds={callIds} onEvidence={jump} onHover={setLit} />
          ))}
          {triage.length > 0 && (
            <Sheet className="card triage-card">
              <h3>Triage</h3>
              <ul className="triage-list">
                {triage.map((t) => (
                  <li key={t.symbol} className={t.relevant ? '' : 'triage-out'}>
                    <Status tone={t.relevant ? 'ok' : 'mute'} text={t.relevant ? 'relevant' : 'not relevant'} />
                    <span className="mono triage-symbol">{t.symbol}</span>
                    {t.reason && <span className="muted triage-reason">{t.reason}</span>}
                  </li>
                ))}
              </ul>
            </Sheet>
          )}
        </section>
        <section>
          <h3>Tool trace ({calls.length})</h3>
          <p className="muted trace-note">Every finding must cite these calls. Hover a finding to light up its evidence.</p>
          <ol className="call-list">
            {calls.map((c, i) => (
              <CallRow
                key={c.id}
                call={c}
                index={i}
                open={open.has(c.id)}
                lit={lit.includes(c.id)}
                onToggle={() => toggle(c.id)}
                rowRef={(el) => {
                  rowRefs.current[c.id] = el
                }}
              />
            ))}
          </ol>
        </section>
      </div>
    </>
  )
}

function LiveCalls({ events }) {
  const calls = liveCalls(events)
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [calls.length])
  if (calls.length === 0) return <p className="view-empty">Waiting for the first tool call…</p>
  return (
    <ol className="call-list live">
      {calls.map((c, i) => (
        <CallRow key={c.id || i} call={c} index={i} open={false} lit={c.pending} onToggle={() => {}} />
      ))}
      <li ref={endRef} aria-hidden="true" />
    </ol>
  )
}

export default function InvestigateView({ runId, snapshot, events, demo }) {
  const recorded = useOptionalJson(demo ? null : `/api/runs/${runId}/artifacts/investigation.json`)
  const data = demo || recorded
  const active = snapshot?.status === 'running' || snapshot?.status === 'queued'
  const skippedReason = (events || [])
    .filter((e) => e.stage === 'investigate' && e.type === 'stage_end')
    .map((e) => e.data?.reason || e.data?.skipped)
    .find(Boolean)

  return (
    <div className="run-view investigate-view">
      <h2>Investigate</h2>
      {data ? (
        <InvestigationBody data={data} demo={!!demo} />
      ) : active ? (
        <>
          <p className="view-empty">The investigator is working — each tool call appears as Muse Spark makes it.</p>
          <LiveCalls events={events} />
        </>
      ) : (
        <p className="view-empty">
          Investigation skipped{typeof skippedReason === 'string' ? `: ${skippedReason}` : ' for this run'}. The
          prediction uses static propagation only.
        </p>
      )}
    </div>
  )
}
