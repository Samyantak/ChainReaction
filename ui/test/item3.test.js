import test from 'node:test'
import assert from 'node:assert/strict'
import {
  blindLine,
  detMethodsLine,
  normalizeDropped,
  survivalRate,
  tallyKinds,
} from '../src/interpretUtils.js'
import { diffFiles, fixModel, guardRows, parseDiff, splitDiff, stopText } from '../src/fixUtils.js'
import { readFileSync } from 'node:fs'

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

test('diff rows carry line numbers from hunk headers', () => {
  const rows = parseDiff('--- a/x.py\n+++ b/x.py\n@@ -10,3 +10,3 @@\n a\n-old\n+new\n b\n')
  assert.deepEqual(rows.filter((r) => r.type === 'ctx').map((r) => [r.oldNo, r.newNo]), [[10, 10], [12, 12]])
  assert.equal(rows.find((r) => r.type === 'del').oldNo, 11)
  assert.equal(rows.find((r) => r.type === 'add').newNo, 11)
  assert.deepEqual(diffFiles('--- a/x.py\n+++ b/x.py\n--- a/y.py\n+++ b/y.py\n'), ['x.py', 'y.py'])
})

test('split diff pairs deletions with the additions that replace them', () => {
  const pairs = splitDiff(parseDiff('@@ -1,3 +1,4 @@\n a\n-b\n-c\n+B\n d\n+e\n'))
  assert.equal(pairs[0].hunk, '@@ -1,3 +1,4 @@')
  assert.equal(pairs[1].left.text, 'a')
  assert.deepEqual([pairs[2].left.text, pairs[2].right.text], ['b', 'B'])
  assert.deepEqual([pairs[3].left.text, pairs[3].right], ['c', null])
  assert.deepEqual([pairs[5].left, pairs[5].right.text], [null, 'e'])
})

test('fix model follows the real fix.json shape', () => {
  const fix = JSON.parse(readFileSync(new URL('./fixtures/fix.example.json', import.meta.url)))
  const m = fixModel(fix)
  assert.equal(m.verdict.label, 'PARTIAL')
  assert.equal(m.rounds.length, 5)
  assert.equal(m.importBroken, true)
  assert.equal(m.importFixedAt, 2)
  assert.equal(m.rounds[0].kind, 'probe')
  assert.match(m.rounds[0].outcome, /next break at nibabel\/quaternions.py:32/)
  assert.equal(m.rounds[1].outcome, 'import now succeeds')
  assert.equal(m.rounds[3].progress, false)
  assert.equal(m.rounds[0].diffPath, 'fixes/round-1.diff')
  assert.equal(m.stillFailing, 35)
  assert.equal(m.newlyBroken, 0)
  assert.equal(m.baselineFailures, 125)
  assert.equal(m.guards.ok, true)
  assert.deepEqual(m.upstream.overlap.length, 3)
  assert.equal(stopText('no-progress-twice'), 'stopped after two rounds without progress')
})

test('guard rows turn red per violation category', () => {
  const rows = guardRows({ ok: false, violations: ['added skip/xfail marker: @pytest.mark.skip', 'setup.cfg: dependency/packaging file edited'] })
  assert.deepEqual(rows.map((r) => r.ok), [false, true, true, false])
  assert.deepEqual(guardRows({ ok: true, violations: [] }).every((r) => r.ok), true)
})

test('quote links repair double-hash fragments and build from the source URL', async () => {
  const { quoteLink, recallParts, filterItems } = await import('../src/interpretUtils.js')
  assert.equal(
    quoteLink({ link: 'https://x.dev/changes/#v3#:~:text=Deprecate' }),
    'https://x.dev/changes/#v3:~:text=Deprecate',
  )
  assert.equal(
    quoteLink({ quote: 'np.sctypes was removed' }, 'https://numpy.org/guide.html'),
    'https://numpy.org/guide.html#:~:text=np.sctypes%20was%20removed',
  )
  assert.equal(quoteLink({ link: 'src/sponsorfit/llm.py:14' }), null)
  assert.deepEqual(recallParts({ interpret_table_size: 65, interpret_blind_hits: 12, interpret_missed: [] }), {
    table: 65, blind: 12, added: 53, missed: 0,
  })
  const items = [{ symbol: 'a', kind: 'removed' }, { symbol: 'b', kind: 'deprecated' }, { symbol: 'c', kind: 'removed' }]
  assert.deepEqual(filterItems(items, { used: new Set(['c']) }).map((i) => i.symbol), ['c', 'a', 'b'])
  assert.deepEqual(filterItems(items, { kind: 'removed', q: 'a' }).map((i) => i.symbol), ['a'])
})
