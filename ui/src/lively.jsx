import React, { useEffect, useRef, useState } from 'react'

/* Motion helpers for the "lively" pass. Everything here degrades to a
 * static, fully readable page under prefers-reduced-motion. */

export const prefersReduced = () =>
  typeof window !== 'undefined' && (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)

/* useInView: true once the element has scrolled into view (sticky). */
export function useInView(threshold = 0.25) {
  const ref = useRef(null)
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (seen || !ref.current) return undefined
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true)
          io.disconnect()
        }
      },
      { threshold },
    )
    io.observe(ref.current)
    return () => io.disconnect()
  }, [seen, threshold])
  return [ref, seen]
}

/* CountUp: animates the leading number in a value like "226",
 * "65/65" or "0.97" from zero when it scrolls into view. Non-numeric
 * values render as-is. */
export function CountUp({ value, duration = 1100 }) {
  const text = String(value ?? '')
  const m = /^(\d+(?:\.\d+)?)([\s\S]*)$/.exec(text)
  const [ref, seen] = useInView(0.4)
  const [n, setN] = useState(() => (m && !prefersReduced() ? 0 : null))
  useEffect(() => {
    if (!m || !seen || prefersReduced()) {
      setN(null)
      return undefined
    }
    const target = Number(m[1])
    const start = performance.now()
    let raf
    const tick = (t) => {
      const p = Math.min(1, (t - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setN(target * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
      else setN(null)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [seen, text])
  if (!m || n == null) return <span ref={ref}>{text}</span>
  const decimals = (m[1].split('.')[1] || '').length
  return (
    <span ref={ref}>
      {n.toFixed(decimals)}
      {m[2]}
    </span>
  )
}

/* usePointerVars: writes the pointer position over an element into CSS
 * variables (--px/--py in -1..1, --mx/--my in px) for parallax and
 * spotlight effects. Throttled to one write per frame. */
export function usePointerVars() {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el || prefersReduced()) return undefined
    let raf = 0
    const onMove = (e) => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const r = el.getBoundingClientRect()
        const x = e.clientX - r.left
        const y = e.clientY - r.top
        el.style.setProperty('--mx', `${x}px`)
        el.style.setProperty('--my', `${y}px`)
        el.style.setProperty('--px', ((x / r.width) * 2 - 1).toFixed(3))
        el.style.setProperty('--py', ((y / r.height) * 2 - 1).toFixed(3))
      })
    }
    const onLeave = () => {
      el.style.setProperty('--px', '0')
      el.style.setProperty('--py', '0')
    }
    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerleave', onLeave)
    return () => {
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerleave', onLeave)
      cancelAnimationFrame(raf)
    }
  }, [])
  return ref
}

/* Tilt: a card that leans toward the pointer (3D), for preset cards. */
export function Tilt({ className = '', children, max = 5 }) {
  const ref = useRef(null)
  const onMove = (e) => {
    if (prefersReduced()) return
    const el = ref.current
    const r = el.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width - 0.5
    const y = (e.clientY - r.top) / r.height - 0.5
    el.style.setProperty('--tilt-x', `${(-y * max).toFixed(2)}deg`)
    el.style.setProperty('--tilt-y', `${(x * max).toFixed(2)}deg`)
    el.style.setProperty('--glare-x', `${((x + 0.5) * 100).toFixed(1)}%`)
    el.style.setProperty('--glare-y', `${((y + 0.5) * 100).toFixed(1)}%`)
  }
  const onLeave = () => {
    const el = ref.current
    el.style.setProperty('--tilt-x', '0deg')
    el.style.setProperty('--tilt-y', '0deg')
  }
  return (
    <div ref={ref} className={`tilt ${className}`.trim()} onPointerMove={onMove} onPointerLeave={onLeave}>
      {children}
      <span className="tilt-glare" aria-hidden="true" />
    </div>
  )
}

/* Ticker: an endless strip of real breaks caught across the presets.
 * Pauses on hover and focus; static wrap under reduced motion. */
export function Ticker({ items, onPick }) {
  if (!items || items.length === 0) return null
  const row = (hidden) => (
    <div className="ticker-row" aria-hidden={hidden || undefined}>
      {items.map((it, i) => (
        <button
          key={`${it.key}-${i}`}
          type="button"
          className="ticker-item"
          tabIndex={hidden ? -1 : undefined}
          onClick={() => onPick?.(it)}
          title={`Replay ${it.repo}`}
        >
          <span className="ticker-pkg mono">{it.pkg}</span>
          <span className="ticker-sym mono">{it.symbol}</span>
          <span className="ticker-arrow" aria-hidden="true">→</span>
          <span className="ticker-site mono">{it.site}</span>
          <span className="ticker-repo">{it.repo}</span>
        </button>
      ))}
    </div>
  )
  return (
    <div className="ticker" role="region" aria-label="Breaks caught across the recorded presets">
      <div className="ticker-track" style={{ '--ticker-count': items.length }}>
        {row(false)}
        {row(true)}
      </div>
    </div>
  )
}
