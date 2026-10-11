// Thin client for the FastAPI backend. In dev, Vite proxies /api to the server;
// set VITE_API_URL to call a server on another origin instead.
const BASE = import.meta.env.VITE_API_URL ?? ''

function errorMessage(body, status) {
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => `${d.loc?.slice(1).join('.') || 'request'}: ${d.msg}`).join('; ')
  }
  return `Request failed (${status})`
}

async function request(path, options) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, options)
  } catch {
    throw new Error('Cannot reach the costing server. Is it running on port 8000?')
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(errorMessage(body, res.status))
  return body
}

export const getLocations = () => request('/api/locations')
export const getMaterials = () => request('/api/materials')
export const getMachines = () => request('/api/machines')
export const getRates = (locationId) => request(`/api/rates/${encodeURIComponent(locationId)}`)

function toFormData(fields) {
  const fd = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null || value === '') continue
    fd.append(key, value)
  }
  return fd
}

export const analyzePart = (fields) =>
  request('/api/analyze', { method: 'POST', body: toFormData(fields) })

export const estimatePart = (fields) =>
  request('/api/estimate', { method: 'POST', body: toFormData(fields) })
