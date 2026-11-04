import React, { useState } from 'react'
import { useApi, useOptionalJson } from '../api'
import {
  blindLine,
  detMethodsLine,
  normalizeDropped,
  survivalRate,
  tallyKinds,
} from '../interpretUtils'
import { Chip, Sheet, Status } from '../components'
import '../interpret.css'

function SourceLine({ changeset }) {
  const counts = changeset?.source_counts
  const urls = counts ? Object.keys(counts).filter((u) => u !== 'unattributed') : []
  const main = changeset?.source_url
  if (urls.length === 0 && !main) return <span className="mono">—</span>
  const shown = urls.length > 0 ? urls : [main]
  return (
    <span>
      {shown.map((url) => {
        const c = counts?.[url]
        return (
          <span key={url} className="source-entry">
            <a href={url} target="_blank" rel="noreferrer" className="mono">
              {url}
            </a>
            {c && (
              <span className="mono source-counts">
                {' '}
                · {c.verified}/{c.proposed} verified{c.dropped ? `, ${c.dropped} dropped` : ''}
              </span>
            )}
          </span>
        )
      })}
    </span>
  )
}

function ItemRow({ item, used }) {
  const [open, setOpen] = useState(false)
  const link = item.link || null
  return (
    <div className="sheet item-row">
      <button type="button" className="item-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="mono item-symbol">{item.symbol}</span>
        <Chip>{item.kind}</Chip>
        {used && <Status tone="ok" text="used here" />}
        <span className="spacer" />
        <span className="mono item-meta">
          {[item.introduced_in, item.confidence].filter(Boolean).join(' · ')}
        </span>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="item-body">
          {item.quote && <p className="item-quote">“{item.quote}”</p>}
          {item.replacement && (
            <p>
              → <span className="mono">{item.replacement}</span>
            </p>
          )}
          {link ? (
            <p>
              <a href={link} target="_blank" rel="noreferrer">
                open the source at this quote
              </a>
            </p>
          ) : (
            item.source_url && (
              <p>
                <a href={item.source_url} target="_blank" rel="noreferrer">
                  migration guide
                </a>
              </p>
            )
          )}
          {item.verified === false && (
            <p>
              <Status tone="flag" text="unverified" />
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function InterpretView({ runId }) {
  const { data: changeset, error } = useApi(`/api/runs/${runId}/artifacts/changeset.json`)
  const report = useOptionalJson(`/api/runs/${runId}/artifacts/report.json`)

  if (error) {
    return (
      <div className="run-view">
        <h2>Interpret</h2>
        <div className="view-error">
          No change set yet — the interpret stage hasn&apos;t run ({error}).
        </div>
      </div>
    )
  }
  if (!changeset) {
    return (
      <div className="run-view">
        <h2>Interpret</h2>
        <p>Loading change set…</p>
      </div>
    )
  }

  const items = changeset.items || []
  const kinds = tallyKinds(items)
  const survival = survivalRate(changeset, items)
  const blind = blindLine(changeset)
  const detMethods = detMethodsLine(changeset)
  const modelOnly = changeset.interpret_model_only || []
  const dropped = normalizeDropped(changeset)
  const used = new Set(report?.used_symbols || [])
  const releases = changeset.releases || []

  return (
    <div className="run-view interpret-view">
      <h2>Interpret</h2>

      <Sheet>
        <div className="interp-kv">
          <span className="interp-key">sources</span>
          <SourceLine changeset={changeset} />
        </div>
        {releases.length > 0 && (
          <div className="interp-kv">
            <span className="interp-key">releases</span>
            <span className="mono">{releases.join(', ')}</span>
          </div>
        )}
        {changeset.guide_method && (
          <div className="interp-kv">
            <span className="interp-key">guide found via</span>
            <span className="mono">{changeset.guide_method}</span>
          </div>
        )}
        <div className="interp-kv">
          <span className="interp-key">kinds</span>
          <span className="mono">
            {kinds.length > 0 ? kinds.map(([k, n]) => `${k} ${n}`).join(' · ') : '—'}
          </span>
        </div>
        <div className="interp-kv">
          <span className="interp-key">quote survival</span>
          <span className="mono">
            {survival.rate != null
              ? `${survival.verified}/${survival.total} verified (${Math.round(survival.rate * 100)}%)`
              : '—'}
          </span>
        </div>
        {blind && (
          <div className="interp-kv">
            <span className="interp-key">recall</span>
            <span className="mono">{blind}</span>
          </div>
        )}
        {!blind && detMethods && (
          <div className="interp-kv">
            <span className="interp-key">recall</span>
            <span className="mono">{detMethods}</span>
          </div>
        )}
        {modelOnly.length > 0 && (
          <div className="interp-kv">
            <span className="interp-key">model-only finds</span>
            <span className="mono">
              {modelOnly.length} · {modelOnly.slice(0, 6).join(', ')}
              {modelOnly.length > 6 ? ` +${modelOnly.length - 6} more` : ''}
            </span>
          </div>
        )}
      </Sheet>

      <h3>Change items ({items.length})</h3>
      {items.length === 0 && <p className="view-empty">No change items recorded.</p>}
      <div className="item-list">
        {items.map((item) => (
          <ItemRow key={item.symbol} item={item} used={used.has(item.symbol)} />
        ))}
      </div>

      {dropped.length > 0 && (
        <details className="dropped-block">
          <summary>
            Dropped items <span className="mono">({dropped.length})</span>
          </summary>
          {dropped.map((d) => (
            <div className="mono dropped-row" key={d.symbol}>
              {d.symbol}
              {d.kind ? ` · ${d.kind}` : ''}
              {d.reason ? ` — ${d.reason}` : ''}
            </div>
          ))}
        </details>
      )}
    </div>
  )
}
