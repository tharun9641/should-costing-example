import { useState } from 'react'
import { money, pct } from '../format'

// Fixed categorical order: each cost component always gets the same slot.
const COMPONENTS = [
  { key: 'material_cost_usd', label: 'Material', slot: 1 },
  { key: 'labour_cost_usd', label: 'Labour', slot: 2 },
  { key: 'machine_cost_usd', label: 'Machine', slot: 3 },
  { key: 'overhead_usd', label: 'Overhead', slot: 4 },
  { key: 'profit_usd', label: 'Profit', slot: 5 },
]

export default function CostBar({ cost, fx }) {
  const [hover, setHover] = useState(null)
  const total = cost.unit_price_usd
  const local = (usd) => money(usd * fx, cost.currency)
  const parts = COMPONENTS.map((c) => ({ ...c, value: cost[c.key] })).filter((c) => c.value > 0)

  return (
    <div className="costbar">
      <div className="costbar-track" role="img" aria-label="Unit cost breakdown" onMouseLeave={() => setHover(null)}>
        {parts.map((p) => (
          <div
            key={p.key}
            className={`costbar-seg${hover && hover !== p.key ? ' dim' : ''}`}
            style={{ flexGrow: p.value, background: `var(--series-${p.slot})` }}
            onMouseEnter={() => setHover(p.key)}
          >
            {hover === p.key && (
              <div className="tooltip" role="tooltip">
                <span className="swatch" style={{ background: `var(--series-${p.slot})` }} />
                <strong>{p.label}</strong>
                <span>{local(p.value)}</span>
                <span className="muted">{pct(p.value, total)}</span>
              </div>
            )}
          </div>
        ))}
      </div>

      <table className="table legend-table">
        <thead>
          <tr>
            <th>Component</th>
            <th className="num">Per unit</th>
            <th className="num">USD</th>
            <th className="num">Share</th>
          </tr>
        </thead>
        <tbody>
          {COMPONENTS.map((c) => (
            <tr
              key={c.key}
              className={hover === c.key ? 'hl' : ''}
              onMouseEnter={() => setHover(c.key)}
              onMouseLeave={() => setHover(null)}
            >
              <td>
                <span className="swatch" style={{ background: `var(--series-${c.slot})` }} />
                {c.label}
              </td>
              <td className="num">{local(cost[c.key])}</td>
              <td className="num muted">{money(cost[c.key])}</td>
              <td className="num muted">{pct(cost[c.key], total)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>Unit price</td>
            <td className="num">{money(cost.unit_price_local, cost.currency)}</td>
            <td className="num">{money(cost.unit_price_usd)}</td>
            <td className="num">100%</td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}
