import React, { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'
import { getJSON, useApi, withOptional } from '../api'

cytoscape.use(dagre)
import { Status } from '../components'
import { CodeBlock, FileHead } from '../CodeBlock'
import { GRAPH_MAX_ZOOM, GRAPH_MIN_FIT_ZOOM, describeEdge, nodeVisible } from '../graphUtils'
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
            <span className="domino-top mono">{t.module}</span>
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

function CytoGraph({ nodes, edges, clusters, spine, filters, query, selected, onSelect, onExpand, visible }) {
  const ref = useRef(null)
  const cyRef = useRef(null)
  const [themeTick, setThemeTick] = useState(0)
  const dataKey = JSON.stringify([nodes.map((n) => n.id), edges.length, clusters.map((c) => c.id)])

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeTick((t) => t + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!ref.current) return undefined
    if (cyRef.current) cyRef.current.destroy()
    const chainPairs = new Set()
    for (let i = 0; i + 1 < spine.length; i += 1) {
      chainPairs.add(`${spine[i]}|${spine[i + 1]}`)
      chainPairs.add(`${spine[i + 1]}|${spine[i]}`)
    }
    const short = (id) => id.split('.').slice(-2).join('.')
    const cy = cytoscape({
      container: ref.current,
      // Small graphs (2-3 nodes) would otherwise fit at 3x with giant
      // overlapping labels; the cap keeps every label readable.
      maxZoom: GRAPH_MAX_ZOOM,
      elements: [
        ...nodes.map((n) => ({
          data: {
            id: n.id,
            label: `${short(n.id)}\n${n.status === 'HARD' ? 'HARD' : 'AT RISK'}`,
            status: n.status,
            chain: n.chain ? 1 : 0,
            behavior: n.behavior ? 1 : 0,
          },
          classes: [n.chain && 'chain', n.behavior && 'behavior'].filter(Boolean).join(' '),
        })),
        ...clusters.map((c) => ({
          data: { id: c.id, label: c.label, cluster: 1 },
          classes: 'cluster',
        })),
        ...edges.map((e, i) => ({
          data: { id: `e${i}`, source: e.source, target: e.target },
          classes: chainPairs.has(`${e.source}|${e.target}`) ? 'chain-edge' : '',
        })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            shape: 'rectangle',
            'background-color': cssVar('--sheet') || '#fff',
            'border-width': 1.5,
            'border-color': cssVar('--ink') || '#102536',
            label: 'data(label)',
            color: cssVar('--ink') || '#102536',
            'font-family': '"Martian Mono", monospace',
            'font-size': 13,
            'text-wrap': 'wrap',
            'text-max-width': 180,
            padding: '8px',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'text-margin-y': 8,
          },
        },
        {
          selector: 'node.chain',
          style: {
            'background-color': cssVar('--sky'),
            'border-width': 3,
            'font-size': 14,
            'font-weight': 600,
            padding: '12px',
            'text-max-width': 220,
          },
        },
        {
          selector: 'node.cluster',
          style: {
            'background-color': cssVar('--paper'),
            'border-style': 'dashed',
            'border-width': 2,
            'font-size': 12,
          },
        },
        {
          selector: 'node.behavior',
          style: {
            'border-style': 'dotted',
            'border-color': cssVar('--sky-ink'),
          },
        },
        {
          selector: 'node:selected',
          style: { 'background-color': cssVar('--sky'), 'border-width': 3 },
        },
        {
          selector: 'edge',
          style: {
            width: 1,
            opacity: 0.25,
            'line-color': cssVar('--ink-muted'),
            'target-arrow-color': cssVar('--ink-muted'),
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
          },
        },
        {
          selector: 'edge.chain-edge',
          style: {
            width: 2.5,
            opacity: 1,
            'line-color': cssVar('--red'),
            'target-arrow-color': cssVar('--red'),
          },
        },
      ],
      layout: {
        name: 'dagre',
        // Import edges point importer -> imported, so bottom-to-top
        // ranks nodes by distance from the breaking line: the break
        // sits on top, the package root at the bottom.
        rankDir: 'BT',
        ranker: 'longest-path',
        nodeSep: 40,
        rankSep: 80,
        animate: false,
      },
    })
    // Fit the whole visible graph once positions are final. Both
    // hooks stay: whichever fires last wins, so a sync layout that
    // finishes before ready still ends fitted to the viewport.
    // Whole-fit can shrink labels below reading size, so never zoom
    // out past 0.75 (chain labels still render ~10px) — instead fit
    // the chain itself so it lands fully inside the canvas at a
    // readable zoom, and the rest is a pan away.
    const fitVisible = () => {
      const shown = cy.elements(':visible')
      if (shown.length > 0) cy.fit(shown, 30)
      if (cy.zoom() > GRAPH_MAX_ZOOM) {
        cy.zoom(GRAPH_MAX_ZOOM)
        if (shown.length > 0) cy.center(shown)
      }
      if (cy.zoom() < GRAPH_MIN_FIT_ZOOM) {
        const chain = cy.nodes('.chain:visible')
        if (chain.length > 0) {
          cy.fit(chain, 30)
          if (cy.zoom() < GRAPH_MIN_FIT_ZOOM) {
            cy.zoom(GRAPH_MIN_FIT_ZOOM)
            cy.center(chain)
          }
        } else {
          cy.zoom(GRAPH_MIN_FIT_ZOOM)
          cy.center()
        }
      }
    }
    cy.ready(fitVisible)
    cy.one('layoutstop', fitVisible)
    cy.on('tap', 'node', (e) => {
      const node = e.target
      if (node.data('cluster')) onExpand()
      else onSelect(node.id())
    })
    cy.on('tap', (e) => {
      if (e.target === cy) onSelect(null)
    })
    cyRef.current = cy
    return () => cy.destroy()
  }, [dataKey, themeTick])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const q = query.trim().toLowerCase()
    cy.nodes().forEach((node) => {
      if (node.data('cluster')) {
        node.style('display', q ? 'none' : 'element')
        return
      }
      const shown = nodeVisible(
        { status: node.data('status'), behavior: node.data('behavior'), id: node.id() },
        filters,
        query,
      )
      node.style('display', shown ? 'element' : 'none')
      if (node.id() === selected) node.select()
      else node.unselect()
    })
    // Chain edges always show; the rest appear only around the selected
    // node, so dense graphs read as chain-first instead of clutter.
    // Edges to filtered-out endpoints stay hidden (no dangling lines).
    const endsVisible = (edge) =>
      edge.source().style('display') !== 'none' && edge.target().style('display') !== 'none'
    cy.edges().forEach((edge) => {
      const [display, lit] = describeEdge({
        isChain: edge.hasClass('chain-edge'),
        endsVisible: endsVisible(edge),
        selected,
        sourceId: edge.source().id(),
        targetId: edge.target().id(),
      })
      edge.style('display', display)
      if (lit) edge.style('opacity', 0.9)
    })
  }, [filters, query, selected, dataKey])

  return <div ref={ref} className="cyto" style={{ opacity: visible ? 1 : 0 }} />
}

/* ---------- Side panel ---------- */

function SidePanel({ runId, module, tiles, nodes, changeset }) {
  const tile = tiles.find((t) => t.module === module)
  const node = nodes.find((n) => n.id === module)
  if (!tile && !node) {
    return (
      <div className="graph-panel">
        <p className="view-empty">Select a tile or a node for its evidence.</p>
      </div>
    )
  }
  const item = tile?.symbol ? changeset?.items?.find((i) => i.symbol === tile.symbol) : null
  const tileIndex = tile ? tiles.indexOf(tile) : -1
  return (
    <div className="graph-panel">
      <h3 className="mono panel-module">{module}</h3>
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
              {t.module} <span className="chain-site">{t.file ? `${t.file}:${t.line}` : 'package root'}</span>
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
      <p className="mono graph-counts">
        showing {shown.counts.shown} of {shown.counts.hard + shown.counts.at_risk}
        {shown.truncated && !full && (
          <>
            {' · '}
            {shown.clusters.map((c) => c.label).join(' · ')} — click a cluster to expand
            {expanding ? '…' : ''}
          </>
        )}
      </p>
      <div className="graph-split">
        <CytoGraph
          nodes={shown.nodes}
          edges={shown.edges}
          clusters={shown.clusters}
          spine={shown.spine}
          filters={filters}
          query={query}
          selected={selected}
          onSelect={setSelected}
          onExpand={expand}
          visible={animDone || data.tiles.length === 0}
        />
        <SidePanel runId={runId} module={selected} tiles={data.tiles} nodes={shown.nodes} changeset={changeset} />
      </div>
    </div>
  )
}
