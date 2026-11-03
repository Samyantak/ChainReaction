import React, { useEffect, useRef, useState } from 'react'
import { postJSON, useApi, useStatus } from '../api'
import { navigate } from '../router'
import { decisionPreviewText, validateGithubUrl } from '../formUtils'
import { Button, RedNote, Sheet, Status } from '../components'
import '../pages.css'

const NIBABEL_PRESET = 'nibabel-numpy2'

/* Same ordering as the landing drawings (nibabel leads), so DWG numbers
 * match across pages. Numbers come from API order, never hard-coded. */
function orderPresets(presets) {
  return [...presets].sort((a, b) =>
    a.id === NIBABEL_PRESET ? -1 : b.id === NIBABEL_PRESET ? 1 : 0,
  )
}

async function startRun(body, setBusyId, setError, key) {
  setBusyId(key)
  setError(null)
  try {
    const run = await postJSON('/api/runs', body)
    navigate(`/runs/${run.id}/setup`)
  } catch (e) {
    setError(`Could not start the run: ${e.message || e}`)
    setBusyId(null)
  }
}

function PresetCards({ presets, liveAvailable, busyId, setBusyId, setError }) {
  const ordered = orderPresets(presets)
  return (
    <div className="sheet-grid preset-grid">
      {ordered.map((p, i) => {
        const h = p.headlines
        return (
          <div className="sheet preset-card" key={p.id}>
            <div className="mono sheet-no">DWG CR-{String(i + 1).padStart(3, '0')}</div>
            <h3>{p.title}</h3>
            <p className="mono preset-decision">{p.decision}</p>
            {h && (
              <p className="preset-stats">
                {h.hard} HARD · {h.at_risk} AT RISK
              </p>
            )}
            <div className="preset-actions">
              <Button
                kind="primary"
                onClick={() => startRun({ preset: p.id, mode: 'replay' }, setBusyId, setError, `replay:${p.id}`)}
                disabled={busyId != null || !p.has_recording}
              >
                {busyId === `replay:${p.id}` ? 'Starting…' : 'Replay'}
              </Button>
              {liveAvailable && p.has_recording && (
                <Button
                  kind="secondary"
                  onClick={() => startRun({ preset: p.id, mode: 'live' }, setBusyId, setError, `live:${p.id}`)}
                  disabled={busyId != null}
                >
                  {busyId === `live:${p.id}` ? 'Starting…' : 'Run live'}
                </Button>
              )}
            </div>
            {!p.has_recording && <p className="view-empty">No recording yet.</p>}
          </div>
        )
      })}
    </div>
  )
}

function useDecisionPreview(decision) {
  const [preview, setPreview] = useState(null)
  const timer = useRef(null)
  useEffect(() => {
    setPreview(null)
    if (!decision.trim()) return undefined
    timer.current = setTimeout(() => {
      postJSON('/api/parse-decision', { decision })
        .then((p) => setPreview(decisionPreviewText(p)))
        .catch(() => setPreview(null))
    }, 350)
    return () => clearTimeout(timer.current)
  }, [decision])
  return preview
}

function UntrustedModal({ repo, onCancel, onConfirm, busy }) {
  const [checked, setChecked] = useState(false)
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Untrusted code warning">
      <div className="sheet modal-sheet">
        <h3>Run untrusted code?</h3>
        <p>
          <span className="mono">{repo}</span> is not a curated preset. Starting this analysis
          clones the repo and runs its code and test suite on this machine.
        </p>
        <label className="modal-check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />{' '}
          I understand this repo&apos;s code will execute here.
        </label>
        <div className="modal-actions">
          <Button kind="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button kind="primary" onClick={onConfirm} disabled={busy || !checked}>
            {busy ? 'Starting…' : 'Run analysis'}
          </Button>
        </div>
      </div>
    </div>
  )
}

function CustomForm({ liveAvailable, busyId, setBusyId, error, setError }) {
  const [repoUrl, setRepoUrl] = useState('')
  const [ref, setRef] = useState('')
  const [decision, setDecision] = useState('')
  const [guideUrl, setGuideUrl] = useState('')
  const [pkg, setPkg] = useState('')
  const [touched, setTouched] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const preview = useDecisionPreview(decision)
  const busy = busyId === 'custom'

  const urlError = touched ? validateGithubUrl(repoUrl) : null
  const decisionError = touched && !decision.trim() ? 'Decision is required.' : null
  const guideError =
    touched && guideUrl.trim() && !/^https?:\/\//i.test(guideUrl.trim())
      ? 'Guide URL must start with http(s)://.'
      : null
  const valid = !validateGithubUrl(repoUrl) && decision.trim() && !guideError

  const submit = (e) => {
    e.preventDefault()
    setTouched(true)
    if (!valid || !liveAvailable) return
    setShowModal(true)
  }

  const confirm = () => {
    setShowModal(false)
    startRun(
      {
        repo_url: repoUrl.trim(),
        ref: ref.trim() || null,
        decision: decision.trim(),
        guide_url: guideUrl.trim() || null,
        package: pkg.trim() || null,
        mode: 'live',
        allow_untrusted: true,
      },
      setBusyId,
      setError,
      'custom',
    )
  }

  return (
    <form className="sheet custom-form" onSubmit={submit}>
      <h3>Analyze a repo</h3>
      {!liveAvailable && (
        <p className="replay-only">
          <Status tone="mute" text="Replay only — set MODEL_API_KEY to run live analyses." />
        </p>
      )}
      <label className="field">
        <span className="field-label">Repo URL</span>
        <input
          type="url"
          placeholder="https://github.com/owner/repo"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          disabled={busy}
        />
        {urlError && <span className="field-error">{urlError}</span>}
      </label>
      <label className="field">
        <span className="field-label">Ref (optional)</span>
        <input
          type="text"
          placeholder="commit, branch, or tag"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          disabled={busy}
        />
      </label>
      <label className="field">
        <span className="field-label">Decision</span>
        <input
          type="text"
          placeholder="upgrade numpy to 2.0 · upgrade werkzeug from 2.2 to 3.0"
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          disabled={busy}
        />
        {decisionError && <span className="field-error">{decisionError}</span>}
        {preview && !decisionError && <span className="field-preview mono">{preview}</span>}
      </label>
      <label className="field">
        <span className="field-label">Migration guide URL (optional)</span>
        <input
          type="url"
          placeholder="https://…"
          value={guideUrl}
          onChange={(e) => setGuideUrl(e.target.value)}
          disabled={busy}
        />
        {guideError && <span className="field-error">{guideError}</span>}
      </label>
      <label className="field">
        <span className="field-label">Package override (optional)</span>
        <input
          type="text"
          placeholder="top-level package, if detection gets it wrong"
          value={pkg}
          onChange={(e) => setPkg(e.target.value)}
          disabled={busy}
        />
      </label>
      <Button kind="primary" disabled={busy || !valid || !liveAvailable}>
        {busy ? 'Starting…' : 'Run live analysis'}
      </Button>
      {showModal && (
        <UntrustedModal repo={repoUrl.trim()} onCancel={() => setShowModal(false)} onConfirm={confirm} busy={busy} />
      )}
    </form>
  )
}

export default function NewView() {
  const { data: presets, error: presetsError } = useApi('/api/presets')
  const status = useStatus()
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  const liveAvailable = status?.live_available === true

  return (
    <div className="page new-page">
      <h2>New analysis</h2>
      {error && <div className="view-error">{error}</div>}
      <section>
        <h3>Replay a drawing</h3>
        {!presets && !presetsError && <p>Loading presets…</p>}
        {presetsError && <div className="view-error">Could not load presets: {presetsError}</div>}
        {presets && (
          <PresetCards
            presets={presets}
            liveAvailable={liveAvailable}
            busyId={busyId}
            setBusyId={setBusyId}
            setError={setError}
          />
        )}
        {status && !liveAvailable && (
          <p className="replay-note">
            <RedNote>no key needed — every preset replays offline</RedNote>
          </p>
        )}
      </section>
      <section>
        <CustomForm
          liveAvailable={liveAvailable}
          busyId={busyId}
          setBusyId={setBusyId}
          error={error}
          setError={setError}
        />
      </section>
    </div>
  )
}
