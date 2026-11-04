import test from 'node:test'
import assert from 'node:assert/strict'
import {
  blindLine,
  detMethodsLine,
  normalizeDropped,
  survivalRate,
  tallyKinds,
} from '../src/interpretUtils.js'
import { counterSeries, parseDiff } from '../src/fixUtils.js'

test('kinds tally sorted by count', () => {
  assert.deepEqual(
    tallyKinds([{ kind: 'removed' }, { kind: 'deprecated' }, { kind: 'removed' }, {}]),
    [['removed', 2], ['deprecated', 1], ['unknown', 1]],
  )
  assert.deepEqual(tallyKinds(null), [])
})

test('survival prefers recorded rate, falls back to verified share', () => {
  const items = [{ verified: true }, { verified: false }]
  assert.deepEqual(survivalRate({ interpret_survival_rate: 1.0 }, items), {
    verified: 1,
    total: 2,
    rate: 1.0,
  })
  assert.deepEqual(survivalRate({}, items), { verified: 1, total: 2, rate: 0.5 })
  assert.deepEqual(survivalRate({}, []), { verified: 0, total: 0, rate: null })
})

test('blind line needs a table', () => {
  assert.equal(
    blindLine({ interpret_table_size: 62, interpret_blind_hits: 9, interpret_missed: [] }),
    'Muse Spark alone 9/62 → with table extraction 62/62',
  )
  assert.equal(blindLine({ interpret_table_size: 0 }), null)
  assert.equal(blindLine({}), null)
})

test('detection methods and dropped normalize both shapes', () => {
  assert.equal(
    detMethodsLine({ interpret_det_by_method: { list: 0, pattern: 12, table: 0 } }),
    'detection by method: pattern 12',
  )
  assert.equal(detMethodsLine({}), null)
  assert.deepEqual(
    normalizeDropped({ dropped: [{ symbol: 'a', kind: 'renamed', reason: 'duplicate' }] }),
    [{ symbol: 'a', kind: 'renamed', reason: 'duplicate' }],
  )
  assert.deepEqual(normalizeDropped({ dropped_symbols: ['b'] }), [
    { symbol: 'b', kind: null, reason: null },
  ])
  assert.deepEqual(normalizeDropped({}), [])
})

test('diff parses hunks, adds, dels, context', () => {
  const rows = parseDiff(
    '--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n',
  )
  assert.deepEqual(
    rows.map((r) => r.type),
    ['meta', 'meta', 'hunk', 'ctx', 'del', 'add'],
  )
  assert.equal(rows[4].text, 'old')
  assert.equal(rows[5].text, 'new')
})

test('counter series tracks failures per round', () => {
  const fix = {
    tests_before: { failed: 4, passed: 120 },
    rounds: [{ tests_after: { failed: 1 } }, { tests_after: { failed: 0 } }],
  }
  assert.deepEqual(counterSeries(fix), [4, 1, 0])
  assert.equal(counterSeries({}), null)
  assert.equal(counterSeries({ tests_before: { failed: 2 }, rounds: [{}] }), null)
})
