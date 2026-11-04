import test from 'node:test'
import assert from 'node:assert/strict'
import {
  capabilityStatus,
  countReplayable,
  miniRailStates,
  stripTiles,
  tipAngle,
} from '../src/polishUtils.js'

const CHAIN = ['nibabel', 'nibabel.analyze', 'nibabel.casting']
const EVIDENCE = [
  ['nibabel', 'nibabel.analyze', 'nibabel/__init__.py', 41],
  ['nibabel.analyze', 'nibabel.casting', 'nibabel/analyze.py', 88],
  ['nibabel.casting', 'numpy.sctypes', 'nibabel/casting.py', 717],
]
const BREAK = { path: 'nibabel/casting.py', line: 717, symbol: 'numpy.sctypes' }

test('strip tiles run root first with the break site on the decision tile', () => {
  assert.deepEqual(stripTiles(CHAIN, EVIDENCE, BREAK), [
    { module: 'nibabel', role: 'root', file: 'nibabel/__init__.py', line: 41 },
    { module: 'nibabel.analyze', role: 'link', file: 'nibabel/analyze.py', line: 88 },
    { module: 'nibabel.casting', role: 'decision', file: 'nibabel/casting.py', line: 717 },
  ])
})

test('strip tiles tolerate missing hops and breaks', () => {
  assert.deepEqual(stripTiles([], EVIDENCE, BREAK), [])
  assert.deepEqual(stripTiles(null, EVIDENCE, BREAK), [])
  assert.deepEqual(stripTiles(['a', 'b'], [], null), [
    { module: 'a', role: 'root', file: null, line: null },
    { module: 'b', role: 'decision', file: null, line: null },
  ])
})

test('tip angles cycle 0/4/8/13/19', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6].map(tipAngle), [0, 4, 8, 13, 19, 0, 4])
})

test('capability status reads provenance', () => {
  assert.equal(capabilityStatus({ id: 'structured-output' }), 'IN USE')
  assert.equal(capabilityStatus({ id: 'search-grounding' }), 'IN USE')
  assert.equal(capabilityStatus({ id: 'tool-calling' }), 'M4')
  assert.equal(capabilityStatus({ id: 'muse-code' }), 'BUILD')
})

test('replayable count follows has_recording', () => {
  assert.equal(countReplayable(null), 0)
  assert.equal(
    countReplayable([{ has_recording: true }, { has_recording: false }, {}]),
    1,
  )
})

test('mini rail: done recordings ran every stage, gated outputs gate M4/M5', () => {
  const states = miniRailStates('done', { investigation: null, fix: null })
  assert.equal(states.setup.state, 'done')
  assert.equal(states.verify.state, 'done')
  assert.equal(states.report.state, 'done')
  assert.deepEqual(states.investigate, { state: 'skipped', reason: 'not available yet (M4)' })
  assert.deepEqual(states.fix, { state: 'skipped', reason: 'not available yet (M5)' })
  const lit = miniRailStates('done', { investigation: {}, fix: {} })
  assert.equal(lit.investigate.state, 'done')
  assert.equal(lit.fix.state, 'done')
  const pending = miniRailStates('running', {})
  assert.equal(pending.setup.state, 'pending')
  assert.equal(pending.fix.state, 'pending')
})
