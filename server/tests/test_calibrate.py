"""scripts/calibrate.py: fit location factors to quotes."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate.py"
spec = importlib.util.spec_from_file_location("calibrate", SCRIPT)
calibrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrate)

BLOCK = {"bbox_mm": [40, 60, 100], "shape": "prismatic", "volume_cm3": 240, "surface_area_cm2": 248}
PLATE = {"bbox_mm": [30, 150, 200], "shape": "prismatic", "volume_cm3": 700, "surface_area_cm2": 900,
         "hole_count": 12, "complexity": "high"}


def quotes_priced_with(m, k):
    """Quotes whose prices equal the tool's at material x m and machine rates x k."""
    out = []
    for feats, mat in ((BLOCK, "AL6061"), (PLATE, "SS316")):
        q = {"features": feats, "material_id": mat, "location_id": "IN-CHENNAI", "quantity": 50,
             "unit_price": 1.0, "currency": "USD", "supplier": "test", "date": "2026-10-12"}
        q["unit_price"] = calibrate._predicted(calibrate.price_quote(q), m, k)
        out.append(q)
    return out


def test_fit_recovers_the_multipliers():
    lines = []
    s = calibrate.run(quotes_priced_with(0.8, 0.5), out=lines.append)
    assert s["IN-CHENNAI"]["m"] == pytest.approx(0.8, rel=1e-6)
    assert s["IN-CHENNAI"]["k"] == pytest.approx(0.5, rel=1e-6)
    assert any("-> 0.0%" in line for line in lines)


def test_single_quote_fits_machine_rates_only():
    s = calibrate.run(quotes_priced_with(1.0, 0.7)[:1], out=lambda _: None)
    assert s["IN-CHENNAI"] == {"m": 1.0, "k": pytest.approx(0.7, rel=1e-6)}


def test_apply_scales_only_that_location(tmp_path):
    from app.config import settings

    path = tmp_path / "locations.json"
    shutil.copy(Path(settings.data_dir) / "locations.json", path)
    calibrate.apply({"IN-CHENNAI": {"m": 0.8, "k": 0.5}}, path)
    locs = {l["id"]: l for l in json.loads(path.read_text())["locations"]}
    orig = {l["id"]: l for l in json.loads((Path(settings.data_dir) / "locations.json").read_text())["locations"]}
    chn = locs["IN-CHENNAI"]
    assert chn["material_price_factor"] == pytest.approx(1.02 * 0.8)
    assert chn["machine_rate_factor"] == pytest.approx(0.55 * 0.5)
    assert chn["machine_rate_overrides"]["CNC_MILL_3AX"]["rate_usd_per_hour"] == pytest.approx(4.5 * 0.5)
    assert locs["IN-PUNE"] == orig["IN-PUNE"]
    assert chn["labour_rates"] == orig["IN-CHENNAI"]["labour_rates"]


def test_report_only_by_default(capsys, tmp_path):
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"quotes": quotes_priced_with(0.9, 0.9)}))
    calibrate.main(["--quotes", str(quotes)])
    assert "Nothing written" in capsys.readouterr().out
