import { useRef, useState } from 'react'
import { analyzePart, estimatePart } from '../api'
import MaterialPicker from './MaterialPicker'

const ACCEPT = '.pdf,.step,.stp,.stl,.obj,.ply,.off,.3mf,.glb'

function DropZone({ file, onFile }) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div
      className={`dropzone${dragging ? ' dragging' : ''}${file ? ' has-file' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
      />
      {file ? (
        <>
          <strong className="file-name">{file.name}</strong>
          <span className="muted">{(file.size / 1024).toFixed(1)} KB · click to replace</span>
        </>
      ) : (
        <>
          <strong>Drop a drawing or CAD file</strong>
          <span className="muted">PDF, STEP, STL, OBJ, PLY, 3MF, GLB</span>
        </>
      )}
    </div>
  )
}

export default function EstimateForm({ catalog, loading, onResult }) {
  const [file, setFile] = useState(null)
  const [locationId, setLocationId] = useState('')
  const [quantity, setQuantity] = useState(100)
  const [materialId, setMaterialId] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [dims, setDims] = useState({ length_mm: '', width_mm: '', height_mm: '' })
  const [shape, setShape] = useState('')
  const [machineIds, setMachineIds] = useState([])
  const [busy, setBusy] = useState(null) // 'analyze' | 'estimate'
  const [error, setError] = useState(null)

  const location = locationId || catalog.locations[0]?.id || ''

  const overrides = {
    material_id: materialId,
    ...dims,
    shape,
  }

  function validate(kind) {
    if (!file) return 'Choose a PDF drawing or CAD file first.'
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
    if (!ACCEPT.split(',').includes(ext)) return `Unsupported file type “${ext}”. Use ${ACCEPT}.`
    const given = Object.values(dims).filter((d) => d !== '')
    if (given.length && (given.length < 3 || given.some((d) => !(Number(d) > 0)))) {
      return 'Envelope override needs all of length, width and height, each greater than 0.'
    }
    if (kind === 'estimate') {
      const q = Number(quantity)
      if (!Number.isInteger(q) || q < 1 || q > 1_000_000) return 'Quantity must be a whole number from 1 to 1,000,000.'
      if (!location) return 'No location available — is the costing server running?'
    }
    return null
  }

  async function run(kind) {
    const problem = validate(kind)
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    setBusy(kind)
    try {
      const data =
        kind === 'analyze'
          ? await analyzePart({ file, ...overrides })
          : await estimatePart({
              file,
              ...overrides,
              location_id: location,
              quantity,
              machine_ids: machineIds.join(','),
            })
      onResult({ kind, data })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const activeOverrides =
    (Object.values(dims).some((d) => d !== '') ? 1 : 0) + (shape ? 1 : 0) + (machineIds.length ? 1 : 0)

  const toggleMachine = (id) =>
    setMachineIds((ids) => (ids.includes(id) ? ids.filter((m) => m !== id) : [...ids, id]))

  return (
    <form
      className="panel form"
      onSubmit={(e) => {
        e.preventDefault()
        run('estimate')
      }}
    >
      <h2>Part</h2>
      <DropZone
        file={file}
        onFile={(f) => {
          setFile(f)
          setError(null)
        }}
      />

      <label className="field">
        <span>Location</span>
        <select value={location} onChange={(e) => setLocationId(e.target.value)} disabled={loading}>
          {loading && <option value="">Loading…</option>}
          {catalog.locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name} ({l.currency})
            </option>
          ))}
        </select>
      </label>

      <label className="field quantity">
        <span>Quantity</span>
        <input
          type="number"
          min="1"
          max="1000000"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          required
        />
      </label>

      <MaterialPicker
        materials={catalog.materials}
        location={catalog.locations.find((l) => l.id === location)}
        value={materialId}
        onChange={setMaterialId}
      />

      <button
        type="button"
        className="disclosure"
        aria-expanded={showAdvanced}
        onClick={() => setShowAdvanced((s) => !s)}
      >
        {showAdvanced ? '▾' : '▸'} Overrides
        {activeOverrides > 0 && <span className="count">{activeOverrides} active</span>}
      </button>

      {showAdvanced && (
        <div className="advanced">
          <p className="muted small">
            Use these when the file doesn't carry the information (e.g. scanned PDFs).
          </p>
          <div className="field-row three">
            {[
              ['length_mm', 'Length'],
              ['width_mm', 'Width'],
              ['height_mm', 'Height'],
            ].map(([key, label]) => (
              <label className="field" key={key}>
                <span>{label} (mm)</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={dims[key]}
                  onChange={(e) => setDims((d) => ({ ...d, [key]: e.target.value }))}
                />
              </label>
            ))}
          </div>
          <label className="field">
            <span>Shape</span>
            <select value={shape} onChange={(e) => setShape(e.target.value)}>
              <option value="">Detect from file</option>
              <option value="rotational">Rotational (turned)</option>
              <option value="prismatic">Prismatic (milled)</option>
              <option value="sheet">Sheet metal</option>
            </select>
          </label>
          <fieldset className="field">
            <span className="field-label-row">
              Force machine routing (in order)
              {machineIds.length > 0 && (
                <button type="button" className="link" onClick={() => setMachineIds([])}>
                  Clear
                </button>
              )}
            </span>
            <div className="chips">
              {catalog.machines.map((m) => {
                const idx = machineIds.indexOf(m.id)
                return (
                  <button
                    type="button"
                    key={m.id}
                    className={idx >= 0 ? 'chip on' : 'chip'}
                    aria-pressed={idx >= 0}
                    onClick={() => toggleMachine(m.id)}
                    title={m.name}
                  >
                    {idx >= 0 && <b>{idx + 1}</b>}
                    {m.name}
                  </button>
                )
              })}
            </div>
          </fieldset>
        </div>
      )}

      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}

      <div className="actions">
        <button type="button" className="btn secondary" disabled={!!busy} onClick={() => run('analyze')}>
          {busy === 'analyze' ? 'Analysing…' : 'Analyse only'}
        </button>
        <button type="submit" className="btn primary" disabled={!!busy || loading}>
          {busy === 'estimate' ? 'Estimating…' : 'Estimate cost'}
        </button>
      </div>
    </form>
  )
}
