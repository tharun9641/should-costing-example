import { money } from '../format'

const TYPE_LABELS = {
  aluminium: 'Aluminium',
  steel: 'Steel',
  stainless: 'Stainless steel',
  copper: 'Brass / copper',
  titanium: 'Titanium',
  plastic: 'Plastic',
}

const typeLabel = (t) => TYPE_LABELS[t] ?? t.charAt(0).toUpperCase() + t.slice(1)

// Two-step material choice: type (category) chips, then a grade within that type.
// An empty value means "detect from the file".
export default function MaterialPicker({ materials, location, value, onChange }) {
  const types = [...new Set(materials.map((m) => m.category))]
  const selected = materials.find((m) => m.id === value)
  const type = selected?.category ?? ''
  const grades = materials.filter((m) => m.category === type)

  const pickType = (t) => onChange(t ? materials.find((m) => m.category === t).id : '')

  const localPrice =
    selected && location
      ? money(selected.price_usd_per_kg * location.material_price_factor * location.fx_per_usd, location.currency)
      : null

  return (
    <fieldset className="field">
      <span>Type</span>
      <div className="chips" role="radiogroup" aria-label="Material type">
        {['', ...types].map((t) => (
          <button
            type="button"
            key={t || 'detect'}
            role="radio"
            aria-checked={type === t}
            className={type === t ? 'chip on' : 'chip'}
            onClick={() => pickType(t)}
          >
            {t ? typeLabel(t) : 'Detect from file'}
          </button>
        ))}
      </div>

      {selected ? (
        <>
          <label className="field material-grade">
            <span>Grade</span>
            <select value={value} onChange={(e) => onChange(e.target.value)}>
              {grades.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <p className="material-facts muted small">
            {localPrice && <span>{localPrice}/kg</span>}
            <span>{selected.density_g_cm3} g/cm³</span>
            <span title="Relative to Al 6061 = 1.0; higher machines faster">
              machinability {selected.machinability}
            </span>
          </p>
        </>
      ) : (
        <p className="muted small material-facts">
          Read from the drawing or CAD file. Pick a type if the file doesn't specify one.
        </p>
      )}
    </fieldset>
  )
}
