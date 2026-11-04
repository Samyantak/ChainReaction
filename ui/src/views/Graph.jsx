import React, { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'
import { getJSON, useApi, withOptional } from '../api'

cytoscape.use(dagre)
import { DotBreak, Status } from '../components'
import { CodeBlock, FileHead } from '../CodeBlock'
import { GRAPH_MAX_ZOOM, GRAPH_MIN_FIT_ZOOM, edgeState, neighborhood, nodeVisible, shortLabel, spineLayout } from '../graphUtils'
import '../graph.css'

const base = (file) => (file ? file.split('/').pop() : '')

/* ---------- Domino tiles: the main chain, break side first ---------- */

function DominoTiles({ tiles, selected, onSelect, onDone, round, onReplay }) {
  const [tipped, setTipped] = useState(0)
  const reduced = useMemo(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
    [],
  )
  useEffect(() => {
    setTipped(0)
    const timers = tiles.map((_, i) =>
      setTimeout(() => setTipped(i + 1), reduced ? i * 60 : 300 + i * 150),
    )
    const last = tiles.length - 1
    const doneTimer = setTimeout(
      () => onDone?.(),
      reduced ? last * 60 + 50 : 300 + last * 150 + 250,
    )
    return () => [...timers, doneTimer].forEach(clearTimeout)
  }, [tiles, round])
  if (tiles.length === 0) return null
  return (
    <div className="domino-block">
      <div className="domino-row">
        {tiles.map((t, i) => (
          <button
            key={t.module}
            type="button"
            aria-pressed={selected === t.module}
            onClick={() => onSelect(t.module)}
            className={[
              'domino',
              `role-${t.role}`,
              i < tipped ? 'tipped' : 'upright',
              reduced ? 'no-motion' : '',
              selected === t.module ? 'selected' : '',
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
      <button type="button" className="btn btn-secondary btn-small" onClick={onReplay}>
        Replay animation
      </button>
    </div>
  )
}

/* ---------- Cytoscape impacted subgraph ---------- */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

function CytoGraph({ nodes, edges, spine, chainOrder, breakId, filters, query, selected, onSelect, visible }) {
  const ref = useRef(null)
  const cyRef = useRef(null)
  const [themeTick, setThemeTick] = useState(0)
  const [tip, setTip] = useState(null)
  const dataKey = JSON.stringify([nodes.map((n) => n.id), edges.length])

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeTick((t) => t + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-presenter'] })
    return () => observer.disconnect()
  }, [])

  /* Frame what is visible at a readable zoom: the whole graph when it
   * fits at >= GRAPH_MIN_FIT_ZOOM, otherwise the main chain (the rest is
   * a pan away). Computed synchronously, then optionally animated to. */
  const fitVisible = (cy, animate = false) => {
    const shown = cy.nodes(':visible')
    if (shown.length === 0) return
    const from = { zoom: cy.zoom(), pan: { ...cy.pan() } }
    cy.fit(shown, 40)
    if (cy.zoom() < GRAPH_MIN_FIT_ZOOM) {
      const chain = cy.nodes('.chain:visible')
      cy.fit(chain.length ? chain : shown, 60)
    }
    const z = Math.min(GRAPH_MAX_ZOOM, Math.max(GRAPH_MIN_FIT_ZOOM, cy.zoom()))
    if (z !== cy.zoom()) {
      const chain = cy.nodes('.chain:visible')
      cy.zoom(z)
      cy.center(chain.length && z === GRAPH_MIN_FIT_ZOOM ? chain : shown)
    }
    // No chain and still taller than the canvas at the zoom floor (anndata's
    // 17 AT RISK nodes): align to the top so the first node is not cut off.
    if (cy.nodes('.chain:visible').length === 0) {
      const bb = shown.renderedBoundingBox()
      if (bb.h > cy.height() - 40) cy.panBy({ x: 0, y: 24 - bb.y1 })
    }
    if (animate && !reducedMotion()) {
      const to = { zoom: cy.zoom(), pan: { ...cy.pan() } }
      cy.zoom(from.zoom)
      cy.pan(from.pan)
      cy.animate({ zoom: to.zoom, pan: to.pan, duration: 300, easing: 'ease-out' })
    }
  }

  useEffect(() => {
    if (!ref.current) return undefined
    if (cyRef.current) cyRef.current.destroy()
    const chainPairs = new Set()
    for (let i = 0; i + 1 < spine.length; i += 1) {
      chainPairs.add(`${spine[i]}|${spine[i + 1]}`)
      chainPairs.add(`${spine[i + 1]}|${spine[i]}`)
    }
    const ink = cssVar('--ink') || '#102536'
    const sheet = cssVar('--sheet') || '#fff'
    const red = cssVar('--red') || '#d9481c'
    const presenter = document.documentElement.dataset.presenter === 'on'
    const fs = presenter ? 15 : 12
    // Chain graphs use the spine-and-ribs layout; chainless ones dagre.
    // Column spacing follows the longest label so neighbors never touch.
    const longest = Math.max(10, ...nodes.map((n) => shortLabel(n.id).length))
    const colGap = Math.min(360, Math.max(200, longest * (fs * 0.62) + 56))
    const spinePos = spineLayout(nodes, edges, chainOrder, { colGap, rowGap: fs * 4.2 })
    const cy = cytoscape({
      container: ref.current,
      maxZoom: 2.2,
      minZoom: 0.2,
      wheelSensitivity: 0.25,
      boxSelectionEnabled: false,
      elements: [
        ...nodes.map((n) => ({
          data: {
            id: n.id,
            label: `${shortLabel(n.id)}\n${n.status === 'HARD' ? 'HARD' : 'AT RISK'}`,
            status: n.status,
            behavior: n.behavior ? 1 : 0,
            file: n.file || '',
          },
          classes: [
            n.chain && 'chain',
            n.behavior && 'behavior',
            n.status === 'HARD' ? 'hard' : 'risk',
            n.id === breakId && 'break',
            spine.length && n.id === spine[0] && 'root',
          ]
            .filter(Boolean)
            .join(' '),
        })),
        ...edges.map((e, i) => ({
          data: { id: `e${i}`, source: e.source, target: e.target },
          classes: chainPairs.has(`${e.source}|${e.target}`) ? 'chain-edge' : 'plain-edge',
        })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            shape: 'rectangle',
            width: 'label',
            height: 'label',
            padding: '9px',
            'background-color': sheet,
            'border-width': 1.5,
            'border-color': ink,
            label: 'data(label)',
            color: ink,
            'font-family': '"Martian Mono", ui-monospace, monospace',
            'font-size': fs,
            'line-height': 1.35,
            'text-wrap': 'wrap',
            'text-valign': 'center',
            'text-halign': 'center',
            'transition-property': 'opacity, border-width, background-color',
            'transition-duration': '180ms',
          },
        },
        { selector: 'node.hard', style: { 'border-color': red, 'border-width': 2 } },
        { selector: 'node.risk', style: { 'border-style': 'dashed' } },
        { selector: 'node.behavior', style: { 'border-style': 'dotted', 'border-color': cssVar('--sky-ink') } },
        {
          selector: 'node.chain',
          style: { 'background-color': cssVar('--sky'), 'border-color': ink, 'border-width': 2.5, 'font-weight': 600 },
        },
        { selector: 'node.root', style: { 'background-color': ink, color: sheet, 'border-color': ink } },
        { selector: 'node.break', style: { 'background-color': red, color: '#fff', 'border-color': red } },
        { selector: 'node.hovered', style: { 'border-width': 3.5 } },
        { selector: 'node:selected', style: { 'border-width': 4, 'border-color': cssVar('--sky-ink') } },
        { selector: 'node.faded', style: { opacity: 0.22 } },
        {
          selector: 'edge',
          style: {
            width: 1.2,
            'curve-style': 'bezier',
            'line-color': cssVar('--ink-muted'),
            'target-arrow-color': cssVar('--ink-muted'),
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.8,
            // Hundreds of edges read as hatching: fainter when dense.
            opacity: edges.length > 300 ? 0.07 : 0.22,
            'transition-property': 'opacity, width',
            'transition-duration': '180ms',
          },
        },
        { selector: 'edge.lit', style: { opacity: 0.95, width: 2, 'line-color': cssVar('--sky-ink'), 'target-arrow-color': cssVar('--sky-ink') } },
        { selector: 'edge.dim', style: { opacity: 0.05 } },
        {
          selector: 'edge.chain-edge',
          style: {
            width: 3,
            opacity: 1,
            'line-color': red,
            'target-arrow-color': red,
            'arrow-scale': 1,
            'line-style': 'dashed',
            'line-dash-pattern': [12, 5],
          },
        },
      ],
      layout: spinePos
        ? { name: 'preset', positions: (n) => spinePos[n.id()] || { x: 0, y: 0 }, animate: false }
        : {
        name: 'dagre',
        // Import edges point importer -> imported. Right-to-left ranks
        // put the package root on the right and the breaking module on
        // the left: the same order as the domino tiles above.
        rankDir: 'RL',
        ranker: 'network-simplex',
        nodeSep: 14,
        rankSep: 48,
        edgeSep: 6,
        animate: false,
      },
    })
    cy.ready(() => fitVisible(cy))
    // Labels size the nodes: refit once the web fonts are in.
    document.fonts?.ready?.then(() => {
      if (!cy.destroyed()) fitVisible(cy)
    })
    cy.one('layoutstop', () => fitVisible(cy))
    cy.on('tap', 'node', (e) => onSelect(e.target.id()))
    cy.on('tap', (e) => {
      if (e.target === cy) onSelect(null)
    })
    cy.on('mouseover', 'node', (e) => {
      const n = e.target
      n.addClass('hovered')
      const p = n.renderedPosition()
      setTip({ x: p.x, y: p.y - n.renderedOuterHeight() / 2 - 8, id: n.id(), status: n.data('status'), file: n.data('file') })
    })
    cy.on('mouseout', 'node', (e) => {
      e.target.removeClass('hovered')
      setTip(null)
    })
    cy.on('pan zoom', () => setTip(null))
    // Marching dashes along the chain: the failure flowing from the
    // breaking line to the package root. Paused under reduced motion.
    let raf = 0
    if (!reducedMotion()) {
      const chainEdges = cy.edges('.chain-edge')
      let last = 0
      let offset = 0
      const step = (t) => {
        if (t - last > 40) {
          offset = (offset + 1) % 34
          chainEdges.style('line-dash-offset', offset)
          last = t
        }
        raf = requestAnimationFrame(step)
      }
      if (chainEdges.length) raf = requestAnimationFrame(step)
    }
    cyRef.current = cy
    return () => {
      cancelAnimationFrame(raf)
      cy.destroy()
    }
  }, [dataKey, themeTick, breakId])

  // Filters, search, and selection: visibility + neighborhood focus.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const near = neighborhood(edges, selected)
    cy.batch(() => {
      cy.nodes().forEach((node) => {
        const shown = nodeVisible(
          { status: node.data('status'), behavior: node.data('behavior'), id: node.id() },
          filters,
          query,
        )
        node.style('display', shown ? 'element' : 'none')
        node.toggleClass('faded', !!selected && !near.has(node.id()))
        if (node.id() === selected) node.select()
        else node.unselect()
      })
      cy.edges().forEach((edge) => {
        const endsVisible = edge.source().style('display') !== 'none' && edge.target().style('display') !== 'none'
        const state = edgeState({
          isChain: edge.hasClass('chain-edge'),
          endsVisible,
          selected,
          sourceId: edge.source().id(),
          targetId: edge.target().id(),
        })
        edge.style('display', state === 'hidden' ? 'none' : 'element')
        edge.toggleClass('lit', state === 'lit')
        edge.toggleClass('dim', state === 'dim')
      })
    })
    if (selected) {
      const node = cy.getElementById(selected)
      if (node.nonempty() && node.visible()) {
        // Center the module in the part of the canvas the evidence
        // drawer leaves visible (the drawer covers the right side).
        const z = Math.max(cy.zoom(), 0.9)
        const drawer = Math.min(400, cy.width() * 0.92)
        const visibleW = cy.width() > 760 ? cy.width() - drawer : cy.width()
        const p = node.position()
        const pan = { x: visibleW / 2 - p.x * z, y: cy.height() / 2 - p.y * z }
        if (reducedMotion()) {
          cy.zoom(z)
          cy.pan(pan)
        } else {
          cy.animate({ zoom: z, pan, duration: 320, easing: 'ease-out' })
        }
      }
    }
  }, [filters, query, selected, dataKey])

  // Refit whenever filters or search change what is visible (not on
  // mount: the layout hooks already framed the first view).
  const firstFilter = useRef(true)
  useEffect(() => {
    if (firstFilter.current) {
      firstFilter.current = false
      return
    }
    const cy = cyRef.current
    if (cy && !selected) fitVisible(cy, true)
  }, [filters, query])

  const zoomBy = (f) => {
    const cy = cyRef.current
    if (!cy) return
    const z = Math.min(2.2, Math.max(0.2, cy.zoom() * f))
    cy.zoom({ level: z, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } })
  }
  const frameChain = () => {
    const cy = cyRef.current
    if (!cy) return
    const chain = cy.nodes('.chain:visible')
    if (chain.length) cy.animate({ fit: { eles: chain, padding: 60 }, duration: reducedMotion() ? 0 : 300 })
  }

  return (
    <div className="cyto-wrap" style={{ opacity: visible ? 1 : 0 }}>
      <div ref={ref} className="cyto" />
      <div className="cyto-controls" role="group" aria-label="Graph view controls">
        <button type="button" onClick={() => zoomBy(1.25)} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => zoomBy(0.8)} aria-label="Zoom out">−</button>
        <button type="button" onClick={() => cyRef.current && fitVisible(cyRef.current, true)}>Fit</button>
        {spine.length > 0 && (
          <button type="button" onClick={frameChain}>
            Chain
          </button>
        )}
      </div>
      <div className="cyto-legend mono" aria-hidden="true">
        <span><i className="lg lg-break" /> first break</span>
        <span><i className="lg lg-chain" /> on chain</span>
        <span><i className="lg lg-root" /> package root</span>
        <span><i className="lg lg-edge" /> import</span>
      </div>
      {tip && (
        <div className="cyto-tip" style={{ left: tip.x, top: tip.y }}>
          <div className="mono tip-id">{tip.id}</div>
          <div className="mono tip-meta">
            {tip.status === 'HARD' ? 'HARD · fails at import' : 'AT RISK · breaks when called'}
            {tip.file ? ` · ${tip.file}` : ''}
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------- Side panel ---------- */

function SidePanel({ runId, module, tiles, nodes, changeset, onClose }) {
  const tile = tiles.find((t) => t.module === module)
  const node = nodes.find((n) => n.id === module)
  if (!tile && !node) return null
  const item = tile?.symbol ? changeset?.items?.find((i) => i.symbol === tile.symbol) : null
  const tileIndex = tile ? tiles.indexOf(tile) : -1
  return (
    <div className="graph-panel" role="complementary" aria-label="Module evidence">
      <button type="button" className="panel-close" onClick={onClose} aria-label="Close evidence panel">×</button>
      <h3 className="mono panel-module">
        <DotBreak text={module} />
      </h3>
      {node && (
        <p>
          <Status tone={node.status === 'HARD' ? 'flag' : 'warn'} text={node.status} />
          {node.chain ? ' · on the main chain' : ''}
        </p>
      )}
      {tile && tileIndex >= 0 && (
        <div className="panel-chain">
          <h4>Chain to the root</h4>
          {tiles.slice(tileIndex).map((t) => (
            <div className="mono chain-hop" key={t.module}>
              <DotBreak text={t.module} />{' '}
              <span className="chain-site">{t.file ? `${t.file}:${t.line}` : 'package root'}</span>
            </div>
          ))}
        </div>
      )}
      {tile?.file && (
        <CodeBlock
          runId={runId}
          path={tile.file}
          center={tile.line}
          radius={20}
          breakLine={tile.role === 'decision' ? tile.line : null}
          caption={tile.role === 'decision' ? null : 'import site'}
        />
      )}
      {!tile?.file && node?.file && <FileHead runId={runId} path={node.file} />}
      {node?.tests && (
        <div className="panel-tests">
          <h4>Covering tests</h4>
          {node.tests.map((t) => (
            <div className="mono panel-test" key={t}>
              {t}
            </div>
          ))}
          {node.tests_more > 0 && <div className="mono panel-test">+{node.tests_more} more</div>}
        </div>
      )}
      {item && (
        <div className="panel-change">
          <h4>Change item</h4>
          <p className="mono">
            {item.symbol} · {item.kind}
          </p>
          {item.quote && <p className="panel-quote">“{item.quote}”</p>}
          {item.replacement && <p>→ {item.replacement}</p>}
          {changeset?.source_url && (
            <p>
              <a href={changeset.source_url} target="_blank" rel="noreferrer">
                migration guide
              </a>
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------- View ---------- */

export default function GraphView({ runId }) {
  const { data, error } = useApi(`/api/runs/${runId}/graph`)
  const { data: changeset } = useApi(withOptional(`/api/runs/${runId}/artifacts/changeset.json`))
  const [full, setFull] = useState(null)
  const [expanding, setExpanding] = useState(false)
  const [selected, setSelected] = useState(null)
  const [filters, setFilters] = useState({ HARD: true, AT_RISK: true, BEHAVIOR: true })
  const [query, setQuery] = useState('')
  const [animDone, setAnimDone] = useState(false)
  const [round, setRound] = useState(0)
  const shown = full ?? data

  // R replays the domino animation. Ignored while typing.
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target?.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'Escape') {
        setSelected(null)
        return
      }
      if (e.key !== 'r' && e.key !== 'R') return
      e.preventDefault()
      setRound((r) => r + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (error) {
    return (
      <div className="run-view">
        <h2>Prediction graph</h2>
        <div className="view-error">
          Graph not ready yet — the prediction stage hasn&apos;t run, or the target checkout is
          missing ({error}).
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="run-view">
        <h2>Prediction graph</h2>
        <p>Loading graph…</p>
      </div>
    )
  }
  const expand = async () => {
    if (full || expanding) return
    setExpanding(true)
    try {
      setFull(await getJSON(`/api/runs/${runId}/graph?full=1`))
    } catch {
      setExpanding(false)
    }
  }
  return (
    <div className="run-view graph-view">
      <h2>Prediction graph</h2>
      {data.tiles.length === 0 && (
        <p className="view-empty">
          No import chain — nothing breaks at import. {data.counts.at_risk} items at risk below.
        </p>
      )}
      <DominoTiles
        tiles={data.tiles}
        selected={selected}
        onSelect={setSelected}
        onDone={() => setAnimDone(true)}
        round={round}
        onReplay={() => setRound((r) => r + 1)}
      />
      <div className="graph-toolbar">
        <fieldset className="legend">
          <legend>Show</legend>
          <label className="legend-item">
            <input
              type="checkbox"
              checked={filters.HARD}
              onChange={(e) => setFilters((f) => ({ ...f, HARD: e.target.checked }))}
            />
            <span className="swatch hard" /> HARD
          </label>
          <label className="legend-item">
            <input
              type="checkbox"
              checked={filters.AT_RISK}
              onChange={(e) => setFilters((f) => ({ ...f, AT_RISK: e.target.checked }))}
            />
            <span className="swatch risk" /> AT RISK
          </label>
          <label
            className="legend-item"
            title="Modules in upstream behavior-change files (not predicted breakage)"
          >
            <input
              type="checkbox"
              checked={filters.BEHAVIOR}
              onChange={(e) => setFilters((f) => ({ ...f, BEHAVIOR: e.target.checked }))}
            />
            <span className="swatch behavior" /> behavior
          </label>
          <span className="legend-item">
            <span className="swatch chain" /> on chain
          </span>
        </fieldset>
        <input
          className="graph-search"
          type="search"
          placeholder="module search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="graph-counts">
        <span className="mono">
          showing {shown.counts.shown} of {shown.counts.hard + shown.counts.at_risk} modules
          {shown.edges ? ` · ${shown.edges.length} import edges` : ''}
        </span>
        {shown.truncated && !full &&
          shown.clusters.map((c) => (
            <button key={c.id} type="button" className="cluster-btn mono" onClick={expand} disabled={expanding}>
              {c.label}
            </button>
          ))}
        {shown.truncated && !full && (
          <span className="muted">{expanding ? 'loading the full graph…' : '— click to show every module'}</span>
        )}
        <span className="muted graph-hint">scroll to zoom · drag to pan · click a module for its evidence · Esc clears</span>
      </div>
      <div className={`graph-split${selected ? ' panel-open' : ''}`}>
        <CytoGraph
          nodes={shown.nodes}
          edges={shown.edges}
          spine={shown.spine}
          chainOrder={data.tiles.map((t) => t.module)}
          breakId={data.tiles[0]?.module || null}
          filters={filters}
          query={query}
          selected={selected}
          onSelect={setSelected}
          visible={animDone || data.tiles.length === 0}
        />
        <SidePanel
          runId={runId}
          module={selected}
          tiles={data.tiles}
          nodes={shown.nodes}
          changeset={changeset}
          onClose={() => setSelected(null)}
        />
      </div>
    </div>
  )
}
