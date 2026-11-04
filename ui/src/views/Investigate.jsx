import React, { useState } from 'react'
import { useOptionalJson } from '../api'
import { Chip, Sheet, Status } from '../components'
import '../investigate.css'

/* Investigate renders investigation.json when the run recorded it, a
 * live tool-call log while the stage runs, and a skipped state
 * otherwise. The `demo` prop (fixture object) exists only for
 * feature-page demo mode, labeled "example" — real runs never pass it. */

function shortArgs(args) {
  const text = JSON.stringify(args || {})
  return text.length > 90 ? `${text.slice(0, 90)}…` : text
}

function CallRow({ call, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`call-row${selected ? ' selected' : ''}`}
      onClick={() => onSelect(selected ? null : call.id)}
      aria-expanded={selected}
    >
      <span className="mono call-id">{call.id}</span>
      <span className="mono call-name">{call.name || call.tool}</span>
      <span className="mono call-args">{shortArgs(call.args)}</span>
      <span className="spacer" />
      {call.duration_ms != null && <span className="mono call-dur">{call.duration_ms}ms</span>}
      <Status tone={call.ok === false ? 'flag' : 'ok'} text={call.ok === false ? 'fail' : 'ok'} />
    </button>
  )
}

function FindingCard({ finding, callsById }) {
  const verified = finding.verified !== false
  const reason = finding.reason || finding.verified_reason || null
  return (
    <Sheet className="finding-card">
      <div className="finding-head">
        <span className="mono finding-id">{finding.id}</span>
        <Chip>{finding.kind}</Chip>
        {verified ? (
          <Status tone="ok" text={finding.confidence || 'verified'} />
        ) : (
          <Status tone="flag" text="flagged" />
        )}
      </div>
      <p className="finding-claim">{finding.claim}</p>
      {reason && !verified && <p className="finding-reason">✗ {reason}</p>}
      <div className="mono finding-meta">
        {finding.symbol && <span>{finding.symbol}</span>}
        {(finding.sites || []).map((s) => (
          <span key={s} className="finding-site">
            {' '}
            · {s}
          </span>
        ))}
      </div>
      {(finding.evidence || []).length > 0 && (
        <div className="finding-evidence">
          {(finding.evidence || []).map((id) => (
            <span key={id} className="mono evidence-chip" title={callsById[id]?.summary || ''}>
              {id}
            </span>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function InvestigationBody({ data, demo }) {
  const [selected, setSelected] = useState(null)
  const calls = data.calls || data.trace || []
  const callsById = Object.fromEntries(calls.map((c) => [c.id, c]))
  const findings = data.findings || []
  const triage = data.triage || []
  const selectedCall = selected ? callsById[selected] : null

  return (
    <>
      {demo && (
        <p>
          <Chip accent>example</Chip>{' '}
          <span className="demo-note">Fixture data for illustration — not from a run.</span>
        </p>
      )}
      {data.summary && <p className="invest-summary">{data.summary}</p>}

      <h3>Tool calls ({calls.length})</h3>
      {calls.length === 0 && <p className="view-empty">No tool calls recorded.</p>}
      <div className="call-list">
        {calls.map((c) => (
          <CallRow key={c.id} call={c} selected={selected === c.id} onSelect={setSelected} />
        ))}
      </div>
      {selectedCall && (
        <Sheet>
          <div className="mono">{selectedCall.summary || 'no summary'}</div>
          <pre className="log-tail">{JSON.stringify(selectedCall.result ?? selectedCall, null, 2)}</pre>
        </Sheet>
      )}

      <h3>Findings ({findings.length})</h3>
      {findings.length === 0 && <p className="view-empty">No findings submitted.</p>}
      {findings.map((f) => (
        <FindingCard key={f.id} finding={f} callsById={callsById} />
      ))}

      {triage.length > 0 && (
        <>
          <h3>Triage</h3>
          <Sheet>
            {triage.map((t) => (
              <div className="mono triage-row" key={t.symbol}>
                {t.relevant ? '✓' : '✗'} {t.symbol}
                {t.reason ? ` — ${t.reason}` : ''}
              </div>
            ))}
          </Sheet>
        </>
      )}
    </>
  )
}

function LiveCalls({ events }) {
  const calls = (events || []).filter((e) => e.type === 'tool_call' || e.type === 'tool_result')
  if (calls.length === 0) return <p className="view-empty">Waiting for tool calls…</p>
  return (
    <div className="call-list">
      {calls.map((e) => (
        <div className="mono triage-row" key={e.seq}>
          {e.type === 'tool_call' ? `→ ${e.data?.name || 'call'}` : `✓ ${e.data?.id || ''}: ${e.data?.summary || ''}`}
        </div>
      ))}
    </div>
  )
}

export default function InvestigateView({ runId, snapshot, events, demo }) {
  const recorded = useOptionalJson(demo ? null : `/api/runs/${runId}/artifacts/investigation.json`)
  const data = demo || recorded
  const active = snapshot?.status === 'running' || snapshot?.status === 'queued'

  return (
    <div className="run-view investigate-view">
      <h2>Investigate</h2>
      {data ? (
        <InvestigationBody data={data} demo={!!demo} />
      ) : active ? (
        <>
          <p className="view-empty">The investigator is working — tool calls appear live.</p>
          <LiveCalls events={events} />
        </>
      ) : (
        <p className="view-empty">Investigation skipped: not available yet (M4).</p>
      )}
    </div>
  )
}
