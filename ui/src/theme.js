import { useEffect, useState } from 'react'

/* Theme: explicit toggle persisted in localStorage, default follows the
 * OS preference. Sets data-theme on <html>; theme.css holds the tokens. */

export function initialTheme() {
  try {
    const saved = localStorage.getItem('cr-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    /* private mode: fall through to the OS preference */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState(initialTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('cr-theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])
  return [theme, setTheme]
}

/* Presenter mode (M6 §14): larger type + auto-advancing sheets for the
 * video. Persisted like the theme; sets data-presenter on <html>. */

export function usePresenter() {
  const [presenter, setPresenter] = useState(() => {
    try {
      return localStorage.getItem('cr-presenter') === 'on'
    } catch {
      return false
    }
  })
  useEffect(() => {
    document.documentElement.dataset.presenter = presenter ? 'on' : 'off'
    try {
      localStorage.setItem('cr-presenter', presenter ? 'on' : 'off')
    } catch {
      /* ignore */
    }
  }, [presenter])
  return [presenter, setPresenter]
}
