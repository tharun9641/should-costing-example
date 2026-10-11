import CostBar from './CostBar'
import { downloadCsv, downloadJson } from '../export'
import { money, num, SHAPE_LABELS } from '../format'

function Tile({ label, value, sub }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  )
}

function Section({ title, children, aside }) {
  return (
    <section className="panel section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

function Routing({ routing }) {
  return (
    <ol className="routing">
      {routing.map((s, i) => (
        <li key={`${s.machine_id}-${i}`}>
          <span className="step-no">{i + 1}</span>
          <div>
            <div className="step-title">
              <strong>{s.machine_name}</strong>
              <span className={`badge role-${s.role}`}>{s.role}</span>
            </div>
            <p className="muted small">{s.reason}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}

function Features({ f, materials }) {
  const rows = [
    ['Source', `${f.source.toUpperCase()} · ${f.file_name}`],
    ['Shape', SHAPE_LABELS[f.shape]],
    ['Material', f.material_id ? `${materials?.[f.material_id] ?? f.material_id}${f.material_text && f.material_text !== 'override' ? ` (“${f.material_text}”)` : ''}` : 'Not detected'],
    ['Envelope', f.bbox_mm ? `${f.bbox_mm.map((d) => num(d, 1)).join(' × ')} mm` : 'Unknown'],
    ['Volume', f.volume_cm3 != null ? `${num(f.volume_cm3)} cm³` : '—'],
    ['Surface area', f.surface_area_cm2 != null ? `${num(f.surface_area_cm2)} cm²` : '—'],
    ['Holes / threads / bends', `${f.hole_count} / ${f.thread_count} / ${f.bend_count}`],
    ['Tightest tolerance', f.tightest_tolerance_mm != null ? `±${f.tightest_tolerance_mm} mm` : '—'],
    ['Best surface finish', f.best_surface_finish_ra_um != null ? `Ra ${f.best_surface_finish_ra_um} µm` : '—'],
    ['Complexity', f.complexity],
    ['Units in file', f.units_detected],
  ]
  return (
    <dl className="features">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Notes({ items, title }) {
  if (!items?.length) return null
  return (
    <div className="notes">
      <h3>{title}</h3>
      <ul>
        {items.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  )
}

export default function Results({ result, materials = [] }) {
  if (!result) {
    return (
      <div className="panel empty">
        <h2>No estimate yet</h2>
        <p className="muted">
          Upload a PDF drawing or a CAD model, pick the manufacturing location and quantity, then
          press <b>Estimate cost</b>. You'll get material, labour and machine cost, and the machines
          needed to make the part.
        </p>
        <p className="muted small">
          <b>Analyse only</b> shows what was read from the file and the suggested machines, without
          pricing.
        </p>
      </div>
    )
  }

  const { kind, data } = result
  const f = data.features
  const materialNames = Object.fromEntries(materials.map((m) => [m.id, m.name]))
  const confidence = <span className={`badge conf-${f.confidence}`}>{f.confidence} confidence</span>

  if (kind === 'analysis') {
    return (
      <div className="results">
        <Section title="Machines required">
          <Routing routing={data.routing} />
        </Section>
        <Section title="Extracted from file" aside={confidence}>
          <Features f={f} materials={materialNames} />
          <Notes title="Notes" items={f.notes} />
        </Section>
      </div>
    )
  }

  const { cost, location, material, material_rate: mr, operations, quantity } = data
  const totalCycle = operations.reduce((s, o) => s + o.cycle_time_min, 0)

  return (
    <div className="results">
      <div className="result-actions">
        <span className="muted small">
          {f.file_name} · {location.name} · {num(quantity, 0)} pcs
        </span>
        <div className="result-buttons">
          <button type="button" className="btn ghost" onClick={() => downloadCsv(data)}>
            CSV
          </button>
          <button type="button" className="btn ghost" onClick={() => downloadJson(data)}>
            JSON
          </button>
          <button type="button" className="btn ghost" onClick={() => window.print()}>
            Print
          </button>
        </div>
      </div>
      <div className="tiles">
        <Tile label="Unit price" value={money(cost.unit_price_local, cost.currency)} sub={money(cost.unit_price_usd)} />
        <Tile
          label={`Total · ${num(quantity, 0)} pcs`}
          value={money(cost.total_price_local, cost.currency)}
          sub={money(cost.total_price_usd)}
        />
        <Tile
          label="Stock / part mass"
          value={`${num(data.stock_mass_kg, 3)} kg`}
          sub={data.part_mass_kg != null ? `part ${num(data.part_mass_kg, 3)} kg` : null}
        />
        <Tile label="Cycle time" value={`${num(totalCycle, 1)} min`} sub={`${operations.length} operation${operations.length === 1 ? '' : 's'}`} />
      </div>

      <Section title="Cost breakdown per unit" aside={<span className="muted small">{location.name}</span>}>
        <CostBar cost={cost} fx={location.fx_per_usd} />
      </Section>

      <div className="two-col">
        <Section title="Material">
          <dl className="features compact">
            <div><dt>Material</dt><dd>{material.name}</dd></div>
            <div><dt>Price</dt><dd>{money(mr.price_local_per_kg, mr.currency)}/kg <span className="muted">({money(mr.price_usd_per_kg)})</span></dd></div>
            <div><dt>Density</dt><dd>{material.density_g_cm3} g/cm³</dd></div>
            <div><dt>Stock mass</dt><dd>{num(data.stock_mass_kg, 3)} kg</dd></div>
          </dl>
        </Section>
        <Section title="Labour rates">
          <dl className="features compact">
            {Object.entries(location.labour_rates).map(([skill, usd]) => (
              <div key={skill}>
                <dt className="cap">{skill}</dt>
                <dd>{money(usd * location.fx_per_usd, location.currency)}/h <span className="muted">({money(usd)})</span></dd>
              </div>
            ))}
          </dl>
        </Section>
      </div>

      <Section title="Machines & operations">
        <Routing routing={data.routing} />
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Machine</th>
                <th className="num">Cycle (min)</th>
                <th className="num">Setup / unit (min)</th>
                <th className="num">Machine cost</th>
                <th className="num">Labour cost</th>
              </tr>
            </thead>
            <tbody>
              {operations.map((o, i) => (
                <tr key={`${o.machine_id}-${i}`}>
                  <td>{o.machine_name}</td>
                  <td className="num">{num(o.cycle_time_min)}</td>
                  <td className="num">{num(o.setup_time_min)}</td>
                  <td className="num">{money(o.machine_cost_usd * location.fx_per_usd, location.currency)}</td>
                  <td className="num">{money(o.labour_cost_usd * location.fx_per_usd, location.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Extracted from file" aside={confidence}>
        <Features f={f} materials={{ [material.id]: material.name }} />
        <Notes title="Notes" items={f.notes} />
        <Notes title="Assumptions" items={data.assumptions} />
      </Section>
    </div>
  )
}
