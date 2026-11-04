import test from 'node:test'
import assert from 'node:assert/strict'
import {
  GRAPH_MAX_ZOOM,
  GRAPH_MIN_FIT_ZOOM,
  describeEdge,
  edgeState,
  neighborhood,
  nodeVisible,
  shortLabel,
} from '../src/graphUtils.js'

test('zoom stays inside the readable band', () => {
  assert.ok(GRAPH_MAX_ZOOM > 1 && GRAPH_MAX_ZOOM <= 1.5)
  assert.ok(GRAPH_MIN_FIT_ZOOM >= 0.5 && GRAPH_MIN_FIT_ZOOM < 1)
})

test('edges: chain always bold, others faint, selection lights and dims', () => {
  const base = { isChain: false, endsVisible: true, sourceId: 'a', targetId: 'b' }
  assert.equal(edgeState({ ...base, isChain: true, selected: null }), 'chain')
  assert.equal(edgeState({ ...base, isChain: true, endsVisible: false, selected: null }), 'hidden')
  assert.equal(edgeState({ ...base, selected: null }), 'faint')
  assert.equal(edgeState({ ...base, selected: 'a' }), 'lit')
  assert.equal(edgeState({ ...base, selected: 'b' }), 'lit')
  assert.equal(edgeState({ ...base, selected: 'c' }), 'dim')
  assert.equal(edgeState({ ...base, selected: 'a', endsVisible: false }), 'hidden')
  assert.deepEqual(describeEdge({ ...base, selected: 'a' }), ['element', true])
})

test('labels and neighborhoods', () => {
  assert.equal(shortLabel('nibabel'), 'nibabel')
  assert.equal(shortLabel('nibabel.casting'), 'nibabel.casting')
  assert.equal(shortLabel('anndata._core.anndata'), '_core.anndata')
  const edges = [{ source: 'a', target: 'b' }, { source: 'c', target: 'a' }, { source: 'd', target: 'e' }]
  assert.deepEqual([...neighborhood(edges, 'a')].sort(), ['a', 'b', 'c'])
  assert.equal(neighborhood(edges, null).size, 0)
})

test('legend filters combine status, behavior, and search', () => {
  const all = { HARD: true, AT_RISK: true, BEHAVIOR: true }
  assert.equal(nodeVisible({ status: 'HARD', behavior: 0, id: 'a.b' }, all, ''), true)
  assert.equal(
    nodeVisible({ status: 'HARD', behavior: 0, id: 'a.b' }, { ...all, HARD: false }, ''),
    false,
  )
  assert.equal(
    nodeVisible({ status: 'AT_RISK', behavior: 1, id: 'a.b' }, { ...all, BEHAVIOR: false }, ''),
    false,
  )
  assert.equal(
    nodeVisible({ status: 'AT_RISK', behavior: 1, id: 'a.b' }, all, ''),
    true,
  )
  assert.equal(nodeVisible({ status: 'HARD', behavior: 0, id: 'a.b' }, all, 'zzz'), false)
  assert.equal(nodeVisible({ status: 'HARD', behavior: 0, id: 'a.b' }, all, 'A.B'), true)
})

test('spine layout puts the chain on one line and hangs the rest by neighbor', async () => {
  const { spineLayout } = await import('../src/graphUtils.js')
  const nodes = ['brk', 'mid', 'root', 'x', 'y', 'z'].map((id) => ({ id }))
  const edges = [
    { source: 'root', target: 'mid' }, { source: 'mid', target: 'brk' },
    { source: 'x', target: 'brk' }, { source: 'root', target: 'y' }, { source: 'y', target: 'mid' },
  ]
  const pos = spineLayout(nodes, edges, ['brk', 'mid', 'root'])
  assert.deepEqual([pos.brk.y, pos.mid.y, pos.root.y], [0, 0, 0])
  assert.ok(pos.brk.x < pos.mid.x && pos.mid.x < pos.root.x)
  assert.equal(pos.x.x, pos.brk.x) // hangs under the break
  assert.equal(pos.y.x, pos.mid.x) // closest chain neighbor to the break wins
  assert.ok(pos.z.x > pos.root.x) // no chain neighbor: far right
  assert.notEqual(pos.x.y, 0)
  assert.equal(spineLayout(nodes, edges, ['brk']), null)
})
