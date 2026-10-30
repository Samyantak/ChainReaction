import React, { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'

const COLORS = { HARD: '#e5484d', AT_RISK: '#f5a524', OK: '#8b8d98' }

async function getJSON(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.json()
}

export default function App() {
  const [scenarios, setScenarios] = useState([])
  const [scenario, setScenario] = useState('nibabel-numpy2')
  const [graph, setGraph] = useState(null)
  const [report, setReport] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const cyRef = useRef(null)
  const cy = useRef(null)

  useEffect(() => {
    getJSON('/api/scenarios').then(setScenarios).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    setError(null)
    setGraph(null)
    setReport(null)
    setSelected(null)
    Promise.all([
      getJSON(`/api/graph?scenario=${scenario}`),
      getJSON(`/api/report?scenario=${scenario}`),
    ])
      .then(([g, r]) => {
        setGraph(g)
        setReport(r)
      })
      .catch((e) => setError(String(e)))
  }, [scenario])

  useEffect(() => {
    if (!graph || !cyRef.current) return
    if (cy.current) cy.current.destroy()
    const spine = new Set(graph.spine || [])
    cy.current = cytoscape({
      container: cyRef.current,
      elements: [
        ...graph.nodes.map((n) => ({
          data: { id: n.id, status: n.status, spine: spine.has(n.id) },
        })),
        ...graph.edges.map((e, i) => ({
          data: {
            id: `e${i}`,
            source: e.source,
            target: e.target,
            kind: e.kind,
            file: e.file,
            line: e.line,
          },
        })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (el) => COLORS[el.data('status')] || COLORS.OK,
            label: 'data(id)',
            color: '#e8e8e8',
            'font-size': 9,
            'text-wrap': 'wrap',
            'text-max-width': 120,
          },
        },
        {
          selector: 'node[spine = 1]',
          style: { 'border-width': 3, 'border-color': '#ffffff' },
        },
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': '#555',
            'target-arrow-color': '#555',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
          },
        },
      ],
      layout: { name: 'cose', animate: false },
    })
    cy.current.on('tap', 'node', (e) => setSelected(e.target.id()))
    cy.current.on('tap', (e) => {
      if (e.target === cy.current) setSelected(null)
    })
    return () => cy.current && cy.current.destroy()
  }, [graph])

  const selectedEdges = selected
    ? (graph?.edges || []).filter(
        (e) => e.source === selected || e.target === selected
      )
    : []

  return (
    <div className="layout">
      <header>
        <h1>ChainReaction</h1>
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.decision} on {s.target}
            </option>
          ))}
        </select>
        <span className="target-note">
          Target repo: pre-existing, analyzed by ChainReaction
        </span>
      </header>
      {error && <div className="error">API error: {error}</div>}
      <main>
        <div className="canvas" ref={cyRef} />
        <aside className="panel">
          {!report && !error && <p>Computing pipeline…</p>}
          {report && (
            <>
              <section>
                <h2>Prediction</h2>
                <p>
                  HARD: {report.hard.length} modules | AT_RISK:{' '}
                  {report.at_risk.length} nodes
                </p>
                <p className="muted">
                  Change set: {report.change_symbols.join(', ')} | numpy:{' '}
                  {report.numpy_version || 'unknown'}
                </p>
              </section>
              <section>
                <h2>Scoreboard</h2>
                {report.layers.map((l) => (
                  <div key={l.name} className="layer">
                    <strong>
                      {l.name} — recall {Number(l.recall).toFixed(2)}
                    </strong>
                    <div className="chain">{l.actual.join(' → ') || '(import ok)'}</div>
                    {l.missing.length > 0 && (
                      <div className="missed">Missed: {l.missing.join(', ')}</div>
                    )}
                  </div>
                ))}
                <p className="muted">
                  Baselines: grep {report.grep_hits} hits | ruff NPY201{' '}
                  {report.ruff_hits ?? 'n/a'} hits
                </p>
              </section>
              <section>
                <h2>Evidence</h2>
                {report.spine_evidence.map(([u, v, file, line], i) => (
                  <div key={i} className="evidence">
                    {u} → {v} ({file}:{line})
                  </div>
                ))}
              </section>
              {selected && (
                <section>
                  <h2>{selected}</h2>
                  {selectedEdges.map((e, i) => (
                    <div key={i} className="evidence">
                      {e.source} → {e.target} [{e.kind}]{' '}
                      {e.file}:{e.line}
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </aside>
      </main>
    </div>
  )
}
