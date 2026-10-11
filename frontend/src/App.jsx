import { useEffect, useState } from 'react'
import { getLocations, getMachines, getMaterials } from './api'
import EstimateForm from './components/EstimateForm'
import Results from './components/Results'
import RatesView from './components/RatesView'
import Icon from './components/Icon'
import './App.css'

const TABS = [
  { id: 'estimate', label: 'Estimate' },
  { id: 'rates', label: 'Rates' },
]

function App() {
  const [tab, setTab] = useState('estimate')
  const [catalog, setCatalog] = useState({ locations: [], materials: [], machines: [] })
  const [catalogError, setCatalogError] = useState(null)
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('theme') || 'system'
    } catch {
      return 'system'
    }
  })
  const [result, setResult] = useState(null) // { kind: 'analysis' | 'estimate', data }

  useEffect(() => {
    Promise.all([getLocations(), getMaterials(), getMachines()])
      .then(([locations, materials, machines]) => setCatalog({ locations, materials, machines }))
      .catch((e) => setCatalogError(e.message))
      .finally(() => setCatalogLoading(false))
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('theme', theme)
    } catch {
      // storage unavailable (private mode) — theme just won't persist
    }
  }, [theme])

  const nextTheme = { system: 'light', light: 'dark', dark: 'system' }[theme]

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-mark">
              <Icon name="box" size={20} />
            </span>
            <div>
              <h1>Should-Costing</h1>
              <p>Part cost from a drawing or CAD model</p>
            </div>
          </div>
          <nav className="tabs" role="tablist" aria-label="View">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                className={tab === t.id ? 'tab active' : 'tab'}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setTheme(nextTheme)}
            aria-label={`Theme: ${theme}. Switch to ${nextTheme}`}
            title={`Theme: ${theme}`}
          >
            <Icon name={{ system: 'monitor', light: 'sun', dark: 'moon' }[theme]} />
          </button>
        </div>
      </header>

      {catalogError && (
        <div className="banner error" role="alert">
          <Icon name="alert" />
          <div>
            <strong>Can't reach the costing server.</strong> {catalogError}
            <code>./dev.sh</code>
          </div>
        </div>
      )}

      <main className="main">
        {tab === 'estimate' ? (
          <div className="estimate-layout">
            <EstimateForm catalog={catalog} loading={catalogLoading} onResult={setResult} />
            <Results result={result} materials={catalog.materials} />
          </div>
        ) : (
          <RatesView locations={catalog.locations} loading={catalogLoading} />
        )}
      </main>
    </div>
  )
}

export default App
