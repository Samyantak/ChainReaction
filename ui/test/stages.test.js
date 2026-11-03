// Maintained checks for the run-shell stage derivation (src/stages.js).
// Run: `npm test` from ui/. Zero dependencies (node:test + node:assert).
import test from 'node:test'
import assert from 'node:assert/strict'
import { STAGES, savedArtifacts, stageStates } from '../src/stages.js'

const ev = (type, data, stage = 'setup', ts = '2026-11-03T00:00:00Z') => ({
  seq: 1,
  ts,
  run_id: 'r',
  stage,
  type,
  data,
})
const doneSnap = { status: 'done' }

test('start/end/error drive running/done/failed', () => {
  const events = [
    ev('stage_start', { stage: 'setup' }),
    ev('stage_start', { stage: 'interpret' }),
    ev('stage_end', { stage: 'setup' }),
    ev('error', { stage: 'verify', error: 'boom' }, 'verify'),
  ]
  const states = stageStates(STAGES, events, { status: 'running' }, false)
  assert.equal(states.setup.state, 'done')
  assert.equal(states.interpret.state, 'running')
  assert.equal(states.verify.state, 'failed')
  assert.equal(states.verify.error, 'boom')
  assert.equal(states.report.state, 'pending')
})

test('terminal snapshot + closed stream settles pending/running', () => {
  const events = [ev('stage_start', { stage: 'setup' })]
  const states = stageStates(STAGES, events, doneSnap, true)
  assert.equal(states.setup.state, 'done')
  assert.equal(states.report.state, 'skipped')
  assert.equal(states.report.reason, 'run done')
})

test('open stream never marks unstarted stages skipped', () => {
  const states = stageStates(STAGES, [], { status: 'running' }, false)
  assert.ok(STAGES.every((s) => states[s].state === 'pending'))
})

test('investigate/fix without output read skipped, not done', () => {
  const events = [
    ev('stage_start', { stage: 'investigate' }, 'investigate'),
    ev('stage_end', { stage: 'investigate' }, 'investigate'),
    ev('stage_start', { stage: 'fix' }, 'fix'),
    ev('stage_end', { stage: 'fix' }, 'fix'),
    ev('stage_start', { stage: 'setup' }),
    ev('stage_end', { stage: 'setup' }),
  ]
  const states = stageStates(STAGES, events, doneSnap, true)
  assert.equal(states.investigate.state, 'skipped')
  assert.equal(states.investigate.reason, 'not available yet (M4)')
  assert.equal(states.fix.state, 'skipped')
  assert.equal(states.fix.reason, 'not available yet (M5)')
  assert.equal(states.setup.state, 'done')
})

test('investigate/fix with output artifacts read done', () => {
  const events = [
    ev('stage_start', { stage: 'investigate' }, 'investigate'),
    ev('artifact', { name: 'investigation.json' }, 'investigate'),
    ev('stage_end', { stage: 'investigate' }, 'investigate'),
    ev('stage_start', { stage: 'fix' }, 'fix'),
    ev('artifact', { name: 'fixes/round-1.diff' }, 'fix'),
    ev('stage_end', { stage: 'fix' }, 'fix'),
  ]
  const states = stageStates(STAGES, events, doneSnap, true)
  assert.equal(states.investigate.state, 'done')
  assert.equal(states.fix.state, 'done')
})

test('savedArtifacts collects artifact names', () => {
  const events = [
    ev('artifact', { name: 'report.json' }, 'report'),
    ev('log', { message: 'hi' }),
    ev('artifact', {}, 'verify'),
  ]
  assert.deepEqual(savedArtifacts(events), ['report.json', ''])
})
