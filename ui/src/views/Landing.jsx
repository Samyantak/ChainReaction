import React, { useState } from 'react'
import { postJSON, useApi } from '../api'
import { navigate } from '../router'
import { useTheme } from '../theme'
import { CodeBlock } from '../CodeBlock'
import {
  Button,
  Chip,
  DimensionRule,
  RedNote,
  Sheet,
  Status,
  TitleBlock,
} from '../components'
import '../landing.css'

const GITHUB_URL = 'https://github.com/Samyantak/ChainReaction'
const NIBABEL_PRESET = 'nibabel-numpy2'

async function startReplay(presetId, setBusy, setError) {
  setBusy(true)
  setError(null)
  try {
    const run = await postJSON('/api/runs', { preset: presetId, mode: 'replay' })
    navigate(`/runs/${run.id}/setup`)
  } catch (e) {
    setError(`Could not start the replay: ${e.message || e}`)
    setBusy(false)
  }
}

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

function LandingHeader({ onReplay, busy, theme, onToggleTheme }) {
  return (
    <header className="landing-header">
      <a className="wordmark" href="#/">
        ChainReaction
      </a>
      <span className="dwg-tag mono">DWG CR-001</span>
      <nav>
        <button type="button" className="navlink" onClick={() => scrollToId('sheets')}>
          How it works
        </button>
        <a href="#/features/grounding">Grounding</a>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">
          GitHub
        </a>
      </nav>
      <span className="spacer" />
      <Button kind="secondary" onClick={onReplay} disabled={busy}>
        {busy ? (
          'Starting…'
        ) : (
          <>
            <span className="long">Watch a replay</span>
            <span className="short">Replay</span>
          </>
        )}
      </Button>
      <button className="theme-toggle" type="button" onClick={onToggleTheme}>
        {theme === 'dark' ? 'Light' : 'Dark'}
      </button>
    </header>
  )
}

/* The import chain as boxed mono chips. The first hop IS the root import,
 * so it becomes the leading chip; consecutive same-file hops keep the
 * deeper site. Built entirely from chain_evidence. */
function ChainStrip({ headlines }) {
  const evidence = headlines?.chain_evidence
  const chain = headlines?.chain
  if (!evidence || evidence.length === 0 || !chain || chain.length === 0) return null
  const base = (file) => file.split('/').pop()
  const chips = []
  for (let i = 1; i < evidence.length; i += 1) {
    const [, , file, line] = evidence[i]
    const label = `${base(file)}:${line}`
    if (chips.length > 0 && chips[chips.length - 1].file === file) {
      chips[chips.length - 1] = { label, file }
    } else {
      chips.push({ label, file })
    }
  }
  const lastSymbol = headlines.first_break?.symbol
  return (
    <div className="chain-strip" aria-label="Import chain">
      <Chip>import {chain[0]}</Chip>
      {chips.map((c, i) => {
        const last = i === chips.length - 1
        return (
          <React.Fragment key={`${c.label}-${i}`}>
            <span className="chain-arrow" aria-hidden="true">
              ←
            </span>
            {last && lastSymbol ? (
              <Chip accent>
                {c.label} · {lastSymbol}
              </Chip>
            ) : (
              <Chip>{c.label}</Chip>
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

const KIND_VERBS = { removed: 'was removed', deprecated: 'was deprecated' }

/* The alias the code actually uses (np.sctypes), read from the breaking
 * line itself — never a hard-coded alias map. */
function shortAlias(lineText, symbol) {
  const last = symbol.split('.').pop().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = (lineText || '').match(new RegExp(`([A-Za-z0-9_.]*${last})`))
  return match ? match[1] : symbol
}

function CodeDrawing({ preset }) {
  const h = preset.headlines
  const brk = h.first_break
  const start = Math.max(1, brk.line - 6)
  const end = brk.line + 6
  const { data: src } = useApi(
    `/api/runs/preset:${preset.id}/source?path=${encodeURIComponent(brk.path)}&start=${start}&end=${end}`,
  )
  const { data: changeset } = useApi(`/api/runs/preset:${preset.id}/artifacts/changeset.json`)
  const item = changeset?.items?.find((i) => i.symbol === brk.symbol)
  const breakText = (src?.lines ?? []).find((l) => l.no === brk.line)?.text ?? ''
  const alias = shortAlias(breakText, brk.symbol)
  const verb = KIND_VERBS[item?.kind]
  const parsed = preset.decision_parsed
  let callout1 = `breaks here first — ${alias}`
  if (verb && parsed) {
    callout1 = `breaks here first — ${alias} ${verb} in ${parsed.package} ${parsed.version}`
  } else if (verb) {
    callout1 = `breaks here first — ${alias} ${verb}`
  }
  const callout2 = item?.replacement || null

  return (
    <div className="code-drawing">
      {item && (
        <div className="callout">
          <RedNote>
            {callout1}
            {callout2 && (
              <>
                <br />
                {callout2}
              </>
            )}
          </RedNote>
          <svg className="leader" viewBox="0 0 40 32" aria-hidden="true">
            <path
              d="M32 2 C 24 12, 16 18, 6 28 M6 28 l9 -3 M6 28 l2 -9"
              fill="none"
              stroke="var(--red)"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </div>
      )}
      <CodeBlock
        runId={`preset:${preset.id}`}
        path={brk.path}
        center={brk.line}
        radius={6}
        breakLine={brk.line}
        lines={src?.lines ?? null}
      />
      <TitleBlock
        cells={[
          ['DECISION', preset.decision],
          ['IMPACT', `${h.hard} HARD · ${h.at_risk} AT RISK`, true],
          ['CHAIN', `${h.chain.length} hops`],
          ['SHEET', '4 / 7'],
        ]}
      />
    </div>
  )
}

function Hero({ preset, busy, error, onReplay }) {
  return (
    <section className="hero">
      <div className="hero-copy">
        <h1>Red-line your upgrade before you ship it.</h1>
        <p>
          ChainReaction investigates dependency upgrades before you merge them: it reads the
          migration guide, traces every breaking change through your code, proves the impact
          with real test runs, and lands a verified fix.
        </p>
        <div className="hero-buttons">
          <Button kind="primary" onClick={onReplay} disabled={busy || !preset?.has_recording}>
            {busy ? 'Starting…' : 'Open the nibabel drawing'}
          </Button>
          <Button kind="secondary" href="#/new">
            Analyze a repo
          </Button>
        </div>
        {error && <div className="view-error">{error}</div>}
        <RedNote>no key needed — every run replays offline</RedNote>
      </div>
      <div className="hero-drawing">
        {preset?.has_recording && preset.headlines?.first_break ? (
          <CodeDrawing preset={preset} />
        ) : (
          <Sheet>
            <p>The nibabel recording is missing — record presets to see the drawing.</p>
          </Sheet>
        )}
      </div>
    </section>
  )
}

const SHEETS = [
  ['decision', 'Decision', 'The upgrade under review, parsed into a decision.'],
  ['change-set', 'Change set', 'Every breaking change, quoted from the guide.'],
  ['investigation', 'Investigation', 'The agent traces each change with tools.'],
  ['prediction', 'Prediction', 'The cascade, before anything runs.'],
  ['proof', 'Proof', 'Real tests confirm or deny the cascade.'],
  ['fix', 'Fix', 'Proposed patches, proven round by round.'],
  ['report', 'Report', 'Every claim checked against evidence.'],
]

function SevenSheets({ nibabel }) {
  const h = nibabel?.headlines
  const notes = {}
  if (h?.interpret?.found != null && h.interpret.table_size != null) {
    notes['change-set'] = `${h.interpret.found} / ${h.interpret.table_size} found`
  }
  if (h?.chain_match) notes.prediction = 'matches the traceback'
  if (h?.fix) notes.fix = `${h.fix.rounds} rounds to green`
  return (
    <section id="sheets" className="sheets-section">
      <div className="sheet-grid sheets-grid">
        {SHEETS.map(([id, title, desc], i) => (
          <a className="sheet sheet-card" href={`#/features/${id}`} key={id}>
            <div className="mono sheet-no">SHEET {i + 1} / 7</div>
            <h3>{title}</h3>
            <p>{desc}</p>
            {notes[id] && (
              <div className="sheet-note">
                <RedNote>{notes[id]}</RedNote>
              </div>
            )}
          </a>
        ))}
      </div>
    </section>
  )
}

function Measurements({ nibabel }) {
  const h = nibabel?.headlines
  if (!h) return null
  const stats = []
  stats.push({ value: String(h.hard), label: 'HARD modules', source: 'prediction.json' })
  if (h.chain_match != null) {
    stats.push({
      value: h.chain_match ? 'MATCH' : 'DIFFER',
      label: 'predicted vs actual chain',
      source: 'verify/layer1.log',
      status: h.chain_match ? 'ok' : 'flag',
    })
  }
  if (h.interpret?.found != null && h.interpret.table_size != null) {
    stats.push({
      value: `${h.interpret.found}/${h.interpret.table_size}`,
      label: 'removals found',
      source: 'changeset.json',
    })
  }
  if (h.files) {
    stats.push({
      value: `${h.files.breaking_found}/${h.files.breaking_total}`,
      label: 'breaking files',
      sub: `${h.files.found}/${h.files.total} including behavior changes`,
      source: h.files.pr_ref,
    })
  }
  return (
    <section className="measurements">
      {stats.map((s) => (
        <div className="measure" key={s.label}>
          <DimensionRule />
          <div className="measure-value">
            {s.status ? <Status tone={s.status} text={s.value} /> : s.value}
          </div>
          <div className="measure-label">{s.label}</div>
          {s.sub && <div className="measure-sub">{s.sub}</div>}
          {s.source && <div className="mono measure-source">{s.source}</div>}
        </div>
      ))}
    </section>
  )
}

function GroundingBand() {
  const { data } = useApi('/api/meta-usage')
  const caps = data?.capabilities ?? []
  return (
    <section className="band">
      <div className="band-inner">
        <h2>Every mark has a source.</h2>
        <p>
          The model may only assert what a tool returned. Every claim on this page traces to a
          file:line, a cited changelog span, or a test result — and every run is recorded, so any
          result replays offline with no key.
        </p>
        <ul className="meta-list">
          {caps.map((c) => (
            <li key={c.id}>
              <span className="mono meta-cap">{c.label}</span>
              <span className="meta-where">{c.where}</span>
              {c.evidence && <span className="mono meta-evidence">{c.evidence}</span>}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function DrawingsOnFile({ presets, busy, onReplay }) {
  // The hero drawing leads; the rest keep API order (stable sort).
  const ordered = [...presets].sort((a, b) =>
    a.id === NIBABEL_PRESET ? -1 : b.id === NIBABEL_PRESET ? 1 : 0,
  )
  return (
    <section className="drawings">
      <h2>Drawings on file</h2>
      <p className="drawings-note">
        Targets are pre-existing repos, analyzed by ChainReaction — not part of this build.{' '}
        <a href="#/features/replay">How replay works</a>
      </p>
      <div className="sheet-grid drawings-grid">
        {ordered.map((p, i) => {
          const h = p.headlines
          const hasChain = (h?.chain?.length ?? 0) > 0
          return (
            <div className="sheet drawing-card" key={p.id}>
              <div className="mono sheet-no">DWG CR-{String(i + 1).padStart(3, '0')}</div>
              <h3>{p.title}</h3>
              <p className="mono drawing-decision">{p.decision}</p>
              {h && (
                <p className="drawing-stats">
                  {h.hard} HARD · {h.at_risk} AT RISK
                  {hasChain && h.chain_match != null && (
                    <>
                      {' · '}
                      <Status tone={h.chain_match ? 'ok' : 'flag'} text="chain" />
                    </>
                  )}
                  {!hasChain && h.suite && (
                    <>
                      {' · '}
                      <span className="mono">{h.suite.new_failures} new test failures</span>
                    </>
                  )}
                </p>
              )}
              <Button kind="secondary" onClick={() => onReplay(p.id)} disabled={busy || !p.has_recording}>
                {p.has_recording ? 'Replay' : 'No recording'}
              </Button>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="landing-footer">
      <span>Built during the hackathon · Oct 27 – Nov 4, 2026</span>
      <a href={GITHUB_URL} target="_blank" rel="noreferrer">
        GitHub
      </a>
      <a href={`${GITHUB_URL}#limitations`} target="_blank" rel="noreferrer">
        Limitations
      </a>
    </footer>
  )
}

export default function Landing() {
  const { data: presets, error } = useApi('/api/presets')
  const [busy, setBusy] = useState(false)
  const [replayError, setReplayError] = useState(null)
  const [theme, setTheme] = useTheme()
  const replay = (id) => startReplay(id, setBusy, setReplayError)

  return (
    <>
      <LandingHeader
        onReplay={() => replay(NIBABEL_PRESET)}
        busy={busy}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
      />
      <main>
        {!presets && !error && (
          <div className="landing">
            <Sheet>
              <p>Loading drawings…</p>
            </Sheet>
          </div>
        )}
        {error && (
          <div className="landing">
            <div className="view-error">Could not load presets: {error}</div>
          </div>
        )}
        {presets && (
          <>
            <div className="landing">
              <Hero
                preset={presets.find((p) => p.id === NIBABEL_PRESET)}
                busy={busy}
                error={replayError}
                onReplay={() => replay(NIBABEL_PRESET)}
              />
              <ChainStrip headlines={presets.find((p) => p.id === NIBABEL_PRESET)?.headlines} />
              <SevenSheets nibabel={presets.find((p) => p.id === NIBABEL_PRESET)} />
              <Measurements nibabel={presets.find((p) => p.id === NIBABEL_PRESET)} />
            </div>
            <GroundingBand />
            <div className="landing">
              <DrawingsOnFile presets={presets} busy={busy} onReplay={replay} />
            </div>
          </>
        )}
        <div className="landing">
          <Footer />
        </div>
      </main>
    </>
  )
}
