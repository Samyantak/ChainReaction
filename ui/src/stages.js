/* Pure stage derivation for the run shell (no React imports, so
 * Node can exercise the same code the UI runs). */

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

// Stages that don't exist yet run as backend stubs that save no output.
// An "ended" stub must not read done: the stage counts as done only once
// its recorded output exists, otherwise skipped with why. The rule needs
// no update when M4/M5 land — saving the artifact lights the stage up.
const GATED_OUTPUTS = {
  investigate: { names: ['investigation.json'], prefixes: [], arrivesIn: 'M4' },
  fix: { names: ['fix.json'], prefixes: ['fixes/'], arrivesIn: 'M5' },
}

export function savedArtifacts(events) {
  return events.filter((e) => e.type === 'artifact').map((e) => e.data?.name || '')
}

export function stageStates(stages, events, snapshot, streamClosed = false) {
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
  // Gated stages (investigate/fix) that ended without saving their
  // output artifact ran as stubs: downgrade done to skipped with why.
  const saved = savedArtifacts(events)
  Object.entries(GATED_OUTPUTS).forEach(([stage, gate]) => {
    if (!states[stage] || states[stage].state !== 'done') return
    const produced = saved.some(
      (name) => gate.names.includes(name) || gate.prefixes.some((p) => name.startsWith(p)),
    )
    if (!produced) {
      states[stage] = { ...states[stage], state: 'skipped', reason: `not available yet (${gate.arrivesIn})` }
    }
  })
  // A stage counts as skipped only once the full event history is in:
  // terminal snapshot plus a closed stream, with no stage_start seen.
  // (The backend runs every stage straight through; a done run never
  // skips, a failed/cancelled one skips what never started.)
  if (snapshot && TERMINAL.includes(snapshot.status) && streamClosed) {
    stages.forEach((stage) => {
      if (states[stage].state === 'pending') {
        states[stage] = { state: 'skipped', reason: `run ${snapshot.status}` }
      } else if (states[stage].state === 'running') {
        states[stage] = {
          ...states[stage],
          state: snapshot.status === 'done' ? 'done' : snapshot.status,
        }
      }
    })
  }
  return states
}
