import React, { useEffect, useState } from 'react'
import { getJSON, withOptional } from '../api'
import { LogRows } from '../log'
import { Chip, Sheet, Status } from '../components'
import '../setup.css'

/* Artifact JSON that refetches as setup-stage events land, so a live run
 * fills in detection, envs, and baselines without a remount. Missing
 * files are null (optional fetch), never console 404s. */
function useLiveArtifact(runId, name, tick) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let live = true
    getJSON(withOptional(`/api/runs/${runId}/artifacts/${name}`))
      .then((d) => live && setData(d))
      .catch(() => live && setData(null))
    return () => {
      live = false
    }
  }, [runId, name, tick])
  return data
}

function fmtCounts(counts) {
  const entries = Object.entries(counts || {}).filter(([, v]) => v != null)
  if (entries.length === 0) return null
  return entries
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([k, v]) => `${v} ${k}`)
    .join(', ')
}

function Kv({ label, value }) {
  if (value == null || value === '') return null
  return (
    <div className="setup-kv">
      <span className="setup-key">{label}</span>
      <span className="mono setup-val">{value}</span>
    </div>
  )
}

function Provenance({ value }) {
  if (value === 'reused') return <Chip sky>cached</Chip>
  if (value === 'built') return <span className="mono setup-built">built fresh</span>
  return null
}

function EnvCard({ title, env, decisionPkg }) {
  if (!env) return null
  const freeze = env.freeze || {}
  const pinned = decisionPkg ? (freeze[decisionPkg] ?? null) : null
  return (
    <Sheet className="env-card">
      <div className="mono sheet-no">{title}</div>
      {decisionPkg && (
        <div className="mono env-pinned">
          {decisionPkg} {pinned ?? '(version unknown)'}
        </div>
      )}
      <Kv label="python" value={env.python} />
      <div className="setup-kv">
        <span className="setup-key">env</span>
        <span className="setup-val">
          <Provenance value={env.provenance} />
        </span>
      </div>
      {env.note && <p className="env-note">{env.note}</p>}
    </Sheet>
  )
}

export default function SetupView({ runId, snapshot, events }) {
  const setupEvents = (events || []).filter((e) => e.stage === 'setup')
  const artifactTick = (events || []).filter((e) => e.type === 'artifact').length
  const project = useLiveArtifact(runId, 'project.json', artifactTick)
  const envs = useLiveArtifact(runId, 'envs.json', artifactTick)
  const scores = useLiveArtifact(runId, 'scores.json', artifactTick)
  const manifest = snapshot?.manifest || {}
  const active = snapshot?.status === 'running' || snapshot?.status === 'queued'

  const testDeps = [
    ...(project?.test_requirements || []),
    ...(project?.test_extras || []),
    ...(project?.test_specs || []),
  ]
  const collateral = Object.entries(envs?.collateral || {}).sort(([a], [b]) =>
    a < b ? -1 : 1,
  )
  const baselineCounts = fmtCounts(scores?.baseline_counts)
  const decisionPkg = envs?.decision?.package || manifest.decision_package || null

  return (
    <div className="run-view setup-view">
      <h2>Setup</h2>

      <section>
        <h3>Clone</h3>
        <Sheet>
          <Kv label="repo" value={project?.repo_url || manifest.repo_url} />
          <Kv label="ref" value={project?.ref || manifest.ref} />
          <Kv
            label="commit"
            value={(project?.resolved_commit || manifest.resolved_commit || '').slice(0, 12)}
          />
          <Kv label="checkout" value={project?.workspace || manifest.workspace} />
          <Kv label="commit date" value={project?.commit_date} />
        </Sheet>
      </section>

      <section>
        <h3>Detection</h3>
        {project ? (
          <Sheet>
            <Kv label="package" value={project.package} />
            <Kv
              label="python"
              value={
                project.python_version
                  ? `${project.python_version}${project.requires_python ? ` (${project.requires_python})` : ''}`
                  : null
              }
            />
            <Kv label="test command" value={(project.test_command || []).join(' ')} />
            {testDeps.length > 0 && (
              <div className="setup-kv">
                <span className="setup-key">test deps</span>
                <span className="mono setup-val">{testDeps.join(', ')}</span>
              </div>
            )}
            {project.pytest_required_plugins && (
              <Kv label="pytest plugins" value={project.pytest_required_plugins} />
            )}
          </Sheet>
        ) : (
          <p className="view-empty">
            {active ? 'Detecting the package and test setup…' : 'No detection recorded yet.'}
          </p>
        )}
      </section>

      <section>
        <h3>Environments</h3>
        {envs ? (
          <>
            <div className="sheet-grid env-grid">
              <EnvCard title="BEFORE" env={envs.before} decisionPkg={decisionPkg} />
              <EnvCard title="AFTER" env={envs.after} decisionPkg={decisionPkg} />
            </div>
            {collateral.length > 0 && (
              <Sheet>
                <div className="mono sheet-no">COLLATERAL UPGRADES</div>
                {collateral.map(([pkg, v]) => (
                  <div className="mono collateral-row" key={pkg}>
                    {pkg} {v.before ?? '?'} → {v.after ?? '?'}
                  </div>
                ))}
              </Sheet>
            )}
            {collateral.length === 0 && (
              <p className="view-empty">No collateral upgrades — only the decided package moved.</p>
            )}
            {envs.conflict != null && (
              <p>
                <Status tone="warn" text={`env conflict: ${envs.conflict}`} />
              </p>
            )}
          </>
        ) : (
          <p className="view-empty">
            {active ? 'Building before/after environments…' : 'No environments recorded yet.'}
          </p>
        )}
      </section>

      <section>
        <h3>Baseline</h3>
        {baselineCounts || scores?.unhealthy_baseline ? (
          <Sheet>
            {baselineCounts && (
              <div className="mono baseline-counts">{baselineCounts}</div>
            )}
            {scores?.unhealthy_baseline && (
              <Status tone="warn" text="unhealthy baseline" />
            )}
            {scores?.testmap_tests != null && (
              <div className="mono baseline-counts">
                test map: {scores.testmap_tests} tests
                {scores.testmap_collected === false ? ' (collection incomplete)' : ''}
              </div>
            )}
          </Sheet>
        ) : (
          <p className="view-empty">
            {active ? 'Running the baseline suite…' : 'No baseline recorded yet.'}
          </p>
        )}
      </section>

      <section>
        <h3>Log</h3>
        <LogRows events={setupEvents} runId={runId} />
        {setupEvents.length === 0 && (
          <p className="view-empty">
            {active ? 'Waiting for setup events…' : 'No setup events recorded.'}
          </p>
        )}
      </section>
    </div>
  )
}
