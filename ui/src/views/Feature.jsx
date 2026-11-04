import React, { useState } from 'react'
import { postJSON, useApi } from '../api'
import { navigate } from '../router'
import { Button, Sheet, Status } from '../components'
import InterpretView from './Interpret'
import InvestigateView from './Investigate'
import GraphView from './Graph'
import VerifyView from './Verify'
import FixView from './Fix'
import ReportView from './Report'
import investigationDemo from '../../test/fixtures/investigation.example.json'
import fixDemo from '../../test/fixtures/fix.example.json'
import '../features.css'

const NIBABEL_PRESET = 'nibabel-numpy2'
const PRESET_RUN = `preset:${NIBABEL_PRESET}`

const FEATURES = [
  {
    id: 'decision',
    title: 'Decision',
    blurb:
      'Every run starts with a plain-English decision — "upgrade numpy to 2.0" — parsed into a package and versions. Vendor switches like "move to the Meta API" skip the grammar and go straight to code-aware interpretation.',
    view: 'setup',
    render: () => <DecisionDemo />,
  },
  {
    id: 'change-set',
    title: 'Change set',
    blurb:
      'Muse Spark reads the migration guide and extracts every breaking change as a quoted item: symbol, kind, release, and replacement. Each quote is verified against the source before it can seed the prediction.',
    view: 'interpret',
    render: () => <InterpretView runId={PRESET_RUN} />,
  },
  {
    id: 'investigation',
    title: 'Investigation',
    blurb:
      'The investigator agent traces each change with deterministic tools — usages, importers, probes — and submits findings citing the exact tool calls. Below is an example trace; live runs show the real one.',
    view: 'investigate',
    render: () => <InvestigateView runId={PRESET_RUN} snapshot={null} events={[]} demo={investigationDemo} />,
  },
  {
    id: 'prediction',
    title: 'Prediction',
    blurb:
      'Static propagation walks every usage to the package root, producing HARD and AT RISK sets plus the main import chain. The dominoes fall from the first break to the root.',
    view: 'graph',
    render: () => <GraphView runId={PRESET_RUN} />,
  },
  {
    id: 'proof',
    title: 'Proof',
    blurb:
      'Before and after environments run the real import probes and test suites. Predicted chains and failing tests are scored against actuals — precision and recall, not vibes.',
    view: 'verify',
    render: () => <VerifyView runId={PRESET_RUN} />,
  },
  {
    id: 'fix',
    title: 'Fix',
    blurb:
      'Each break gets a proposed patch, applied to a throwaway copy and proven by re-running probes and tests until the counter goes green. Below is an example loop; live runs show the real one.',
    view: 'fix',
    render: () => <FixView runId={PRESET_RUN} snapshot={null} demo={fixDemo} />,
  },
  {
    id: 'report',
    title: 'Report',
    blurb:
      'The narrative makes one checkable claim at a time, each verified against the artifacts. The scoreboard rolls up chain match, test precision/recall, and provenance.',
    view: 'report',
    render: () => <ReportView runId={PRESET_RUN} />,
  },
  {
    id: 'grounding',
    title: 'Grounding',
    blurb:
      'The model may only assert what a tool returned. Quotes link to the guide at the cited span, findings cite tool calls, and Meta capabilities light up only once a recording proves them.',
    view: 'report',
    render: () => <GroundingDemo />,
  },
  {
    id: 'replay',
    title: 'Replay',
    blurb:
      'Every run records its model calls, commands, and events. A replay re-streams them with no key and no network — judges see exactly what the live run saw, byte for byte.',
    view: 'setup',
    render: () => <ReplayDemo />,
  },
]

function DecisionDemo() {
  const { data: presets } = useApi('/api/presets')
  const preset = (presets || []).find((p) => p.id === NIBABEL_PRESET)
  if (!preset) return <p>Loading decision…</p>
  return (
    <Sheet>
      <div className="mono feature-decision">“{preset.decision}”</div>
      <p>
        {preset.decision_parsed ? (
          <>
            Parsed to package <span className="mono">{preset.decision_parsed.package}</span> version{' '}
            <span className="mono">{preset.decision_parsed.version}</span>.
          </>
        ) : (
          'Free-form decision — the model interprets it at run time.'
        )}
      </p>
      <p className="mono feature-meta">
        {preset.title} · {preset.target}
      </p>
    </Sheet>
  )
}

function GroundingDemo() {
  const { data } = useApi('/api/meta-usage')
  const caps = data?.capabilities ?? []
  const changeset = useApi(`/api/runs/${PRESET_RUN}/artifacts/changeset.json`).data
  const quoted = (changeset?.items || []).filter((i) => i.quote).slice(0, 3)
  return (
    <>
      <Sheet>
        <div className="mono sheet-no">META CAPABILITIES IN USE</div>
        {caps.map((c) => (
          <div key={c.id} className="mono grounding-row">
            ✓ {c.label}
            {c.evidence ? ` — ${c.evidence}` : ''}
          </div>
        ))}
      </Sheet>
      {quoted.length > 0 && (
        <Sheet>
          <div className="mono sheet-no">QUOTED FROM THE GUIDE</div>
          {quoted.map((i) => (
            <p key={i.symbol} className="grounding-quote">
              <span className="mono">{i.symbol}</span> — “{i.quote.slice(0, 120)}
              {i.quote.length > 120 ? '…' : ''}”{' '}
              {(i.link || changeset.source_url) && (
                <a href={i.link || changeset.source_url} target="_blank" rel="noreferrer">
                  source
                </a>
              )}
            </p>
          ))}
        </Sheet>
      )}
    </>
  )
}

function ReplayDemo() {
  const { data: prov } = useApi(`/api/runs/${PRESET_RUN}/provenance`)
  return (
    <Sheet>
      <div className="mono sheet-no">RECORDED IN THE NIBABEL DRAWING</div>
      <p className="mono">
        {(prov?.llm_calls ?? '…')} model calls · {(prov?.cmds ?? '…')} commands ·{' '}
        {(prov?.fetches ?? '…')} fetches
      </p>
      <p>
        Replays re-stream these recordings with <span className="mono">MODEL_API_KEY</span> unset —
        no network, no key, identical bytes.
      </p>
    </Sheet>
  )
}

export default function FeatureView({ id }) {
  const index = FEATURES.findIndex((f) => f.id === id)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  if (index < 0) {
    return (
      <div className="page">
        <Sheet>
          <h2>Unknown feature</h2>
          <p>Pick one of the documented features:</p>
          <ul>
            {FEATURES.map((f) => (
              <li key={f.id}>
                <a href={`#/features/${f.id}`}>{f.title}</a>
              </li>
            ))}
          </ul>
        </Sheet>
      </div>
    )
  }

  const feature = FEATURES[index]
  const prev = FEATURES[(index + FEATURES.length - 1) % FEATURES.length]
  const next = FEATURES[(index + 1) % FEATURES.length]

  const seeFullRun = async () => {
    setBusy(true)
    setError(null)
    try {
      const run = await postJSON('/api/runs', { preset: NIBABEL_PRESET, mode: 'replay' })
      navigate(`/runs/${run.id}/${feature.view}`)
    } catch (e) {
      setError(`Could not start the replay: ${e.message || e}`)
      setBusy(false)
    }
  }

  return (
    <div className="page feature-page">
      <div className="mono sheet-no">
        FEATURE {index + 1} / {FEATURES.length}
      </div>
      <h2>{feature.title}</h2>
      <div className="feature-split">
        <div className="feature-blurb">
          <p>{feature.blurb}</p>
          <Button kind="primary" onClick={seeFullRun} disabled={busy}>
            {busy ? 'Starting…' : 'See it in a full run'}
          </Button>
          {error && <div className="view-error">{error}</div>}
        </div>
        <div className="feature-demo">
          <div className="mono sheet-no">LIVE PRESET DATA · NIBABEL</div>
          {feature.render()}
        </div>
      </div>
      <nav className="feature-nav">
        <a href={`#/features/${prev.id}`}>← {prev.title}</a>
        <span className="spacer" />
        <a href={`#/features/${next.id}`}>{next.title} →</a>
      </nav>
    </div>
  )
}
