import React, { useEffect, useRef } from 'react'

/* Shared live-log rendering (run shell + Setup view). Every event type
 * has a one-line summary; unknown types render as generic JSON rows. */

export function summarize(event, runId) {
  const d = event.data || {}
  switch (event.type) {
    case 'log':
      if (d.warning) return { tone: 'warn', text: `warning: ${d.warning}` }
      return { tone: 'mute', text: d.message || '' }
    case 'stage_start':
      return { tone: 'mute', text: `— ${d.stage} started —` }
    case 'stage_end':
      return { tone: 'ok', text: `— ${d.stage} done —` }
    case 'error':
      return { tone: 'flag', text: d.error || 'error' }
    case 'fetch': {
      // Replayed fetches carry no byte count; say where the page came from.
      const size = Number.isFinite(d.bytes) ? ` (${d.bytes.toLocaleString('en-US')} bytes)` : ''
      const from = d.replayed ? ' · from recording' : ''
      return { tone: 'mute', text: `GET ${d.url} → ${d.status ?? '—'}${size}${from}` }
    }
    case 'llm_call':
      return {
        tone: 'mute',
        text: `LLM ${d.model || 'model'} · ${(d.prompt_hash || '').slice(0, 8)}${d.cached ? ' · cached' : ''}`,
      }
    case 'cmd': {
      const cmd = Array.isArray(d.cmd) ? d.cmd.join(' ') : String(d.cmd || '')
      const short = cmd.length > 140 ? `${cmd.slice(0, 140)}…` : cmd
      const rc = d.timed_out ? 'timed out' : `exit ${d.returncode}`
      return { tone: d.returncode === 0 ? 'mute' : 'flag', text: `${d.cwd || ''}$ ${short} → ${rc}` }
    }
    case 'artifact':
      return {
        tone: 'mute',
        text: `artifact: ${d.name}`,
        href: `/api/runs/${runId}/artifacts/${d.name}`,
      }
    default:
      return { tone: 'mute', text: `${event.type}: ${JSON.stringify(d)}` }
  }
}

export function LogRows({ events, runId }) {
  const boxRef = useRef(null)
  useEffect(() => {
    const box = boxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [events.length])
  if (events.length === 0) return null
  return (
    <div className="log-box" ref={boxRef}>
      {events.map((e) => {
        const row = summarize(e, runId)
        return (
          <div className={`log-row tone-${row.tone}`} key={e.seq}>
            <span className="log-time">{(e.ts || '').slice(11, 19)}</span>
            <span className="log-text">
              {row.href ? <a href={row.href}>{row.text}</a> : row.text}
            </span>
          </div>
        )
      })}
    </div>
  )
}
