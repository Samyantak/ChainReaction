import test from 'node:test'
import assert from 'node:assert/strict'
import { GRAPH_MAX_ZOOM, GRAPH_MIN_FIT_ZOOM, describeEdge } from '../src/graphUtils.js'

test('zoom stays inside the readable band', () => {
  assert.equal(GRAPH_MAX_ZOOM, 1.2)
  assert.equal(GRAPH_MIN_FIT_ZOOM, 0.75)
})

test('chain edges show whenever their endpoints do', () => {
  assert.deepEqual(
    describeEdge({ isChain: true, endsVisible: true, selected: null, sourceId: 'a', targetId: 'b' }),
    ['element', false],
  )
  assert.deepEqual(
    describeEdge({ isChain: true, endsVisible: false, selected: null, sourceId: 'a', targetId: 'b' }),
    ['none', false],
  )
})

test('plain edges appear only around the selection', () => {
  const base = { isChain: false, endsVisible: true, sourceId: 'a', targetId: 'b' }
  assert.deepEqual(describeEdge({ ...base, selected: null }), ['none', false])
  assert.deepEqual(describeEdge({ ...base, selected: 'a' }), ['element', true])
  assert.deepEqual(describeEdge({ ...base, selected: 'b' }), ['element', true])
  assert.deepEqual(describeEdge({ ...base, selected: 'c' }), ['none', false])
  assert.deepEqual(describeEdge({ ...base, selected: 'a', endsVisible: false }), ['none', false])
})
