import React, { useEffect, useState } from 'react'
import { RUN_VIEWS } from '../router'
import { Chip, Sheet, Status, TitleBlock } from '../components'
import { STAGES, STAGE_VIEW, VIEW_STAGE, stageStates } from '../stages'
import { useRun } from '../useRun'
import { LogRows } from '../log'
import GraphView from './Graph'
import SetupView from './Setup'
import VerifyView from './Verify'
import ReportView from './Report'
import '../run.css'

const VIEW_TITLES = {
  setup: ['Setup', 'Clone, detect, build envs, baselines.'],
  interpret: ['Interpret', 'The change set. Full view lands in step 8.'],
  investigate: ['Investigate', 'Tool calls and findings. Full view lands in step 8.'],
  graph: ['Prediction graph', 'Domino tiles + impacted subgraph.'],
  verify: ['Verify', 'Before vs after, predicted vs actual.'],
  fix: ['Fix', 'Rounds to green. Full view lands in step 8.'],
  report: ['Report', 'Narrative, scoreboard, exports.'],
}

function repoShort(manifest) {
  const url = (manifest.repo_url || '').replace(/\/$/, '')
  const name = url ? url.split('/').pop() : ''
  return name || manifest.preset || manifest.run_id
}

function RunHeader({ snapshot, view }) {
  const m = snapshot.manifest
  const commit = (m.resolved_commit || m.ref || 'unknown').slice(0, 12)
  const spine = snapshot.report?.spine
  const sheet = RUN_VIEWS.indexOf(view) + 1
  const mode = m.mode === 'replay' ? `replay · ${m.replay_from}` : 'live'
  return (
    <TitleBlock
      cells={[
        ['DECISION', m.decision || '(none)'],
        ['TARGET', `${repoShort(m)} @ ${commit}`],
        ['MODE', mode, m.mode === 'replay'],
        ['CHAIN', spine ? `${spine.length} hops` : '…'],
        ['SHEET', `${sheet} / 7`],
      ]}
    />
  )
}

function StatusRow({ snapshot, stream, onCancel }) {
  const m = snapshot.manifest
  const [cancelling, setCancelling] = useState(false)
  const active = snapshot.status === 'running' || snapshot.status === 'queued'
  const tone = { done: 'ok', failed: 'flag', cancelled: 'warn', running: 'run', queued: 'mute' }[
    snapshot.status
  ]
  return (
    <div className="run-status-row">
      {m.mode === 'replay' && <Chip sky>REPLAY</Chip>}
      <Status tone={tone ?? 'mute'} text={snapshot.status} />
      {active && stream === 'connecting' && <span className="stream-pill">reconnecting…</span>}
      {active && stream === 'live' && <span className="stream-pill live">live</span>}
      <span className="spacer" />
      {active && (
        <button
          className="btn btn-secondary btn-small"
          type="button"
          disabled={cancelling}
          onClick={() => {
            setCancelling(true)
            onCancel().finally(() => setCancelling(false))
          }}
        >
          {cancelling ? 'Cancelling…' : 'Cancel run'}
        </button>
      )}
    </div>
  )
}

function elapsed(ts, now) {
  const s = Math.max(0, Math.floor((now - new Date(ts).getTime()) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function RailItem({ runId, stage, index, state, now }) {
  const view = STAGE_VIEW[stage]
  const active = window.location.hash === `#/runs/${runId}/${view}`
  const icon = { pending: '○', running: '…', done: '✓', failed: '✗', skipped: '–', cancelled: '!' }[
    state.state
  ]
  return (
    <a className={`rail-item${active ? ' active' : ''}`} href={`#/runs/${runId}/${view}`}>
      <span className="mono rail-sheet">SHEET {index + 1} / 7</span>
      <span className="rail-name">{stage}</span>
      <span className="mono rail-state">
        {icon} {state.state}
        {state.state === 'running' && state.startedAt ? ` · ${elapsed(state.startedAt, now)}` : ''}
        {state.state === 'skipped' && state.reason ? ` · ${state.reason}` : ''}
      </span>
    </a>
  )
}

function StageRail({ runId, states, now }) {
  return (
    <nav className="rail" aria-label="Run stages">
      {STAGES.map((stage, i) => (
        <RailItem key={stage} runId={runId} stage={stage} index={i} state={states[stage]} now={now} />
      ))}
    </nav>
  )
}

function FailedPanel({ snapshot, events }) {
  const [copied, setCopied] = useState(false)
  const tail = events
    .filter((e) => e.type === 'log' || e.type === 'error')
    .slice(-15)
    .map((e) => e.data.message || e.data.warning || e.data.error)
    .join('\n')
  const details = `run ${snapshot.manifest.run_id} failed: ${snapshot.manifest.error}\n\n${tail}`
  return (
    <div className="view-error">
      <div>{snapshot.manifest.error || 'Run failed.'}</div>
      {tail && <pre className="log-tail">{tail}</pre>}
      <button
        className="btn btn-secondary btn-small"
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(details).then(
            () => setCopied(true),
            () => setCopied(false),
          )
        }}
      >
        {copied ? 'Copied' : 'Copy details'}
      </button>
    </div>
  )
}

function ViewPanel({ runId, view, stage, state, events }) {
  const [title, blurb] = VIEW_TITLES[view] ?? [view, '']
  const stageEvents = events.filter((e) => e.stage === stage)
  const artifacts = stageEvents.filter((e) => e.type === 'artifact')
  return (
    <div className="run-view">
      <h2>{title}</h2>
      <p className="view-blurb">{blurb}</p>
      <p className="view-stage-state mono">
        stage {stage}: {state.state}
        {state.reason ? ` (${state.reason})` : ''}
        {state.error ? ` — ${state.error}` : ''}
      </p>
      {stageEvents.length === 0 && state.state === 'pending' && (
        <p className="view-empty">No events yet — the {stage} stage hasn&apos;t started.</p>
      )}
      <LogRows events={stageEvents} runId={runId} />
      {artifacts.length > 0 && (
        <div className="artifact-list">
          {artifacts.map((e) => (
            <a key={e.seq} href={`/api/runs/${runId}/artifacts/${e.data.name}`}>
              {e.data.name}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Run({ id, view }) {
  const { snapshot, snapError, events, stream, cancel } = useRun(id)
  const [now, setNow] = useState(() => Date.now())
  const stage = VIEW_STAGE[view] ?? 'setup'

  useEffect(() => {
    if (!snapshot || ['done', 'failed', 'cancelled'].includes(snapshot.status)) return undefined
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [snapshot])

  if (snapError) {
    return (
      <div className="page">
        <Sheet>
          <h2>Run not found</h2>
          <p className="mono">{snapError}</p>
          <p>
            <a href="#/history">Back to history</a>
          </p>
        </Sheet>
      </div>
    )
  }
  if (!snapshot) {
    return (
      <div className="page">
        <Sheet>
          <p>Loading run…</p>
        </Sheet>
      </div>
    )
  }
  const states = stageStates(STAGES, events, snapshot, stream === 'closed')
  return (
    <div className="run-page">
      <RunHeader snapshot={snapshot} view={view} />
      <StatusRow snapshot={snapshot} stream={stream} onCancel={cancel} />
      {snapshot.status === 'failed' && <FailedPanel snapshot={snapshot} events={events} />}
      {snapshot.status === 'cancelled' && (
        <div className="view-error">Run cancelled{snapshot.manifest.error ? `: ${snapshot.manifest.error}` : ''}.</div>
      )}
      <div className="run-body">
        <StageRail runId={id} states={states} now={now} />
        {view === 'graph' ? (
          <GraphView runId={id} />
        ) : view === 'verify' ? (
          <VerifyView runId={id} />
        ) : view === 'report' ? (
          <ReportView runId={id} />
        ) : view === 'setup' ? (
          <SetupView runId={id} snapshot={snapshot} events={events} />
        ) : (
          <ViewPanel runId={id} view={view} stage={stage} state={states[stage]} events={events} />
        )}
      </div>
    </div>
  )
}
