import React from 'react'

/* Sheet: flat panel, square corners, 1.5px ink border. */
export function Sheet({ children, className = '', ...rest }) {
  return (
    <div className={`sheet ${className}`.trim()} {...rest}>
      {children}
    </div>
  )
}

/* TitleBlock: row of bordered mono LABEL / value cells. */
export function TitleBlock({ cells }) {
  return (
    <div className="title-block">
      {cells.map(([label, value, accent]) => (
        <span className="cell" key={label}>
          <span className="label">{label}</span>
          <span className={`value${accent ? ' accent' : ''}`}>{value}</span>
        </span>
      ))}
    </div>
  )
}

/* Button: primary = red fill, secondary = ink outline. Renders a link
 * when `href` is set so hash routes stay plain anchors. */
export function Button({ kind = 'secondary', href, children, ...rest }) {
  const cls = `btn btn-${kind}`
  if (href) {
    return (
      <a className={cls} href={href} {...rest}>
        {children}
      </a>
    )
  }
  return (
    <button className={cls} type="button" {...rest}>
      {children}
    </button>
  )
}

/* Chip: boxed mono label; accent for breaking sites. */
export function Chip({ accent = false, sky = false, children }) {
  const cls = `chip${accent ? ' chip-accent' : ''}${sky ? ' chip-sky' : ''}`
  return <span className={cls}>{children}</span>
}

/* Status: icon + text always, never color alone. */
const STATUS_ICON = { ok: '✓', flag: '✗', warn: '!', mute: '·', run: '…', pending: '○' }
export function Status({ tone = 'mute', text }) {
  return (
    <span className={`status status-${tone}`}>
      {STATUS_ICON[tone] ?? '·'} {text}
    </span>
  )
}

/* DimensionRule: thin |——| rule above a measured value. */
export function DimensionRule() {
  return (
    <svg className="dim-rule" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      <path d="M1 7 V1 M1 4 H99 M99 7 V1" stroke="currentColor" strokeWidth="1.5" fill="none" />
    </svg>
  )
}

/* RedPencil: hand-drawn SVG strokes (circle, underline, arrow) in red.
 * Decorative — the marked content carries its own text signal. */
export function RedCircle({ children, className = '' }) {
  return (
    <span style={{ position: 'relative', display: 'inline-block' }} className={className}>
      {children}
      <svg
        viewBox="0 0 120 60"
        preserveAspectRatio="none"
        aria-hidden="true"
        style={{ position: 'absolute', inset: '-30% -10px -30% -4px', pointerEvents: 'none' }}
      >
        <ellipse
          cx="60" cy="30" rx="56" ry="25"
          fill="none" stroke="var(--red)" strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray="300 14"
          transform="rotate(-3 60 30)"
        />
      </svg>
    </span>
  )
}

export function RedUnderline({ children }) {
  return (
    <span style={{ position: 'relative', display: 'inline-block' }}>
      {children}
      <svg
        viewBox="0 0 100 8"
        preserveAspectRatio="none"
        aria-hidden="true"
        style={{ position: 'absolute', left: 0, right: 0, bottom: '-4px', width: '100%', height: '8px', pointerEvents: 'none' }}
      >
        <path d="M2 5 C 30 3, 70 7, 98 4" fill="none" stroke="var(--red)" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    </span>
  )
}

/* RedNote: Caveat handwriting for annotations only — never UI text. */
export function RedNote({ children }) {
  return <span className="red-note">{children}</span>
}
