import { useEffect, useState } from 'react'
import { getRates } from '../api'
import { money } from '../format'

function RateTable({ title, rows, nameKey, unit }) {
  return (
    <section className="panel section">
      <div className="section-head">
        <h2>{title}</h2>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th className="num">Local</th>
              <th className="num">USD</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r[nameKey]}>
                <td>
                  <span className={nameKey === 'skill' ? 'cap' : ''}>{r[nameKey]}</span>
                  <div className="muted small">
                    {r.source}
                    {r.as_of && ` · as of ${r.as_of}`}
                  </div>
                </td>
                <td className="num">
                  {money(r.price_local_per_kg ?? r.rate_local_per_hour, r.currency)}
                  <span className="muted">{unit}</span>
                </td>
                <td className="num muted">
                  {money(r.price_usd_per_kg ?? r.rate_usd_per_hour)}
                  {unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function RatesView({ locations, loading }) {
  const [locationId, setLocationId] = useState('')
  const [rates, setRates] = useState(null)
  const [error, setError] = useState(null)
  const location = locationId || locations[0]?.id || ''

  useEffect(() => {
    if (!location) return
    let cancelled = false
    getRates(location)
      .then((r) => !cancelled && (setRates(r), setError(null)))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [location])

  const loc = rates?.location

  return (
    <div className="rates">
      <div className="panel rates-head">
        <label className="field inline">
          <span>Location</span>
          <select value={location} onChange={(e) => setLocationId(e.target.value)} disabled={loading}>
            {loading && <option value="">Loading…</option>}
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        {loc && (
          <div className="rate-facts">
            <span>
              Currency <b>{loc.currency}</b> · {loc.fx_per_usd} per USD ({loc.fx_source}
              {loc.fx_as_of && `, ${loc.fx_as_of}`})
            </span>
            <span>
              Overhead <b>{loc.overhead_pct}%</b>
            </span>
            <span>
              Profit <b>{loc.profit_pct}%</b>
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}

      {!rates && !error && location && <p className="muted">Loading rates…</p>}

      {rates && (
        <div className="rates-grid">
          <RateTable title="Labour" rows={rates.labour} nameKey="skill" unit="/h" />
          <RateTable title="Materials" rows={rates.materials} nameKey="material_name" unit="/kg" />
          <RateTable title="Machines" rows={rates.machines} nameKey="machine_name" unit="/h" />
        </div>
      )}
      <p className="muted small">Indicative benchmark rates from the server's data tables.</p>
    </div>
  )
}
