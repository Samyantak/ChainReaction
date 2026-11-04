import React, { useEffect, useRef, useState } from 'react'

/* DotBreak: module ids break after dots, never mid-word. Pair with
 * normal wrapping (no break-word/break-all) at the call site. The
 * single span root keeps flex parents (domino tiles) wrapping the
 * text as one item instead of laying each part out side by side. */
export function DotBreak({ text }) {
  const parts = String(text ?? '').split('.')
  return (
    <span className="dotbreak">
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {part}
          {i < parts.length - 1 ? '.' : null}
          {i < parts.length - 1 ? <wbr /> : null}
        </React.Fragment>
      ))}
    </span>
  )
}

/* PathBreak: file paths break after '/' only, never mid-name. */
export function PathBreak({ text }) {
  const parts = String(text ?? '').replace(/\\/g, '/').split('/')
  return (
    <span className="dotbreak">
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {part}
          {i < parts.length - 1 ? '/' : null}
          {i < parts.length - 1 ? <wbr /> : null}
        </React.Fragment>
      ))}
    </span>
  )
}

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

/* Chip: boxed mono label; accent for breaking sites, tone for status. */
export function Chip({ accent = false, sky = false, tone = null, children }) {
  const cls = `chip${accent ? ' chip-accent' : ''}${sky ? ' chip-sky' : ''}${tone ? ` chip-${tone}` : ''}`
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

/* DimensionRule: thin dimension line with arrowheads above a value. */
export function DimensionRule() {
  return (
    <svg className="dim-rule" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      <path
        d="M9 4 H91 M9 4 L2 1.5 M9 4 L2 6.5 M91 4 L98 1.5 M91 4 L98 6.5"
        stroke="currentColor"
        strokeWidth="1.5"
        fill="none"
      />
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
        style={{
          position: 'absolute',
          top: '50%',
          left: '-6px',
          width: 'calc(100% + 18px)',
          height: '2.4em',
          transform: 'translateY(-50%)',
          pointerEvents: 'none',
        }}
      >
        <ellipse
          cx="60" cy="30" rx="56" ry="25"
          fill="none" stroke="var(--red)" strokeWidth="2.5"
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray="95.5 4.5"
          className="draw-in"
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
        <path
          d="M2 5 C 30 3, 70 7, 98 4"
          fill="none"
          stroke="var(--red)"
          strokeWidth="2.5"
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray="100 100"
          className="draw-in"
        />
      </svg>
    </span>
  )
}

/* RedNote: Caveat handwriting for annotations only — never UI text. */
export function RedNote({ children }) {
  return <span className="red-note">{children}</span>
}

/* MatchStamp: the ✓ MATCH rubber stamp for proven chains. */
export function MatchStamp({ small = false }) {
  return <span className={`stamp${small ? ' stamp-small' : ''}`}>✓ MATCH</span>
}

/* Eyebrow: mono §0n · LABEL with a 36px rule above a section. */
export function Eyebrow({ n, label }) {
  return (
    <div className="eyebrow mono">
      <span className="eyebrow-rule" aria-hidden="true" />§{n} · {label}
    </div>
  )
}

/* Reveal: rise in on first view. Reduced-motion and missing
 * IntersectionObserver show content immediately, never hidden. */
export function Reveal({ index = 0, className = '', children, ...rest }) {
  const ref = useRef(null)
  const [shown, setShown] = useState(
    () =>
      typeof window === 'undefined' ||
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ||
      !('IntersectionObserver' in window),
  )
  useEffect(() => {
    if (shown || !ref.current) return undefined
    const el = ref.current
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true)
          io.disconnect()
        }
      },
      { threshold: 0.08 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [shown])
  return (
    <div
      ref={ref}
      className={`reveal${shown ? ' revealed' : ''}${className ? ` ${className}` : ''}`}
      style={{ transitionDelay: `${index * 60}ms` }}
      {...rest}
    >
      {children}
    </div>
  )
}

/* Logo: three dominoes tipping right, the last one red. */
export function Logo() {
  return (
    <svg className="logo-mark" viewBox="0 0 36 22" aria-hidden="true">
      <rect x="2" y="5" width="9" height="15" fill="var(--sheet)" stroke="var(--ink)" strokeWidth="2" />
      <rect
        x="14" y="4" width="9" height="15"
        fill="var(--sheet)" stroke="var(--ink)" strokeWidth="2"
        transform="rotate(8 18 19)"
      />
      <rect
        x="25" y="3" width="9" height="15"
        fill="var(--red)" stroke="var(--red)" strokeWidth="2"
        transform="rotate(16 29 18)"
      />
    </svg>
  )
}

/* FileIcon: tiny document glyph for source chips. */
export function FileIcon() {
  return (
    <svg className="file-icon" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d="M2 1 h5 l3 3 v7 h-8 z M7 1 v3 h3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  )
}
