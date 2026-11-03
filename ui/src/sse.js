/* Minimal SSE client over fetch (not EventSource).
 *
 * The server sends NAMED events (`event: stage_end`), which never fire
 * EventSource.onmessage — and EventSource has no wildcard, so unknown
 * future types would be undeliverable. This reader parses every event
 * frame itself (§12: unknown types render as generic rows) and resumes
 * with ?from=<last id>, which the server supports alongside
 * Last-Event-ID. Pure functions stay Node-importable for testing.
 */

export function createParser() {
  return { buffer: '', lastId: 0, retry: 1000 }
}

/* Feed a text chunk; returns { events, ended }. Each event is
 * { id, name, data } with data still a raw string. */
export function parseChunk(state, chunk) {
  const events = []
  state.buffer += chunk
  let boundary = state.buffer.search(/\r?\n\r?\n/)
  while (boundary !== -1) {
    const frame = state.buffer.slice(0, boundary)
    state.buffer = state.buffer.slice(boundary).replace(/^\r?\n\r?\n/, '')
    let id = null
    let name = 'message'
    const dataLines = []
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith(':') || line === '') continue // ping / padding
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''))
      } else if (line.startsWith('event:')) {
        name = line.slice(6).trim()
      } else if (line.startsWith('id:')) {
        id = Number(line.slice(3).trim())
      } else if (line.startsWith('retry:')) {
        const ms = Number(line.slice(6).trim())
        if (Number.isFinite(ms)) state.retry = ms
      }
    }
    if (id !== null && Number.isFinite(id)) state.lastId = id
    if (dataLines.length > 0) events.push({ id: state.lastId, name, data: dataLines.join('\n') })
    boundary = state.buffer.search(/\r?\n\r?\n/)
  }
  return { events }
}

/* Stream url to onEvent({id, name, data}). Always fetches the full
 * history first (?from=0), then reconnects with ?from=<last id> until
 * shouldStop() is true or the signal aborts — so a finished run
 * opened by deep link still builds its state from the event history.
 * onState('live' | 'connecting' | 'closed') reports connection state.
 * Resolves when stopped. */
export async function streamEvents(url, { signal, onEvent, onState, shouldStop }) {
  const state = createParser()
  onState?.('connecting')
  for (;;) {
    if (signal?.aborted) return
    try {
      const sep = url.includes('?') ? '&' : '?'
      const res = await fetch(`${url}${sep}from=${state.lastId}`, { signal })
      if (res.status === 404 || res.status === 400) {
        onState?.('closed')
        return
      }
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      onState?.('live')
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        const { events } = parseChunk(state, decoder.decode(value, { stream: true }))
        for (const e of events) onEvent?.(e)
        if (signal?.aborted) return
      }
    } catch {
      if (signal?.aborted) return
    }
    // The stream ended (server closes idle/terminal streams): stop
    // only once the snapshot confirms terminal, else back off and
    // resume. The pause also keeps a lagging snapshot from hot-loop.
    if (await shouldStop?.()) {
      onState?.('closed')
      return
    }
    onState?.('connecting')
    await new Promise((r) => setTimeout(r, state.retry))
  }
}
