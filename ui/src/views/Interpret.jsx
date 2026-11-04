import React, { useMemo, useState } from 'react'
import { useApi, useOptionalJson } from '../api'
import {
  codeAnchor,
  detMethodsLine,
  filterItems,
  normalizeDropped,
  quoteLink,
  recallParts,
  survivalRate,
  tallyKinds,
  tallyMethods,
} from '../interpretUtils'
import { Chip, DimensionRule, PathBreak, Sheet, Status } from '../components'
import '../interpret.css'

/* Interpret: where the change set came from, how complete it is, and
 * every item with its verified quote — filterable, searchable, with the
 * items this repo actually uses pinned first. */

const FOUND_VIA = {
  search: 'search grounding',
  pypi_meta: 'PyPI metadata',
  guide_url: 'given guide URL',
  github_releases: 'GitHub releases',
  changelog: 'changelog',
}

function Sources({ changeset, sources }) {
  const list = sources?.sources?.length
    ? sources.sources
    : Object.keys(changeset?.source_counts || {})
        .filter((u) => u !== 'unattributed')
        .map((url) => ({ url, used: true }))
  const main = !list.length && changeset?.source_url ? [{ url: changeset.source_url, used: true }] : []
  const all = [...list, ...main]
  const tried = sources?.tried || []
  const citations = tried.map((t) => /search:\s*(\d+)\s*citation/.exec(t)).find(Boolean)
  return (
    <Sheet className="card interp-sources">
      <div className="interp-sources-head">
        <h3>Sources read</h3>
        {citations && <Chip sky>search grounding · {citations[1]} citations</Chip>}
        {sources?.from_version && sources?.to_version && (
          <span className="mono muted">
            {sources.package} {sources.from_version} → {sources.to_version}
          </span>
        )}
      </div>
      {all.length === 0 ? (
        <p className="muted">No external source — items come from the code-aware pass.</p>
      ) : (
        <ul className="source-list">
          {all.map((s) => {
            const c = changeset?.source_counts?.[s.url]
            return (
              <li key={s.url} className={s.used === false ? 'unused' : ''}>
                <a href={s.url} target="_blank" rel="noreferrer" className="mono">
                  {s.url.replace(/^https?:\/\//, '')}
                </a>
                <span className="source-meta">
                  {s.found_via && <Chip>{FOUND_VIA[s.found_via] || s.found_via}</Chip>}
                  {c && c.proposed > 0 && (
                    <span className="mono muted">
                      {c.verified}/{c.proposed} quotes verified{c.dropped ? ` · ${c.dropped} dropped` : ''}
                    </span>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {(changeset?.releases || []).length > 0 && (
        <p className="mono muted releases">
          releases covered · {changeset.releases[0]} → {changeset.releases[changeset.releases.length - 1]} (
          {changeset.releases.length})
        </p>
      )}
    </Sheet>
  )
}

function RecallBar({ parts }) {
  const pct = (n) => `${(n / parts.table) * 100}%`
  return (
    <div className="recall">
      <div className="recall-bar" role="img" aria-label={`Model alone ${parts.blind} of ${parts.table}; with extraction ${parts.blind + parts.added} of ${parts.table}`}>
        <span className="seg-blind" style={{ width: pct(parts.blind) }} />
        <span className="seg-added" style={{ width: pct(parts.added) }} />
        <span className="seg-missed" style={{ width: pct(parts.missed) }} />
      </div>
      <div className="recall-legend mono">
        <span><i className="key key-blind" /> Muse Spark alone {parts.blind}</span>
        <span><i className="key key-added" /> added by table extraction {parts.added}</span>
        {parts.missed > 0 && <span><i className="key key-missed" /> missed {parts.missed}</span>}
      </div>
    </div>
  )
}

function Summary({ changeset, items }) {
  const survival = survivalRate(changeset, items)
  const parts = recallParts(changeset)
  const det = detMethodsLine(changeset)
  const modelOnly = changeset.interpret_model_only || []
  const anchored = items.filter((i) => codeAnchor(i)).length
  return (
    <div className="interp-measures">
      <div className="measure">
        <DimensionRule />
        <div className="measure-value">{items.length}</div>
        <div className="measure-label">change items</div>
        <div className="measure-note mono">
          {tallyKinds(items)
            .slice(0, 4)
            .map(([k, n]) => `${n} ${k}`)
            .join(' · ')}
        </div>
      </div>
      <div className="measure">
        <DimensionRule />
        <div className="measure-value tone-ok">
          {survival.rate != null ? `${Math.round(survival.rate * 100)}%` : '—'}
        </div>
        <div className="measure-label">quotes verified word for word</div>
        <div className="measure-note mono">
          {survival.verified}/{survival.total} kept · unverified quotes are dropped
        </div>
      </div>
      <div className="measure measure-wide">
        <DimensionRule />
        {parts ? (
          <>
            <div className="measure-value">
              {parts.blind}/{parts.table} → {parts.blind + parts.added}/{parts.table}
            </div>
            <div className="measure-label">removal-table recall</div>
            <RecallBar parts={parts} />
          </>
        ) : anchored > 0 ? (
          <>
            <div className="measure-value">{anchored}/{items.length}</div>
            <div className="measure-label">anchored to a file:line in your own code</div>
            <div className="measure-note mono">code-aware pass · repo source + vendor guide in one 1M-token context</div>
          </>
        ) : (
          <>
            <div className="measure-value">{modelOnly.length || '—'}</div>
            <div className="measure-label">found by Muse Spark beyond deterministic extraction</div>
            {det && <div className="measure-note mono">{det}</div>}
          </>
        )}
      </div>
    </div>
  )
}

function ItemRow({ item, used, fallbackUrl }) {
  const [open, setOpen] = useState(false)
  const link = quoteLink(item, fallbackUrl)
  const anchor = codeAnchor(item)
  const breaking = ['removed', 'renamed', 'signature_changed', 'rejected_value', 'missing_param', 'missing_key'].includes(item.kind)
  return (
    <div className={`item-row${used ? ' item-used' : ''}${open ? ' item-open' : ''}`}>
      <button type="button" className="item-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="item-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="mono item-symbol">{item.symbol}</span>
        <Chip tone={breaking ? 'flag' : 'mute'}>{String(item.kind || 'unknown').replace(/_/g, ' ')}</Chip>
        {used && <Chip accent>used here</Chip>}
        <span className="spacer" />
        <span className="mono item-meta">
          {[item.extraction, item.introduced_in, item.confidence].filter(Boolean).join(' · ')}
        </span>
        {item.verified !== false ? (
          <span className="status status-ok item-check" title="quote found word for word in the source">✓</span>
        ) : (
          <span className="status status-flag item-check" title="quote not found">✗</span>
        )}
      </button>
      {open && (
        <div className="item-body">
          {item.quote ? (
            <blockquote className="item-quote">{item.quote}</blockquote>
          ) : null}
          {anchor && (
            <div className="item-code">
              <Chip accent>
                <PathBreak text={anchor} />
              </Chip>
              {item.code_quote && <pre className="mono">{item.code_quote}</pre>}
              {item.detail?.expected && (
                <p className="mono muted">
                  expected {item.detail.expected} · found {item.detail.actual}
                </p>
              )}
              {item.detail?.note && <p>{item.detail.note}</p>}
            </div>
          )}
          <div className="item-foot">
            {item.replacement && (
              <span>
                replace with <span className="mono replacement">{item.replacement}</span>
              </span>
            )}
            <Status
              tone={item.verified !== false ? 'ok' : 'flag'}
              text={item.verified !== false ? `quote verified (${item.verified_mode || 'exact'})` : 'quote not found — excluded'}
            />
            {link && !(anchor && !item.quote) && (
              <a href={link} target="_blank" rel="noreferrer" className="item-link">
                Open the source at this quote ↗
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function InterpretView({ runId }) {
  const { data: changeset, error } = useApi(`/api/runs/${runId}/artifacts/changeset.json`)
  const report = useOptionalJson(`/api/runs/${runId}/artifacts/report.json`)
  const sources = useOptionalJson(`/api/runs/${runId}/artifacts/sources.json`)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('all')
  const [method, setMethod] = useState('all')
  const [usedOnly, setUsedOnly] = useState(false)

  const items = changeset?.items || []
  const used = useMemo(() => new Set(report?.used_symbols || []), [report])
  const shown = useMemo(
    () => filterItems(items, { q, kind, method, usedOnly, used }),
    [items, q, kind, method, usedOnly, used],
  )

  if (error) {
    return (
      <div className="run-view">
        <h2>Interpret</h2>
        <div className="view-error">No change set yet — the interpret stage hasn&apos;t run ({error}).</div>
      </div>
    )
  }
  if (!changeset) {
    return (
      <div className="run-view">
        <h2>Interpret</h2>
        <p className="view-empty">Loading change set…</p>
      </div>
    )
  }

  const kinds = tallyKinds(items)
  const methods = tallyMethods(items)
  const dropped = normalizeDropped(changeset)
  const usedCount = items.filter((i) => used.has(i.symbol)).length

  return (
    <div className="run-view interpret-view">
      <h2>Interpret</h2>
      <p className="interp-lede">
        Muse Spark read the official sources and turned them into a change set. An item survives only if its quote
        appears in the source word for word.
      </p>

      <Summary changeset={changeset} items={items} />
      <Sources changeset={changeset} sources={sources} />

      <div className="interp-toolbar">
        <label className="interp-search">
          <span className="visually-hidden">Search change items</span>
          <input
            type="search"
            placeholder="Search symbols, quotes, replacements"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <div className="filter-chips" role="group" aria-label="Filter by kind">
          <button type="button" aria-pressed={kind === 'all'} onClick={() => setKind('all')}>
            all {items.length}
          </button>
          {kinds.map(([k, n]) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(kind === k ? 'all' : k)}>
              {k.replace(/_/g, ' ')} {n}
            </button>
          ))}
        </div>
        {methods.length > 1 && (
          <div className="filter-chips" role="group" aria-label="Filter by how it was found">
            {methods.map(([m, n]) => (
              <button
                key={m}
                type="button"
                aria-pressed={method === m}
                onClick={() => setMethod(method === m ? 'all' : m)}
              >
                {m} {n}
              </button>
            ))}
          </div>
        )}
        {usedCount > 0 && (
          <label className="used-toggle">
            <input type="checkbox" checked={usedOnly} onChange={(e) => setUsedOnly(e.target.checked)} />
            only the {usedCount} this repo uses
          </label>
        )}
      </div>

      <p className="mono muted interp-count">
        showing {shown.length} of {items.length}
        {usedCount > 0 && !usedOnly ? ` · ${usedCount} used here, pinned first` : ''}
      </p>
      <div className="item-list">
        {shown.map((item) => (
          <ItemRow key={`${item.symbol}-${item.kind}`} item={item} used={used.has(item.symbol)} fallbackUrl={changeset.source_url} />
        ))}
        {shown.length === 0 && <p className="view-empty">No items match these filters.</p>}
      </div>

      {dropped.length > 0 && (
        <details className="dropped-block">
          <summary>
            Dropped before prediction <span className="mono">({dropped.length})</span>
          </summary>
          <p className="muted">Proposed by the model but excluded — the reason is recorded for each.</p>
          {dropped.map((d, i) => (
            <div className="mono dropped-row" key={`${d.symbol}-${i}`}>
              <span className="dropped-symbol">{d.symbol}</span>
              {d.kind ? ` · ${d.kind}` : ''}
              {d.reason ? <span className="dropped-reason"> — {String(d.reason).replace(/_/g, ' ')}</span> : ''}
            </div>
          ))}
        </details>
      )}
    </div>
  )
}
