# Should-Costing API (FastAPI)

Estimates the cost of a manufactured part from a **PDF drawing** or **CAD model**:
material cost and labour cost for a selected location, plus the machines needed to make it.

## Run

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs · Tests: `.venv/bin/pytest`

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/locations` | Supported locations with labour rates, currency, cost factors |
| GET | `/api/materials` | Material catalogue (density, base price, machinability) |
| GET | `/api/machines` | Machine catalogue (hourly rate, setup, removal rates) |
| GET | `/api/rates/material?material_id=&location_id=` | Material price/kg at a location |
| GET | `/api/rates/labour?location_id=&skill=` | Labour rate/hour (`operator`, `machinist`, `programmer`, `inspector`) |
| GET | `/api/rates/machine?machine_id=&location_id=` | Machine rate/hour at a location |
| GET | `/api/rates/{location_id}` | All rates for one location |
| POST | `/api/analyze` | Upload a file → extracted features + recommended machines |
| POST | `/api/estimate` | Upload a file + `location_id` + `quantity` → full cost breakdown |

`/analyze` and `/estimate` take `multipart/form-data` with `file` and optional overrides:
`material_id`, `length_mm`/`width_mm`/`height_mm`, `shape` (`rotational|prismatic|sheet`),
and on `/estimate`, `machine_ids` (comma-separated) to force a routing.

```bash
curl -X POST localhost:8000/api/estimate \
  -F file=@part.step -F location_id=IN-CHENNAI -F quantity=100 -F material_id=AL6061
```

## How it works

```
app/
  main.py                  FastAPI app, CORS (Vite dev server allowed by default)
  routers/catalog.py       Location / material / machine / rate lookups
  routers/analysis.py      /analyze and /estimate (upload handling, overrides)
  services/catalog.py      Loads data/*.json, applies location factors
  services/pdf_parser.py   Drawing text → material, tolerances, Ra, holes, threads, bends, envelope
  services/cad_parser.py   STL/OBJ/PLY/3MF/GLB (trimesh: exact volume, area, holes via genus)
                           STEP (text parse: units, bbox, face types, material in product data)
  services/machine_selector.py  Rules: sheet → laser (+ press brake), rotational → lathe/Swiss,
                           prismatic → 3- or 5-axis mill, tight tol / fine Ra → grinding
  services/cost_engine.py  Stock mass, cycle times, setup/programming amortisation,
                           overhead and profit → unit and total price (USD + local currency)
  data/*.json              Rate tables — edit these to update prices
```

### Limitations

- **Rate data is indicative seed data** (`app/data/*.json`). Swap in your own benchmarks or
  wire a live feed into `services/catalog.py` (the only module that reads rates).
- **PDF parsing reads the text layer only.** Scanned drawings return low confidence; pass
  overrides. The part envelope is guessed from the largest dimension values — verify it.
- **STEP volume/area are estimated** from the envelope; mesh formats give exact values.
  IGES, DXF and native CAD formats (SLDPRT, etc.) are not supported.
