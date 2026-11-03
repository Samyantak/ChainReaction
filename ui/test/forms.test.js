import test from 'node:test'
import assert from 'node:assert/strict'
import {
  decisionPreviewText,
  formatDuration,
  repoShort,
  validateGithubUrl,
} from '../src/formUtils.js'

test('github urls accept owner/repo with optional .git', () => {
  assert.equal(validateGithubUrl('https://github.com/aio-libs/aiohttp-jinja2'), null)
  assert.equal(validateGithubUrl('https://github.com/o/r.git'), null)
  assert.equal(validateGithubUrl('https://github.com/o/r/'), null)
  assert.equal(validateGithubUrl(''), 'Repo URL is required.')
  assert.ok(validateGithubUrl('https://gitlab.com/o/r'))
  assert.ok(validateGithubUrl('not a url'))
  assert.ok(validateGithubUrl('https://github.com/only-owner'))
})

test('repo short keeps owner/repo', () => {
  assert.equal(repoShort('https://github.com/aio-libs/aiohttp-jinja2'), 'aio-libs/aiohttp-jinja2')
  assert.equal(repoShort('https://github.com/o/r.git'), 'o/r')
  assert.equal(repoShort(''), '')
  assert.equal(repoShort(null), '')
})

test('decision preview covers pinned and free-form', () => {
  assert.equal(
    decisionPreviewText({ package: 'werkzeug', from_version: '2.2', to_version: '3.0' }),
    'Package werkzeug from 2.2 → 3.0.',
  )
  assert.equal(
    decisionPreviewText({ package: 'numpy', from_version: '', to_version: '2.0' }),
    'Package numpy → 2.0.',
  )
  assert.equal(
    decisionPreviewText({ package: null, from_version: '', to_version: null }),
    'Free-form decision — the model interprets it at run time.',
  )
  assert.equal(
    decisionPreviewText(null),
    'Free-form decision — the model interprets it at run time.',
  )
})

test('durations render m:ss and hMM', () => {
  assert.equal(formatDuration('2026-11-03T23:21:19.323Z', '2026-11-03T23:22:42.323Z'), '1:23')
  assert.equal(formatDuration('2026-11-03T23:21:19Z', '2026-11-04T01:25:19Z'), '2h04')
  assert.equal(formatDuration('2026-11-03T23:21:19Z', null), null)
  assert.equal(formatDuration(null, null), null)
})
