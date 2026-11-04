import React from 'react'
import { useApi, useOptionalJson, useText } from '../api'
import { Chip, DimensionRule, FileIcon, MatchStamp, Sheet, Status } from '../components'
import { describeMatch, describeVersions } from '../verifyUtils'
import { FailureGroups, TestPrediction } from './VerifyTests'
import '../verify.css'

/* Mirror of stages._artifact_slug: probe "layer2 (peeled)" lives at
 * verify/layer2-peeled.log. A 404 just hides the full log; the layer
 * card still shows the recorded error tail. */
export function layerSlug(name) {
  return (
    (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'probe'
  )
}

function fmtCounts(counts, emptyText) {
  const entries = Object.entries(counts || {}).filter(([, v]) => v != null)
  if (entries.length === 0) return emptyText
  return entries
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([k, v]) => `${v} ${k}`)
    .join(', ')
}

function ProbeResult({ layer, kind }) {
  if (!layer) return <span className="mono">no probe recorded</span>
  const what = kind === 'decision' ? 'probe' : 'import'
  return layer.ok ? (
    <Status tone="ok" text={`${what} passed`} />
  ) : (
    <Status tone="flag" text={`${what} failed`} />
  )
}

export function EnvSheet({ title, version, layer, counts, excluded, warnings, kind, emptyCounts }) {
  return (
    <Sheet className="env-sheet card">
      <div className="mono sheet-no">{title}</div>
      <div className="env-version mono">{version || 'version unknown'}</div>
      <div className="env-row">
        <ProbeResult layer={layer} kind={kind} />
      </div>
      <div className="env-row mono">{fmtCounts(counts, emptyCounts)}</div>
      {excluded != null && (
        <div className="env-row">
          <span className="mono">{excluded} baseline failures excluded</span>
        </div>
      )}
      {(warnings || []).map((w) => (
        <div className="env-row" key={w}>
          <Status tone="warn" text={w} />
        </div>
      ))}
    </Sheet>
  )
}

function ChainLine({ modules }) {
  if (!modules || modules.length === 0) return <span className="mono">(no failures)</span>
  return (
    <span className="mono chain-line">
      {modules.map((m, i) => (
        <React.Fragment key={`${m}-${i}`}>
          {i > 0 && <span className="chain-arrow"> → </span>}
          <span>{m}</span>
        </React.Fragment>
      ))}
    </span>
  )
}

function LayerLog({ runId, layer }) {
  const { data: log } = useText(
    `/api/runs/${runId}/artifacts/verify/${layerSlug(layer.name)}.log`,
  )
  if (!log) return null
  return (
    <details className="layer-log">
      <summary>scrubbed traceback</summary>
      <pre className="log-tail">{log}</pre>
    </details>
  )
}

function LayerCard({ runId, layer, kind }) {
  return (
    <Sheet className="layer-card card">
      <div className="layer-head">
        <strong>{layer.name}</strong>
        <span className="spacer" />
        <ProbeResult layer={layer} kind={kind} />
      </div>
      <div className="layer-row">
        <span className="layer-label">actual chain</span>
        <ChainLine modules={layer.actual} />
      </div>
      {layer.failure_site && (
        <div className="layer-row">
          <span className="layer-label">error line</span>
          <Chip accent>{layer.failure_site}</Chip>
        </div>
      )}
      {(layer.missing || []).length > 0 && (
        <div className="layer-row mono layer-meta">
          <span className="missed">missed: {(layer.missing || []).join(', ')}</span>
        </div>
      )}
      {layer.error_tail && !layer.ok && (
        <pre className="log-tail layer-tail">{layer.error_tail}</pre>
      )}
      <LayerLog runId={runId} layer={layer} />
    </Sheet>
  )
}

export default function VerifyView({ runId }) {
  const { data: report, error } = useApi(`/api/runs/${runId}/artifacts/report.json`)
  const scores = useOptionalJson(`/api/runs/${runId}/artifacts/scores.json`)
  const envs = useOptionalJson(`/api/runs/${runId}/artifacts/envs.json`)

  if (error) {
    return (
      <div className="run-view">
        <h2>Verify</h2>
        <div className="view-error">
          Nothing to verify yet — the verify stage hasn&apos;t written its report ({error}).
        </div>
      </div>
    )
  }
  if (!report) {
    return (
      <div className="run-view">
        <h2>Verify</h2>
        <p>Loading verification…</p>
      </div>
    )
  }

  const layers = report.layers || []
  // Empty counts mean the run probed imports instead of running the
  // suite (report.json exists, so scores aren't merely pending).
  const probeWord = report.probe_kind === 'decision' ? 'probe' : 'import probe'
  const scope = String(runId).startsWith('preset:') ? ' for this preset' : ''
  const emptyCounts = `${probeWord} only (full suite not run${scope})`
  const beforeLayer = layers.find((l) => (l.name || '').toLowerCase().startsWith('before'))
  const afterLayers = layers.filter((l) => l !== beforeLayer)
  const firstAfter = afterLayers[0]
  const versions = describeVersions(report, envs)
  const match = describeMatch(report.spine_match, firstAfter)
  const baselineFailed = scores?.baseline_failed
  const excluded = Array.isArray(baselineFailed) ? baselineFailed.length : baselineFailed ?? null
  const warnings = []
  if (scores?.unhealthy_baseline) warnings.push('unhealthy baseline')
  if (scores?.after_escalated) warnings.push('after suite needed --noconftest')
  if (scores?.suites === 'skipped-no-coverage') warnings.push('no coverage: baseline counts only')

  return (
    <div className="run-view verify-view">
      <h2>Verify</h2>

      <div className="env-grid">
        <EnvSheet
          title="BEFORE"
          version={versions.before}
          layer={beforeLayer}
          counts={scores?.baseline_counts}
          excluded={null}
          warnings={[]}
          kind={report.probe_kind}
          emptyCounts={emptyCounts}
        />
        <EnvSheet
          title="AFTER"
          version={versions.after}
          layer={firstAfter}
          counts={scores?.after_counts}
          excluded={excluded}
          warnings={warnings}
          kind={report.probe_kind}
          emptyCounts={emptyCounts}
        />
      </div>
      {!beforeLayer && !versions.before && (
        <p className="view-empty">No before environment recorded for this run (decision probe).</p>
      )}

      <section>
        <h3 className="chain-head">
          <span>
            Predicted vs actual chain{' '}
            {match === 'match' && <Status tone="ok" text="MATCH" />}
            {match === 'differ' && <Status tone="flag" text="DIFFER" />}
            {match === 'not-compared' && <Status tone="mute" text="NOT COMPARED" />}
          </span>
          {match === 'match' && (
            <>
              <span className="spacer" />
              <MatchStamp small />
            </>
          )}
        </h3>
        {match === 'not-compared' && (
          <p className="view-empty">
            The after import failed without a captured target chain, so there is nothing to
            compare the prediction against.
          </p>
        )}
        <Sheet className="card">
          <div className="layer-row">
            <span className="layer-label">predicted</span>
            <ChainLine modules={report.spine} />
          </div>
          <div className="layer-row">
            <span className="layer-label">actual{firstAfter ? ` (${firstAfter.name})` : ''}</span>
            <ChainLine modules={firstAfter?.actual} />
          </div>
        </Sheet>
      </section>

      <section>
        <h3>Layers</h3>
        {layers.length === 0 && <p className="view-empty">No probe layers recorded.</p>}
        {layers.map((l) => (
          <LayerCard key={l.name} runId={runId} layer={l} kind={report.probe_kind} />
        ))}
      </section>

      <FailureGroups scores={scores} />
      <TestPrediction scores={scores} />
    </div>
  )
}
