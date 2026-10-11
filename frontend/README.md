# Should-Costing UI (React + Vite)

Web front end for the [Should-Costing API](../server/README.md): upload a PDF drawing or CAD
model, pick a manufacturing location and quantity, and get material, labour and machine cost
plus the machines needed to make the part.

## Run

Start the API first (see `../server/README.md`), then:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

In dev, Vite proxies `/api` and `/health` to `http://localhost:8000`. Point the proxy elsewhere
with `VITE_API_PROXY=http://host:port npm run dev`, or have the built app call another origin
directly with `VITE_API_URL=https://api.example.com npm run build` (add that origin's UI host
to the server's `SC_CORS_ORIGINS`).

`npm run build` writes static files to `dist/`. `npm run lint` runs ESLint.

## Features

- **Estimate tab**: drop a file (PDF, STEP, STL, OBJ, PLY, OFF, 3MF, GLB), then choose location,
  quantity and material (or let it be detected from the file).
  - *Overrides*: envelope (L × W × H), shape, and a forced machine routing (in click order).
  - *Analyse only*: shows what was extracted from the file and the suggested machines, without pricing.
  - *Estimate cost*: unit and total price in local currency and USD, per-unit cost breakdown,
    material and labour rates, per-machine operations, extraction confidence, notes and assumptions.
  - Export the estimate as CSV or JSON, or print it.
- **Rates tab**: every labour, material and machine rate for a location, with currency, overhead
  and profit.
- Light/dark theme follows the system; the top-bar toggle cycles system → light → dark.

## Layout

```
src/
  api.js                     fetch wrapper for the backend (FastAPI error details → messages)
  format.js                  money / number / percentage formatting
  export.js                  CSV and JSON download of an estimate
  App.jsx                    tabs, catalogue loading, theme
  components/EstimateForm    file drop zone, inputs, overrides, validation
  components/Results         tiles, breakdown, routing, operations, extracted features
  components/CostBar         stacked unit-cost bar with linked legend table
  components/RatesView       per-location rate tables
```
