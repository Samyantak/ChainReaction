import React, { useState } from 'react'
import { postJSON, useApi, useStatus } from '../api'
import { navigate } from '../router'
import { formatDuration, repoShort } from '../formUtils'
import { Button, Sheet, Status } from '../components'
import '../pages.css'

const NIBABEL_PRESET = 'nibabel-numpy2'

function orderPresets(presets) {
  return [...presets].sort((a, b) =>
    a.id === NIBABEL_PRESET ? -1 : b.id === NIBABEL_PRESET ? 1 : 0,
  )
}

const STATUS_TONE = { done: 'ok', failed: 'flag', cancelled: 'warn', running: 'run', queued: 'mute' }

async function startReplay(body, setBusyId, setError, key) {
  setBusyId(key)
  setError(null)
  try {
    const run = await postJSON('/api/runs', body)
    navigate(`/runs/${run.id}/setup`)
  } catch (e) {
    setError(`Could not start the replay: ${e.message || e}`)
    setBusyId(null)
  }
}

async function deleteRun(runId, onDeleted, setError) {
  if (!window.confirm(`Delete run ${runId}? This removes its directory and cannot be undone.`)) return
  try {
    const res = await fetch(`/api/runs/${runId}`, { method: 'DELETE' })
    if (!res.ok) {
      let detail = ''
      try {
        detail = (await res.json()).detail || ''
      } catch {
        detail = await res.text()
      }
      throw new Error(detail || `HTTP ${res.status}`)
    }
    onDeleted(runId)
  } catch (e) {
    setError(`Could not delete the run: ${e.message || e}`)
  }
}

export default function HistoryView() {
  const { data: presets, error: presetsError } = useApi('/api/presets')
  const { data: runsData, error: runsError } = useApi('/api/runs')
  const status = useStatus()
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  const [deleted, setDeleted] = useState([])
  const liveAvailable = status?.live_available === true

  const runs = (runsData || []).filter((r) => !deleted.includes(r.id))
  const removeRun = (id) => setDeleted((prev) => [...prev, id])

  return (
    <div className="page history-page">
      <h2>Drawings on file</h2>
      {error && <div className="view-error">{error}</div>}

      <section>
        <h3>Pinned presets</h3>
        {!presets && !presetsError && <p>Loading presets…</p>}
        {presetsError && <div className="view-error">Could not load presets: {presetsError}</div>}
        {presets && (
          <div className="sheet-grid preset-grid">
            {orderPresets(presets).map((p, i) => (
              <div className="sheet preset-card" key={p.id}>
                <div className="mono sheet-no">DWG CR-{String(i + 1).padStart(3, '0')}</div>
                <h3>{p.title}</h3>
                <p className="mono preset-decision">{p.decision}</p>
                <div className="preset-actions">
                  <Button
                    kind="primary"
                    onClick={() =>
                      startReplay({ preset: p.id, mode: 'replay' }, setBusyId, setError, `replay:${p.id}`)
                    }
                    disabled={busyId != null || !p.has_recording}
                  >
                    {busyId === `replay:${p.id}` ? 'Starting…' : 'Replay'}
                  </Button>
                  {liveAvailable && p.has_recording && (
                    <Button
                      kind="secondary"
                      onClick={() =>
                        startReplay({ preset: p.id, mode: 'live' }, setBusyId, setError, `live:${p.id}`)
                      }
                      disabled={busyId != null}
                    >
                      {busyId === `live:${p.id}` ? 'Starting…' : 'Run live'}
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3>Past runs</h3>
        {!runsData && !runsError && <p>Loading runs…</p>}
        {runsError && <div className="view-error">Could not load runs: {runsError}</div>}
        {runsData && runs.length === 0 && (
          <p className="view-empty">
            No runs yet — <a href="#/new">start one from New analysis</a>.
          </p>
        )}
        {runs.length > 0 && (
          <div className="run-list">
            {runs.map((r) => {
              const duration = formatDuration(r.created_at, r.finished_at)
              const active = r.status === 'running' || r.status === 'queued'
              return (
                <Sheet key={r.id} className="run-row">
                  <div className="run-main">
                    <a className="run-decision" href={`#/runs/${r.id}/setup`}>
                      {r.decision || '(no decision)'}
                    </a>
                    <div className="mono run-meta">
                      {repoShort(r.repo)} · {r.mode}
                      {r.created_at ? ` · ${r.created_at.slice(0, 10)}` : ''}
                      {duration ? ` · ${duration}` : ''}
                    </div>
                  </div>
                  <Status tone={STATUS_TONE[r.status] ?? 'mute'} text={r.status} />
                  <div className="run-actions">
                    <Button kind="secondary" href={`#/runs/${r.id}/setup`}>
                      Open
                    </Button>
                    <Button
                      kind="secondary"
                      onClick={() =>
                        startReplay(
                          { replay_from: r.id, mode: 'replay' },
                          setBusyId,
                          setError,
                          `replay-run:${r.id}`,
                        )
                      }
                      disabled={busyId != null}
                    >
                      {busyId === `replay-run:${r.id}` ? 'Starting…' : 'Replay'}
                    </Button>
                    <button
                      className="btn btn-secondary btn-small"
                      type="button"
                      disabled={active}
                      title={active ? 'Cancel the run before deleting it.' : 'Delete this run.'}
                      onClick={() => deleteRun(r.id, removeRun, setError)}
                    >
                      Delete
                    </button>
                  </div>
                </Sheet>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
