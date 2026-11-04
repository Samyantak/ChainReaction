import React from 'react'
import { useRoute } from './router'
import { useStatus } from './api'
import { usePresenter, useTheme } from './theme'
import { Sheet } from './components'
import Landing from './views/Landing'
import Run from './views/Run'
import NewView from './views/New'
import HistoryView from './views/History'
import FeatureView from './views/Feature'

function KeyPill() {
  const status = useStatus()
  if (!status) return <span className="key-pill">…</span>
  const long = status.live_available ? 'Live mode available' : 'Replay only — no MODEL_API_KEY'
  const short = status.live_available ? 'Live' : 'Replay only'
  return (
    <span className="key-pill">
      <span className="long">{long}</span>
      <span className="short">{short}</span>
    </span>
  )
}

function AppHeader({ theme, onToggleTheme, presenter, onTogglePresenter }) {
  return (
    <header className="app-header">
      <a className="wordmark" href="#/">
        ChainReaction
      </a>
      <span className="dwg-tag mono">DWG CR-001</span>
      <nav>
        <a href="#/new">New analysis</a>
        <a href="#/history">History</a>
      </nav>
      <span className="spacer" />
      <KeyPill />
      <button
        className={`theme-toggle${presenter ? ' on' : ''}`}
        type="button"
        onClick={onTogglePresenter}
        aria-pressed={presenter}
        title="Presenter mode: larger type, sheets advance as stages finish"
      >
        Present
      </button>
      <button className="theme-toggle" type="button" onClick={onToggleTheme}>
        {theme === 'dark' ? 'Light' : 'Dark'}
      </button>
    </header>
  )
}

/* Step-1 route stubs. Each view lands in its own step; the router and
 * header are real from here on. */
function Stub({ title, step }) {
  return (
    <div className="page">
      <Sheet>
        <h2>{title}</h2>
        <p>
          This view lands in step {step}. The header, theme, and routing are live.
        </p>
        <p className="mono" style={{ fontSize: '12px', color: 'var(--ink-muted)' }}>
          Target repos shown in this app are pre-existing inputs, analyzed by ChainReaction.
        </p>
      </Sheet>
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const [theme, setTheme] = useTheme()
  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  const [presenter, setPresenter] = usePresenter()
  const togglePresenter = () => setPresenter((p) => !p)

  if (route.name === 'landing') {
    return <Landing />
  }

  let view
  switch (route.name) {
    case 'feature':
      view = <FeatureView id={route.params.id} />
      break
    case 'new':
      view = <NewView />
      break
    case 'run':
      view = <Run id={route.params.id} view={route.params.view} presenter={presenter} />
      break
    case 'history':
      view = <HistoryView />
      break
    default:
      view = <Stub title="Not found" step="—" />
  }

  return (
    <>
      <AppHeader
        theme={theme}
        onToggleTheme={toggleTheme}
        presenter={presenter}
        onTogglePresenter={togglePresenter}
      />
      <main>{view}</main>
    </>
  )
}
