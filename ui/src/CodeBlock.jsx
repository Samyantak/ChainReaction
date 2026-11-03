import React from 'react'
import { useApi } from './api'
import { RedCircle } from './components'

/* CodeBlock: a file window with an optional breaking line (accent text
 * + red-pencil circle + gutter dot). Fetches its own lines. */
export function CodeBlock({
  runId,
  path,
  center,
  radius = 20,
  breakLine = null,
  caption = null,
  lines = null,
}) {
  const start = Math.max(1, center - radius)
  const end = center + radius
  const { data: src } = useApi(
    path && !lines
      ? `/api/runs/${runId}/source?path=${encodeURIComponent(path)}&start=${start}&end=${end}`
      : null,
  )
  const rows = lines ?? src?.lines ?? []
  return (
    <div className="sheet code-sheet">
      <div className="code-file mono">{path}</div>
      <div className="code-lines">
        {rows.map((l) => {
          const isBreak = breakLine != null && l.no === breakLine
          return (
            <div className={`code-line${isBreak ? ' break' : ''}`} key={l.no}>
              <span className="code-gutter" aria-hidden="true">
                {isBreak ? '●' : ''}
              </span>
              <span className="code-num">{l.no}</span>
              <span className="code-text">
                {isBreak ? <RedCircle>{l.text || ' '}</RedCircle> : l.text || ' '}
              </span>
            </div>
          )
        })}
        {rows.length === 0 && <div className="code-loading">Loading source…</div>}
      </div>
      {caption && <div className="code-caption">{caption}</div>}
    </div>
  )
}

/* FileHead: first lines of a module with no precise site (labeled). */
export function FileHead({ runId, path, lines = 40 }) {
  const { data: src } = useApi(
    path ? `/api/runs/${runId}/source?path=${encodeURIComponent(path)}&start=1&end=${lines}` : null,
  )
  return (
    <div className="sheet code-sheet">
      <div className="code-file mono">{path}</div>
      <div className="code-lines">
        {(src?.lines ?? []).map((l) => (
          <div className="code-line" key={l.no}>
            <span className="code-gutter" aria-hidden="true" />
            <span className="code-num">{l.no}</span>
            <span className="code-text">{l.text || ' '}</span>
          </div>
        ))}
        {!src && <div className="code-loading">Loading source…</div>}
      </div>
      <div className="code-caption">top of file</div>
    </div>
  )
}
