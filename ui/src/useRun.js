import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON, postJSON } from './api'
import { streamEvents } from './sse'

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
    const controller = new AbortController()
    sourceRef.current = controller
    streamEvents(`/api/runs/${runId}/events`, {
      signal: controller.signal,
      onState: (s) => setStream(s),
      onEvent: (frame) => {
        try {
          const event = JSON.parse(frame.data)
          if (event.seq > maxSeq.current) {
            maxSeq.current = event.seq
            setEvents((prev) => [...prev, event])
          }
        } catch {
          /* a malformed frame never kills the stream */
        }
      },
      // The server closes the stream at terminal status; stop
      // reconnecting once the snapshot confirms it.
      shouldStop: async () => {
        const snap = await refresh()
        return !!snap && TERMINAL.includes(snap.status)
      },
    })
    return () => controller.abort()
  }, [runId, snapError, refresh])

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
