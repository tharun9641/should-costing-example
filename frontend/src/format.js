const moneyCache = new Map()

export function money(value, currency = 'USD') {
  if (value == null) return '—'
  if (!moneyCache.has(currency)) {
    let fmt
    try {
      fmt = new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 })
    } catch {
      fmt = { format: (v) => `${v.toFixed(2)} ${currency}` }
    }
    moneyCache.set(currency, fmt)
  }
  return moneyCache.get(currency).format(value)
}

export function num(value, digits = 2) {
  if (value == null) return '—'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })
}

export function pct(part, whole) {
  if (!whole) return '—'
  return `${((part / whole) * 100).toFixed(1)}%`
}

export const SHAPE_LABELS = {
  rotational: 'Rotational (turned)',
  prismatic: 'Prismatic (milled)',
  sheet: 'Sheet metal',
  unknown: 'Unknown',
}
