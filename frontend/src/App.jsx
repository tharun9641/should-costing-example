import { useEffect, useState } from 'react'
import { getLocations, getMachines, getMaterials } from './api'
import EstimateForm from './components/EstimateForm'
import Results from './components/Results'
import RatesView from './components/RatesView'
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
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">₹$</span>
          <div>
            <h1>Should-Costing</h1>
            <p>Part cost from a drawing or CAD model</p>
          </div>
        </div>
        <nav className="tabs" role="tablist">
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
          className="theme-toggle"
          onClick={() => setTheme(nextTheme)}
          title={`Theme: ${theme} (click for ${nextTheme})`}
        >
          {{ system: '◐', light: '☀', dark: '☾' }[theme]} <span className="cap">{theme}</span>
        </button>
      </header>

      {catalogError && (
        <div className="banner error" role="alert">
          {catalogError}
          <code>cd server && .venv/bin/uvicorn app.main:app --reload --port 8000</code>
        </div>
      )}

      <main>
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
