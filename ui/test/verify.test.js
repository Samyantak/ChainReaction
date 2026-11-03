import test from 'node:test'
import assert from 'node:assert/strict'
import { describeMatch, describeVersions } from '../src/verifyUtils.js'

test('match stays green when import failed with a captured chain', () => {
  const after = { ok: false, actual: ['pkg', 'pkg.mod'] }
  assert.equal(describeMatch(true, after), 'match')
})

test('failed import with no captured chain is never green', () => {
  const after = { ok: false, actual: [] }
  assert.equal(describeMatch(true, after), 'not-compared')
  assert.equal(describeMatch(false, after), 'differ')
})

test('passed import with empty chains still matches', () => {
  const after = { ok: true, actual: [] }
  assert.equal(describeMatch(true, after), 'match')
})

test('null spine_match renders no badge', () => {
  assert.equal(describeMatch(null, null), null)
  assert.equal(describeMatch(undefined, { ok: false, actual: [] }), null)
})

test('generic versions come from envs freeze', () => {
  const report = { decision: 'upgrade jinja2 to 3.1' }
  const envs = {
    decision: { package: 'jinja2', from_version: '', to_version: '3.1.6' },
    before: { freeze: { jinja2: '2.11.3' } },
    after: { freeze: { jinja2: '3.1.6' } },
  }
  assert.deepEqual(describeVersions(report, envs), {
    before: 'jinja2 2.11.3',
    after: 'jinja2 3.1.6',
  })
})

test('freeze lookup is case-insensitive', () => {
  const report = {}
  const envs = {
    decision: { package: 'Werkzeug', to_version: '3.0.6' },
    before: { freeze: { werkzeug: '2.2.3' } },
    after: { freeze: {} },
  }
  assert.deepEqual(describeVersions(report, envs), {
    before: 'Werkzeug 2.2.3',
    after: 'Werkzeug 3.0.6',
  })
})

test('older reports fall back to numpy fields', () => {
  const report = { before_numpy: '1.26.4', numpy_version: '2.0.2' }
  assert.deepEqual(describeVersions(report, null), {
    before: 'numpy 1.26.4',
    after: 'numpy 2.0.2',
  })
  assert.deepEqual(describeVersions({}, null), { before: null, after: null })
})
