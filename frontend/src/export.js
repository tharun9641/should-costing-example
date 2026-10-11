// Client-side export of an estimate: CSV (for spreadsheets) and raw JSON.

function download(name, type, text) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.append(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const baseName = (data) =>
  `estimate-${data.features.file_name.replace(/\.[^.]+$/, '')}-${data.location.id}-q${data.quantity}`

function csvCell(v) {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function estimateToCsv(data) {
  const { cost, location, material, operations, features: f } = data
  const fx = location.fx_per_usd
  const rows = [
    ['Should-cost estimate'],
    ['File', f.file_name],
    ['Location', location.name],
    ['Currency', cost.currency],
    ['Quantity', data.quantity],
    ['Material', material.name],
    ['Stock mass (kg)', data.stock_mass_kg],
    ['Part mass (kg)', data.part_mass_kg],
    [],
    ['Component', `Per unit (${cost.currency})`, 'Per unit (USD)'],
    ['Material', cost.material_cost_usd * fx, cost.material_cost_usd],
    ['Labour', cost.labour_cost_usd * fx, cost.labour_cost_usd],
    ['Machine', cost.machine_cost_usd * fx, cost.machine_cost_usd],
    ['Overhead', cost.overhead_usd * fx, cost.overhead_usd],
    ['Profit', cost.profit_usd * fx, cost.profit_usd],
    ['Unit price', cost.unit_price_local, cost.unit_price_usd],
    ['Total price', cost.total_price_local, cost.total_price_usd],
    [],
    ['Machine', 'Cycle (min)', 'Setup per unit (min)', `Machine cost (${cost.currency})`, `Labour cost (${cost.currency})`],
    ...operations.map((o) => [
      o.machine_name,
      o.cycle_time_min,
      o.setup_time_min,
      o.machine_cost_usd * fx,
      o.labour_cost_usd * fx,
    ]),
    [],
    ['Assumptions'],
    ...data.assumptions.map((a) => [a]),
  ]
  return rows
    .map((r) => r.map((v) => csvCell(typeof v === 'number' ? Math.round(v * 10000) / 10000 : v)).join(','))
    .join('\n')
}

export const downloadCsv = (data) => download(`${baseName(data)}.csv`, 'text/csv', estimateToCsv(data))

export const downloadJson = (data) =>
  download(`${baseName(data)}.json`, 'application/json', JSON.stringify(data, null, 2))
