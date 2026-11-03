import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON, postJSON } from './api'

export const STAGES = ['setup', 'interpret', 'investigate', 'predict', 'verify', 'fix', 'report']
export const STAGE_VIEW = {
  setup: 'setup',
  interpret: 'interpret',
  investigate: 'investigate',
  predict: 'graph',
  verify: 'verify',
  fix: 'fix',
  report: 'report',
}
export const VIEW_STAGE = Object.fromEntries(
  Object.entries(STAGE_VIEW).map(([stage, view]) => [view, stage]),
)
const TERMINAL = ['done', 'failed', 'cancelled']

/* Live run state: snapshot (manifest + status + report) plus the SSE
 * event stream. EventSource resumes with Last-Event-ID natively; we
 * stop it once the snapshot is terminal. */
export function useRun(runId) {
  const [snapshot, setSnapshot] = useState(null)
  const [snapError, setSnapError] = useState(null)
  const [events, setEvents] = useState([])
  const [stream, setStream] = useState('connecting') // live | connecting | closed
  const maxSeq = useRef(0)
  const sourceRef = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const snap = await getJSON(`/api/runs/${runId}`)
      setSnapshot(snap)
      setSnapError(null)
      return snap
    } catch (e) {
      setSnapError(String(e.message || e))
      return null
    }
  }, [runId])

  useEffect(() => {
    setSnapshot(null)
    setSnapError(null)
    setEvents([])
    maxSeq.current = 0
    refresh()
  }, [runId, refresh])

  useEffect(() => {
    if (!runId || snapError) return undefined
    if (snapshot && TERMINAL.includes(snapshot.status)) {
      setStream('closed')
      return undefined
    }
    setStream('connecting')
    const source = new EventSource(`/api/runs/${runId}/events`)
    sourceRef.current = source
    source.onopen = () => setStream('live')
    source.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data)
        if (event.seq > maxSeq.current) {
          maxSeq.current = event.seq
          setEvents((prev) => [...prev, event])
        }
      } catch {
        /* a malformed line never kills the stream */
      }
    }
    source.onerror = () => {
      setStream('connecting')
      // The server closes the stream at terminal status; stop
      // reconnecting once the snapshot confirms it.
      refresh().then((snap) => {
        if (snap && TERMINAL.includes(snap.status)) {
          source.close()
          setStream('closed')
        }
      })
    }
    return () => source.close()
  }, [runId, snapError, snapshot?.status, refresh])

  // Refetch the snapshot as stages complete (report appears at the end).
  const ends = events.filter((e) => e.type === 'stage_end').length
  useEffect(() => {
    if (ends > 0) refresh()
  }, [ends, refresh])

  const cancel = useCallback(async () => {
    await postJSON(`/api/runs/${runId}/cancel`, {})
    refresh()
  }, [runId, refresh])

  return { snapshot, snapError, events, stream, refresh, cancel }
}

export function stageStates(stages, events, snapshot) {
  const states = {}
  stages.forEach((stage) => {
    states[stage] = { state: 'pending' }
  })
  events.forEach((event) => {
    if (event.type === 'stage_start' && states[event.data.stage]) {
      states[event.data.stage] = { state: 'running', startedAt: event.ts }
    } else if (event.type === 'stage_end' && states[event.data.stage]) {
      states[event.data.stage] = {
        state: 'done',
        startedAt: states[event.data.stage].startedAt,
        endedAt: event.ts,
      }
    } else if (event.type === 'error' && event.data.stage && states[event.data.stage]) {
      states[event.data.stage] = { state: 'failed', error: event.data.error }
    }
  })
  if (snapshot && TERMINAL.includes(snapshot.status)) {
    stages.forEach((stage) => {
      if (states[stage].state === 'pending') {
        states[stage] = { state: 'skipped', reason: `run ${snapshot.status}` }
      } else if (states[stage].state === 'running') {
        states[stage] = { ...states[stage], state: snapshot.status === 'done' ? 'done' : snapshot.status }
      }
    })
  }
  return states
}
