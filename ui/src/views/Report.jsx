import React from 'react'
import { useApi, useOptionalJson } from '../api'
import { fixModel } from '../fixUtils'
import { Button, DimensionRule, FileIcon, RedUnderline, Sheet, Status, TitleBlock } from '../components'
import '../report.css'

function ClaimRow({ claim }) {
  const tone = claim.status === 'pass' ? 'ok' : claim.status === 'fail' ? 'flag' : 'pending'
  const text = claim.status === 'pass' ? 'Verified' : claim.status === 'fail' ? 'Flagged' : 'Pending'
  const label =
    claim.status === 'fail' ? <RedUnderline>{claim.label}</RedUnderline> : claim.label
  return (
    <div className="claim-row" title={claim.detail || claim.label}>
      <Status tone={tone} text={text} />
      <span className="claim-label">{label}</span>
      {claim.detail && <span className="mono claim-detail">{claim.detail}</span>}
    </div>
  )
}

function Narrative({ report, claims }) {
  const narrative = report.narrative
  return (
    <section>
      <h3>Narrative</h3>
      {!narrative && <p className="view-empty">No narrative recorded — the explainer hasn&apos;t run.</p>}
      {narrative &&
        narrative.split('\n\n').map((para, i) => <p key={i}>{para}</p>)}
      {claims && claims.length > 0 && (
        <Sheet className="claims-sheet card">
          <div className="mono sheet-no">CLAIM CHECKS</div>
          {claims.map((c, i) => (
            <ClaimRow key={`${c.label}-${i}`} claim={c} />
          ))}
        </Sheet>
      )}
    </section>
  )
}

function fmtPR(value) {
  return value == null ? '—' : Number(value).toFixed(2)
}

function Scoreboard({ report, scores, runId, fix }) {
  const hasPr = (report.pr_files || []).length > 0
  const baseline = useOptionalJson(hasPr ? `/api/runs/${runId}/baseline-files` : null)
  const measures = []
  if (report.spine_match != null) {
    measures.push({
      key: 'chain',
      value: <Status tone={report.spine_match ? 'ok' : 'flag'} text={report.spine_match ? 'MATCH' : 'DIFFER'} />,
      label: 'predicted vs actual chain',
      source: 'verify layer logs',
    })
  }
  if (scores && scores.test_precision != null && scores.test_recall != null) {
    measures.push({
      key: 'test-pr',
      value: `${Number(scores.test_precision).toFixed(2)} / ${Number(scores.test_recall).toFixed(2)}`,
      label: 'test precision / recall',
      sub: `${(scores.actual_new || []).length} new failures`,
      source: 'scores.json',
    })
  }
  const prFiles = report.pr_files || []
  if (prFiles.length > 0) {
    const behavior = new Set(report.file_behavior || [])
    const missing = new Set(report.file_missing || [])
    const inPr = prFiles.filter((f) => behavior.has(f)).length
    const breakingTotal = prFiles.length - inPr
    const breakingFound = breakingTotal - prFiles.filter((f) => missing.has(f) && !behavior.has(f)).length
    const found = prFiles.length - missing.size
    const prMatch = (report.pr_url || '').match(/\/pull\/(\d+)/)
    measures.push({
      key: 'file-pr',
      value: `${breakingFound}/${breakingTotal}`,
      label: 'breaking files',
      sub:
        `${found}/${prFiles.length} including behavior changes` +
        (report.file_precision != null ? ` · P ${fmtPR(report.file_precision)}` : ''),
      source: prMatch ? `PR #${prMatch[1]}` : report.pr_url || 'upstream PR',
    })
  }
  if (report.interpret_table_size) {
    const size = report.interpret_table_size
    const withTable = Math.round((report.interpret_recall ?? 1) * size)
    measures.push({
      key: 'blind',
      value: `${report.interpret_blind_hits ?? 0}/${size} → ${withTable}/${size}`,
      label: 'Muse Spark alone → with table extraction',
      sub: 'removal-table recall',
      source: 'changeset.json',
    })
  }
  const baselineTools = baseline
    ? ['grep', 'ruff'].map((tool) => ({ tool, stats: baseline[tool] })).filter((t) => t.stats)
    : []
  baselineTools.forEach(({ tool, stats }) => {
    const pr = new Set(baseline.pr_files || [])
    const matched = (stats.files || []).filter((f) => pr.has(f)).length
    const total = (baseline.pr_files || []).length
    measures.push({
      key: `base-${tool}`,
      value: `${matched}/${total}`,
      label: `${tool} files in ${baseline.pr_ref || 'upstream PR'}`,
      sub: `P ${fmtPR(stats.precision)} · R ${fmtPR(stats.recall)} · ${stats.hits} hits`,
      source: 'baselines',
    })
  })
  if (baselineTools.length === 0 && (report.grep_hits != null || report.ruff_hits != null)) {
    const parts = []
    if (report.grep_hits != null) parts.push(`grep ${report.grep_hits}`)
    if (report.ruff_hits != null) parts.push(`ruff ${report.ruff_hits}`)
    measures.push({
      key: 'baselines',
      value: parts.join(' · ') || '—',
      label: 'baseline hits',
      source: 'baselines',
    })
  }
  const fm = fixModel(fix)
  if (fm && fm.rounds.length) {
    measures.push({
      key: 'fix',
      value: fm.verdict.label,
      label: 'fix loop verdict',
      sub: `${fm.rounds.length} rounds${fm.importFixedAt ? ` · import fixed in round ${fm.importFixedAt}` : ''} · ${fm.stillFailing} left · ${fm.newlyBroken} newly broken`,
      source: 'fix.json',
    })
  }
  if (measures.length === 0) return null
  return (
    <section>
      <h3>Scoreboard</h3>
      <div className="measure-row">
        {measures.map((m) => (
          <div className="measure" key={m.key}>
            <DimensionRule />
            <div className="measure-value">{m.value}</div>
            <div className="measure-label">{m.label}</div>
            {m.sub && <div className="measure-sub">{m.sub}</div>}
            {m.source && (
              <div className="mono measure-source">
                <FileIcon />
                {m.source}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function repoHref(repoUrl, commit) {
  if (!repoUrl) return null
  if (commit && repoUrl.includes('github.com')) {
    return `${repoUrl.replace(/\/$/, '')}/commit/${commit}`
  }
  return repoUrl
}

function Provenance({ manifest, counts }) {
  const commit = manifest.resolved_commit || manifest.ref || ''
  const repoName = (manifest.repo_url || '').replace(/\/$/, '').split('/').pop()
  const href = repoHref(manifest.repo_url, manifest.resolved_commit)
  const target =
    repoName || manifest.preset
      ? `${repoName || manifest.preset} @ ${commit ? commit.slice(0, 12) : 'unknown'}`
      : 'unknown'
  const targetCell = href ? (
    <a href={href} target="_blank" rel="noreferrer">
      {target}
    </a>
  ) : (
    target
  )
  return (
    <section>
      <h3>Provenance</h3>
      <div className="title-stack">
        <TitleBlock
          cells={[
            ['MODEL', manifest.model || 'unknown'],
            ['CREATED', (manifest.created_at || '').slice(0, 16).replace('T', ' ') || '—'],
            ['FINISHED', (manifest.finished_at || '').slice(0, 16).replace('T', ' ') || '—'],
            ['CODE', (manifest.chainreaction_git_sha || '').slice(0, 12) || 'unknown'],
          ]}
        />
        <TitleBlock
          cells={[
            ['TARGET', targetCell],
            [
              'RECORDED',
              counts
                ? `${counts.llm_calls} llm · ${counts.cmds} cmds · ${counts.fetches} fetches`
                : '…',
            ],
            ['REPLAYED FROM', manifest.replay_from || (manifest.mode === 'replay' ? '—' : 'live run')],
          ]}
        />
      </div>
    </section>
  )
}

export default function ReportView({ runId }) {
  const { data: snapshot, error: snapError } = useApi(`/api/runs/${runId}`)
  const report = snapshot?.report
  const scores = useOptionalJson(`/api/runs/${runId}/artifacts/scores.json`)
  const fix = useOptionalJson(`/api/runs/${runId}/artifacts/fix.json`)
  const { data: claimsData } = useApi(report ? `/api/runs/${runId}/claims` : null)
  const { data: counts } = useApi(report ? `/api/runs/${runId}/provenance` : null)

  if (snapError) {
    return (
      <div className="run-view">
        <h2>Report</h2>
        <div className="view-error">Could not load this run: {snapError}.</div>
      </div>
    )
  }
  if (!snapshot) {
    return (
      <div className="run-view">
        <h2>Report</h2>
        <p>Loading report…</p>
      </div>
    )
  }
  if (!report) {
    return (
      <div className="run-view">
        <h2>Report</h2>
        <p className="view-empty">No report yet — the report stage hasn&apos;t run.</p>
      </div>
    )
  }

  return (
    <div className="run-view report-view">
      <h2>Report</h2>
      <Narrative report={report} claims={claimsData?.claims} />
      <Scoreboard report={report} scores={scores} runId={runId} fix={fix} />
      <Provenance manifest={snapshot.manifest} counts={counts} />
      <section>
        <h3>Export</h3>
        <div className="export-row">
          <Button kind="secondary" href={`/api/runs/${runId}/export?format=md`} download>
            Markdown
          </Button>
          <Button kind="secondary" href={`/api/runs/${runId}/export?format=json`} download>
            JSON
          </Button>
          <Button kind="secondary" href={`/api/runs/${runId}/export?format=bundle`} download>
            Run bundle (zip)
          </Button>
        </div>
        <p className="view-empty">Exports are the run&apos;s own bytes — identical to the artifacts above.</p>
      </section>
    </div>
  )
}
