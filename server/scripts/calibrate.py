"""Run real supplier quotes through the cost engine and suggest location factors.

    .venv/bin/python scripts/calibrate.py             # report only
    .venv/bin/python scripts/calibrate.py --apply     # also write the suggested factors

Quotes live in app/data/quotes.json. Per location the script fits the material
price factor m and machine-rate multiplier k that minimise the squared percentage
error. The unit price is linear in both:

    price = (1 + profit) * (m * material + (1 + overhead) * (labour + k * machine))

With one quote only k is fitted (m stays 1). --apply scales material_price_factor
by m, and machine_rate_factor and every machine_rate_override by k; machines priced
by a complete cost build-up are not changed (fix their inputs instead).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.config import settings  # noqa: E402
from app.schemas import PartFeatures  # noqa: E402
from app.services import catalog, cost_engine, machine_selector  # noqa: E402
from app.services.cad_parser import parse_cad  # noqa: E402
from app.services.pdf_parser import parse_pdf  # noqa: E402


def _features(q: dict) -> PartFeatures:
    if "features" in q:
        return PartFeatures(source="cad", file_name=q.get("part", "quote"), **q["features"])
    path = SERVER / q["part"]
    data, ext = path.read_bytes(), path.suffix.lower()
    return parse_pdf(data, path.name) if ext == ".pdf" else parse_cad(data, path.name, ext, q.get("units"))


def _to_usd(amount: float, currency: str) -> float:
    if currency == "USD":
        return amount
    loc = next((l for l in catalog.all_locations() if l.currency == currency), None)
    if loc is None:
        raise ValueError(f"No location uses currency {currency}; quote the price in USD")
    return amount / loc.fx_per_usd


def price_quote(q: dict) -> dict:
    f = _features(q)
    f.material_id = catalog.get_material(q["material_id"]).id
    if q.get("machine_ids"):
        routing = machine_selector.routing_from_ids(q["machine_ids"].split(","))
    else:
        routing = machine_selector.select_machines(f)
    loc = catalog.get_location(q["location_id"])
    e = cost_engine.estimate(f, routing, catalog.get_material(f.material_id), loc, int(q["quantity"]))
    c = e.cost
    quoted = _to_usd(float(q["unit_price"]), q["currency"])
    return {
        "quote": q, "location": loc, "tool_usd": c.unit_price_usd, "quoted_usd": quoted,
        "error_pct": (c.unit_price_usd - quoted) / quoted * 100,
        "material": c.material_cost_usd, "labour": c.labour_cost_usd, "machine": c.machine_cost_usd,
        "overhead": c.overhead_usd, "profit": c.profit_usd,
    }


def fit(rows: list[dict]) -> tuple[float, float]:
    """(m, k) minimising Σ((price - quoted) / quoted)² for one location's quotes."""
    loc = rows[0]["location"]
    p, oh = 1 + loc.profit_pct / 100, 1 + loc.overhead_pct / 100
    a = np.array([[p * r["material"] / r["quoted_usd"], p * oh * r["machine"] / r["quoted_usd"]] for r in rows])
    b = np.array([1 - p * oh * r["labour"] / r["quoted_usd"] for r in rows])
    if len(rows) == 1 or np.linalg.matrix_rank(a) < 2:
        if not a[:, 1].any():
            return 1.0, 1.0
        k = float(np.dot(a[:, 1], b - a[:, 0]) / np.dot(a[:, 1], a[:, 1]))  # m fixed at 1
        return 1.0, k
    m, k = np.linalg.lstsq(a, b, rcond=None)[0]
    return float(m), float(k)


def _predicted(r: dict, m: float, k: float) -> float:
    loc = r["location"]
    return (1 + loc.profit_pct / 100) * (
        m * r["material"] + (1 + loc.overhead_pct / 100) * (r["labour"] + k * r["machine"])
    )


def run(quotes: list[dict], out=print) -> dict[str, dict]:
    rows = [price_quote(q) for q in quotes]
    out(f"{'location':13} {'supplier':18} {'qty':>6} {'tool USD':>10} {'quoted USD':>10} {'error':>8}"
        f"  material / labour / machine / overhead / profit (USD per unit)")
    for r in rows:
        q = r["quote"]
        out(f"{q['location_id']:13} {q.get('supplier', '')[:18]:18} {q['quantity']:>6} "
            f"{r['tool_usd']:>10.2f} {r['quoted_usd']:>10.2f} {r['error_pct']:>+7.1f}%  "
            f"{r['material']:.2f} / {r['labour']:.2f} / {r['machine']:.2f} / {r['overhead']:.2f} / {r['profit']:.2f}")

    suggestions = {}
    for loc_id in sorted({r["quote"]["location_id"].upper() for r in rows}):
        group = [r for r in rows if r["quote"]["location_id"].upper() == loc_id]
        loc = group[0]["location"]
        m, k = fit(group)
        before = np.mean([abs(r["error_pct"]) for r in group])
        after = np.mean([abs(_predicted(r, m, k) - r["quoted_usd"]) / r["quoted_usd"] * 100 for r in group])
        suggestions[loc_id] = {"m": m, "k": k}
        out(f"\n{loc_id}: {len(group)} quote(s), mean |error| {before:.1f}% -> {after:.1f}% with")
        out(f"  material_price_factor {loc.material_price_factor:g} -> {loc.material_price_factor * m:.4g}"
            f"   (x{m:.3f}{', not fitted: needs 2+ quotes' if len(group) == 1 else ''})")
        out(f"  machine rates x{k:.3f}: machine_rate_factor {loc.machine_rate_factor:g} -> "
            f"{loc.machine_rate_factor * k:.4g}, overrides scaled alike")
        if m <= 0 or k <= 0:
            out("  warning: a non-positive multiplier means the quotes disagree with the model; check them")
    return suggestions


def apply(suggestions: dict[str, dict], path: Path) -> None:
    """Scale factors in locations.json in place, leaving the rest of the file untouched."""
    text = path.read_text()
    for loc_id, s in suggestions.items():
        start = text.index(f'"id": "{loc_id}"')
        nxt = text.find('"id": "', start + 1)
        end = len(text) if nxt == -1 else nxt
        block = text[start:end]

        def scale(key, mult, b):
            return re.sub(rf'("{key}": )([\d.]+)', lambda mm: f"{mm.group(1)}{round(float(mm.group(2)) * mult, 4)}", b)

        block = scale("material_price_factor", s["m"], block)
        block = scale("machine_rate_factor", s["k"], block)
        block = scale("rate_usd_per_hour", s["k"], block)  # only machine_rate_overrides use this key
        text = text[:start] + block + text[end:]
    json.loads(text)
    path.write_text(text)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the suggested factors to locations.json")
    ap.add_argument("--quotes", type=Path, default=Path(settings.data_dir) / "quotes.json")
    args = ap.parse_args(argv)
    quotes = json.loads(args.quotes.read_text())["quotes"]
    if not quotes:
        print(f"No quotes in {args.quotes}; add some (see README 'Calibration').")
        return
    suggestions = run(quotes)
    if args.apply:
        apply(suggestions, Path(settings.data_dir) / "locations.json")
        print("\nWritten to locations.json.")
    else:
        print("\nNothing written (run with --apply to write).")


if __name__ == "__main__":
    main()
