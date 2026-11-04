import React, { useEffect, useMemo, useState } from 'react'
import { postJSON, useApi, useOptionalJson } from '../api'
import { navigate } from '../router'
import { useTheme } from '../theme'
import { CodeBlock } from '../CodeBlock'
import { EnvSheet } from './Verify'
import { describeVersions } from '../verifyUtils'
import { STAGES } from '../stages'
import {
  capabilityStatus,
  countReplayable,
  miniRailStates,
  stripTiles,
  tipAngle,
} from '../polishUtils'
import {
  Button,
  Chip,
  DimensionRule,
  DotBreak,
  Eyebrow,
  FileIcon,
  Logo,
  MatchStamp,
  RedNote,
  RedUnderline,
  Reveal,
  Sheet,
  Status,
  TitleBlock,
} from '../components'
import { CountUp, Ticker, Tilt, prefersReduced, useInView, usePointerVars } from '../lively'
import '../landing.css'
import '../lively.css'

const GITHUB_URL = 'https://github.com/Samyantak/ChainReaction'
const NIBABEL_PRESET = 'nibabel-numpy2'

async function startReplay(presetId, setBusy, setError, view = 'setup') {
  setBusy(true)
  setError(null)
  try {
    const run = await postJSON('/api/runs', { preset: presetId, mode: 'replay' })
    navigate(`/runs/${run.id}/${view}`)
  } catch (e) {
    setError(`Could not start the replay: ${e.message || e}`)
    setBusy(false)
  }
}

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

function LandingHeader({ onReplay, busy, theme, onToggleTheme, replayCount }) {
  return (
    <header className="landing-header">
      <Logo />
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
      <span className="replay-pill mono">
        <span className="long">{replayCount} drawings replay offline</span>
        <span className="short">{replayCount} offline</span>
      </span>
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

const base = (file) => (file ? file.split('/').pop() : '')
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
      {h.chain_match && (
        <div className="hero-stamp">
          <MatchStamp />
        </div>
      )}
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
              pathLength={100}
              strokeDasharray="100 100"
              className="draw-in"
            />
          </svg>
        </div>
      )}
      <div className="measuring" aria-hidden="true">
        <span className="m-tick" />
        <span className="m-line" />
        <span className="mono m-label">
          {brk.path} · lines {start}–{end}
        </span>
        <span className="m-line" />
        <span className="m-tick" />
      </div>
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
  const ref = usePointerVars()
  return (
    <section className="hero hero-live" ref={ref}>
      <span className="hero-spot" aria-hidden="true" />
      <div className="hero-copy">
        <h1 className="hero-title">
          <span className="word" style={{ '--i': 0 }}>
            <RedUnderline>Red-line</RedUnderline>
          </span>{' '}
          {'your upgrade before you ship it.'.split(' ').map((w, i) => (
            <React.Fragment key={w + i}>
              <span className="word" style={{ '--i': i + 1 }}>
                {w}
              </span>{' '}
            </React.Fragment>
          ))}
        </h1>
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

/* The import chain as domino tiles tipping in sequence, root first. A
 * tile starts the nibabel replay and lands on the graph sheet. */
function ChainDominoes({ headlines, busy, onOpenGraph }) {
  const tiles = useMemo(
    () => stripTiles(headlines?.chain, headlines?.chain_evidence, headlines?.first_break),
    [headlines],
  )
  const [tipped, setTipped] = useState(0)
  const [round, setRound] = useState(0)
  const [ref, seen] = useInView(0.35)
  const reduced = useMemo(() => prefersReduced(), [])
  useEffect(() => {
    if (!seen) return undefined
    setTipped(0)
    // Root first, then each importer falls into the next, the red break last.
    const timers = tiles.map((_, i) =>
      setTimeout(() => setTipped(i + 1), reduced ? i * 60 : 350 + i * 230),
    )
    return () => timers.forEach(clearTimeout)
  }, [tiles, reduced, seen, round])
  if (tiles.length === 0) return null
  return (
    <section className="strip-section" ref={ref}>
      <div className="strip-head">
        <Eyebrow n="01" label="THE CASCADE" />
        <button type="button" className="btn btn-secondary btn-small" onClick={() => setRound((r) => r + 1)}>
          ↻ Knock them over again
        </button>
      </div>
      <div className={`strip-row${tipped >= tiles.length ? ' strip-done' : ''}`} aria-label="Import chain">
        {tiles.map((t, i) => (
          <button
            key={t.module}
            type="button"
            onClick={onOpenGraph}
            disabled={busy}
            title="Replay the nibabel run and open the graph"
            style={{ '--tip-angle': `${tipAngle(i)}deg` }}
            className={[
              'domino',
              `role-${t.role}`,
              i < tipped ? 'tipped' : 'upright',
              reduced ? 'no-motion' : '',
            ].join(' ')}
          >
            <span className="domino-top mono">
              <DotBreak text={t.module} />
            </span>
            <span className="domino-divider" aria-hidden="true" />
            <span className="domino-bottom mono">
              {t.file ? `${base(t.file)}:${t.line}` : 'package root'}
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}

function MiniRail({ states }) {
  return (
    <div className="mini-rail" aria-label="Stages">
      {STAGES.map((stage, i) => (
        <div className="mini-rail-item" key={stage}>
          <span className={`stage-dot state-${states[stage].state}`} aria-hidden="true" />
          <span className="mono mini-rail-sheet">SHEET {i + 1} / 7</span>
          <span className="mini-rail-name">{stage}</span>
          <span className="mono mini-rail-state">{states[stage].state}</span>
        </div>
      ))}
    </div>
  )
}

/* A framed mini run page rendered from the nibabel recording: title
 * block, stage rail, before/after environments, MATCH stamp. */
function RunPreview({ preset, busy, onReplay }) {
  const pid = preset?.has_recording ? `preset:${preset.id}` : null
  const { data: snapshot } = useApi(pid ? `/api/runs/${pid}` : null)
  const scores = useOptionalJson(pid ? `/api/runs/${pid}/artifacts/scores.json` : null)
  const envs = useOptionalJson(pid ? `/api/runs/${pid}/artifacts/envs.json` : null)
  const investigation = useOptionalJson(pid ? `/api/runs/${pid}/artifacts/investigation.json` : null)
  const fix = useOptionalJson(pid ? `/api/runs/${pid}/artifacts/fix.json` : null)
  if (!pid || !snapshot?.report) return null
  const report = snapshot.report
  const m = snapshot.manifest
  const h = preset.headlines
  const states = miniRailStates(snapshot.status, { investigation, fix })
  const layers = report.layers || []
  const beforeLayer = layers.find((l) => (l.name || '').toLowerCase().startsWith('before'))
  const firstAfter = layers.filter((l) => l !== beforeLayer)[0]
  const versions = describeVersions(report, envs)
  const probeWord = report.probe_kind === 'decision' ? 'probe' : 'import probe'
  const emptyCounts = `${probeWord} only (full suite not run for this preset)`
  const baselineFailed = scores?.baseline_failed
  const excluded = Array.isArray(baselineFailed) ? baselineFailed.length : baselineFailed ?? null
  const warnings = []
  if (scores?.unhealthy_baseline) warnings.push('unhealthy baseline')
  if (scores?.after_escalated) warnings.push('after suite needed --noconftest')
  if (scores?.suites === 'skipped-no-coverage') warnings.push('no coverage: baseline counts only')
  const repoName = (m.repo_url || '').replace(/\/$/, '').split('/').pop()
  const commit = (m.resolved_commit || m.ref || 'unknown').slice(0, 12)

  return (
    <section className="preview-section">
      <Eyebrow n="02" label="A RUN, LIVE OR REPLAYED" />
      <div className="sheet card preview-frame">
        {h.chain_match && (
          <div className="preview-stamp">
            <MatchStamp small />
          </div>
        )}
        <TitleBlock
          cells={[
            ['DECISION', m.decision || preset.decision],
            ['TARGET', `${repoName} @ ${commit}`],
            ['MODE', 'recording · replays offline'],
            ['CHAIN', `${h.chain.length} hops`],
          ]}
        />
        <div className="preview-body">
          <MiniRail states={states} />
          <div className="preview-compare">
            <div className="preview-envs">
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
            <div className="preview-actions">
              <Button kind="secondary" onClick={onReplay} disabled={busy}>
                {busy ? 'Starting…' : 'Replay this run'}
              </Button>
            </div>
          </div>
        </div>
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
      <Eyebrow n="03" label="SEVEN SHEETS" />
      <div className="sheet-rail" aria-hidden="true">
        {SHEETS.map((_, i) => (
          <span className="sheet-dot mono" key={i}>
            {i + 1}
          </span>
        ))}
      </div>
      <div className="sheets-grid">
        {SHEETS.map(([id, title, desc], i) => (
          <a className="sheet card lift sheet-card" href={`#/features/${id}`} key={id}>
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
    <section className="measurements-section">
      <Eyebrow n="04" label="MEASUREMENTS" />
      <div className="measurements">
        {stats.map((s) => (
          <div className="measure" key={s.label}>
            <DimensionRule />
            <div className="measure-value">
              {s.status ? <Status tone={s.status} text={s.value} /> : <CountUp value={s.value} />}
            </div>
            <div className="measure-label">{s.label}</div>
            {s.sub && <div className="measure-sub">{s.sub}</div>}
            {s.source && (
              <div className="mono measure-source">
                <FileIcon />
                {s.source}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function GroundingBand() {
  const { data } = useApi('/api/meta-usage')
  const caps = data?.capabilities ?? []
  return (
    <section className="band">
      <div className="band-inner">
        <Reveal>
          <Eyebrow n="05" label="GROUNDING" />
          <h2>Every mark has a source.</h2>
          <p>
            The model may only assert what a tool returned. Every claim on this page traces to a
            file:line, a cited changelog span, or a test result — and every run is recorded, so any
            result replays offline with no key.
          </p>
          <ul className="cap-grid">
            {caps.map((c) => (
              <li key={c.id} className="cap-card">
                <div className="cap-head">
                  <span className="mono cap-name">{c.label}</span>
                  <span className={`mono cap-status cap-${capabilityStatus(c).toLowerCase().replace(' ', '-')}`}>
                    {capabilityStatus(c)}
                  </span>
                </div>
                <div className="cap-where">{c.where}</div>
                {c.evidence && <div className="mono cap-evidence">{c.evidence}</div>}
              </li>
            ))}
          </ul>
        </Reveal>
      </div>
    </section>
  )
}

function DominoGlyph({ chain }) {
  if (!chain || chain.length === 0) return null
  const w = chain.length * 11 + 6
  return (
    <svg
      className="domino-glyph"
      viewBox={`0 0 ${w} 20`}
      width={w}
      height={20}
      aria-hidden="true"
    >
      {chain.map((m, i) => {
        const last = i === chain.length - 1
        return (
          <rect
            key={`${m}-${i}`}
            x={3 + i * 11}
            y={3}
            width={8}
            height={14}
            fill={last ? 'var(--red)' : 'var(--sheet)'}
            stroke={last ? 'var(--red)' : 'var(--ink)'}
            strokeWidth={1.5}
            transform={`rotate(${tipAngle(i)} ${7 + i * 11} 17)`}
          />
        )
      })}
    </svg>
  )
}

function PresetStatus({ preset }) {
  const h = preset.headlines
  if (!preset.has_recording) return <Chip tone="mute">NO RECORDING</Chip>
  const hasChain = (h?.chain?.length ?? 0) > 0
  if (hasChain && h.chain_match === true) return <Chip tone="ok">✓ CHAIN MATCH</Chip>
  if (hasChain && h.chain_match === false) return <Chip tone="flag">✗ CHAIN DIFFER</Chip>
  if (hasChain) return <Chip>{h.chain.length} HOPS</Chip>
  if (h?.suite) {
    return h.suite.new_failures > 0 ? (
      <Chip tone="flag">✗ {h.suite.new_failures} NEW FAILURES</Chip>
    ) : (
      <Chip tone="ok">✓ SUITE CLEAN</Chip>
    )
  }
  return null
}

/* One ticker item per preset: the first real break it caught. */
function tickerItems(presets) {
  const short = (sym) => String(sym || '').replace(/^numpy\./, 'np.')
  return (presets || [])
    .filter((p) => p.has_recording && p.headlines?.first_break)
    .map((p) => {
      const fb = p.headlines.first_break
      return {
        key: p.id,
        preset: p.id,
        pkg: p.decision_parsed ? `${p.decision_parsed.package} ${p.decision_parsed.version}` : p.decision,
        symbol: short(fb.symbol),
        site: `${String(fb.path).split('/').pop()}:${fb.line}`,
        repo: p.title,
      }
    })
}

function DrawingsOnFile({ presets, busy, onReplay }) {
  // The hero drawing leads; the rest keep API order (stable sort).
  const ordered = [...presets].sort((a, b) =>
    a.id === NIBABEL_PRESET ? -1 : b.id === NIBABEL_PRESET ? 1 : 0,
  )
  return (
    <section className="drawings">
      <Eyebrow n="06" label="DRAWINGS ON FILE" />
      <p className="drawings-note">
        Targets are pre-existing repos, analyzed by ChainReaction — not part of this build.{' '}
        <a href="#/features/replay">How replay works</a>
      </p>
      <div className="drawings-grid">
        {ordered.map((p, i) => {
          const h = p.headlines
          const hasChain = (h?.chain?.length ?? 0) > 0
          return (
            <Tilt key={p.id} className="drawing-tilt">
            <div className="sheet card drawing-card">
              <div className="mono sheet-no">DWG CR-{String(i + 1).padStart(3, '0')}</div>
              {p.has_recording && hasChain && h.chain_match && (
                <div className="drawing-stamp">
                  <MatchStamp small />
                </div>
              )}
              <h3>{p.title}</h3>
              <p className="mono drawing-decision">{p.decision}</p>
              {h && (
                <p className="drawing-stats">
                  {h.hard} HARD · {h.at_risk} AT RISK
                  {!hasChain && h.suite && (
                    <>
                      {' · '}
                      <span className="mono">{h.suite.new_failures} new test failures</span>
                    </>
                  )}
                </p>
              )}
              {hasChain && <DominoGlyph chain={h.chain} />}
              <p className="drawing-status">
                <PresetStatus preset={p} />
              </p>
              <Button kind="secondary" onClick={() => onReplay(p.id)} disabled={busy || !p.has_recording}>
                {p.has_recording ? 'Replay' : 'No recording'}
              </Button>
            </div>
            </Tilt>
          )
        })}
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="landing-footer">
      <TitleBlock
        cells={[
          ['BUILT', 'hackathon · Oct 27 – Nov 4, 2026'],
          [
            'GITHUB',
            <a key="gh" href={GITHUB_URL} target="_blank" rel="noreferrer">
              Samyantak/ChainReaction
            </a>,
          ],
          [
            'LIMITATIONS',
            <a key="lim" href={`${GITHUB_URL}#limitations`} target="_blank" rel="noreferrer">
              known limits
            </a>,
          ],
        ]}
      />
    </footer>
  )
}

export default function Landing() {
  const { data: presets, error } = useApi('/api/presets')
  const [busy, setBusy] = useState(false)
  const [replayError, setReplayError] = useState(null)
  const [theme, setTheme] = useTheme()
  const replay = (id) => startReplay(id, setBusy, setReplayError)
  const replayGraph = (id) => startReplay(id, setBusy, setReplayError, 'graph')
  const nibabel = presets?.find((p) => p.id === NIBABEL_PRESET)

  return (
    <>
      <LandingHeader
        onReplay={() => replay(NIBABEL_PRESET)}
        busy={busy}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        replayCount={countReplayable(presets)}
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
              <Reveal index={0}>
                <Hero
                  preset={nibabel}
                  busy={busy}
                  error={replayError}
                  onReplay={() => replay(NIBABEL_PRESET)}
                />
              </Reveal>
              <Ticker items={tickerItems(presets)} onPick={(it) => replay(it.preset)} />
              <Reveal index={1}>
                <ChainDominoes
                  headlines={nibabel?.headlines}
                  busy={busy}
                  onOpenGraph={() => replayGraph(NIBABEL_PRESET)}
                />
              </Reveal>
              <Reveal index={2}>
                <RunPreview
                  preset={nibabel}
                  busy={busy}
                  onReplay={() => replay(NIBABEL_PRESET)}
                />
              </Reveal>
              <Reveal index={3}>
                <SevenSheets nibabel={nibabel} />
              </Reveal>
              <Reveal index={4}>
                <Measurements nibabel={nibabel} />
              </Reveal>
            </div>
            <GroundingBand />
            <div className="landing">
              <Reveal index={5}>
                <DrawingsOnFile presets={presets} busy={busy} onReplay={replay} />
              </Reveal>
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
