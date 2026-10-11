import pytest

from app.services import catalog
from app.services.pdf_parser import parse_drawing_text


# ---------- Catalog & rates ----------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_lists(client):
    assert any(l["id"] == "IN-CHENNAI" for l in client.get("/api/locations").json())
    assert any(m["id"] == "AL6061" for m in client.get("/api/materials").json())
    assert any(m["id"] == "CNC_LATHE" for m in client.get("/api/machines").json())


def test_material_rate_is_location_adjusted(client):
    r = client.get("/api/rates/material", params={"material_id": "SS304", "location_id": "US-MICHIGAN"})
    assert r.status_code == 200
    body = r.json()
    assert body["price_usd_per_kg"] == pytest.approx(4.0 * 1.1)
    assert body["currency"] == "USD"


def test_labour_rate(client):
    r = client.get("/api/rates/labour", params={"location_id": "in-chennai", "skill": "operator"})
    assert r.status_code == 200
    assert r.json()["rate_local_per_hour"] == pytest.approx(3.5 * 96.8)


def test_unknown_location_is_404(client):
    r = client.get("/api/rates/labour", params={"location_id": "XX-NOWHERE"})
    assert r.status_code == 404


def test_all_rates_for_location(client):
    body = client.get("/api/rates/DE-STUTTGART").json()
    assert len(body["materials"]) == len(catalog.materials())
    assert {l["skill"] for l in body["labour"]} >= {"operator", "machinist"}


# ---------- PDF parsing ----------

def test_parse_drawing_text():
    f = parse_drawing_text(
        "MATERIAL: SS 316L\nDIMENSIONS IN MM\n±0.05\n+0.01/-0.00\n"
        "4X Ø8 THRU\nM6x1\nRa 0.4\nOVERALL 150 x 40 x 30\n"
    )
    assert f.material_id == "SS316"
    assert f.tightest_tolerance_mm == pytest.approx(0.005)
    assert f.best_surface_finish_ra_um == 0.4
    assert f.thread_count == 1
    assert f.hole_count == 5
    assert f.bbox_mm == [30, 40, 150]


def test_material_alias_needs_whole_word():
    assert catalog.match_material("ABSOLUTE DIMENSIONS") is None
    assert catalog.match_material("MATL: ABS NATURAL")[0] == "ABS"


def test_analyze_pdf(client, drawing_pdf):
    r = client.post("/api/analyze", files={"file": ("bracket.pdf", drawing_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    f = r.json()["features"]
    assert f["material_id"] == "AL6061"
    assert f["hole_count"] == 6  # 4 drilled + 2 tapped
    assert f["thread_count"] == 2
    assert f["bbox_mm"] == [25, 80, 120]
    assert r.json()["routing"][0]["machine_id"] == "CNC_MILL_3AX"


# ---------- CAD parsing ----------

def test_analyze_block_stl(client, block_with_holes_stl):
    r = client.post("/api/analyze", files={"file": ("block.stl", block_with_holes_stl)})
    assert r.status_code == 200, r.text
    f = r.json()["features"]
    assert f["bbox_mm"] == pytest.approx([20, 60, 100], abs=0.01)
    assert f["shape"] == "prismatic"
    assert f["hole_count"] == 2
    expected_vol = (100 * 60 * 20 - 2 * 3.14159 * 25 * 20) / 1000
    assert f["volume_cm3"] == pytest.approx(expected_vol, rel=0.02)
    assert r.json()["routing"][0]["process"] == "milling"


def test_analyze_shaft_stl_picks_swiss_lathe(client, shaft_stl):
    r = client.post("/api/analyze", files={"file": ("shaft.stl", shaft_stl)})
    body = r.json()
    assert body["features"]["shape"] == "rotational"
    assert body["routing"][0]["machine_id"] == "SWISS_LATHE"


def test_analyze_plate_stl_is_sheet(client, plate_stl):
    body = client.post("/api/analyze", files={"file": ("plate.stl", plate_stl)}).json()
    assert body["features"]["shape"] == "sheet"
    assert body["routing"][0]["machine_id"] == "LASER_CUTTER"
    assert all(s["machine_id"] != "PRESS_BRAKE" for s in body["routing"])


def test_analyze_step(client, step_file):
    r = client.post("/api/analyze", files={"file": ("bracket.step", step_file)})
    assert r.status_code == 200, r.text
    f = r.json()["features"]
    assert f["material_id"] == "SS304"
    assert f["bbox_mm"] == [10, 50, 80]
    assert f["face_count"] == 12
    assert f["hole_count"] == 2


def test_unsupported_extension(client):
    r = client.post("/api/analyze", files={"file": ("part.dwg", b"xx")})
    assert r.status_code == 415


# ---------- Estimate ----------

def test_estimate_stl_requires_material(client, block_with_holes_stl):
    r = client.post(
        "/api/estimate",
        files={"file": ("block.stl", block_with_holes_stl)},
        data={"location_id": "IN-CHENNAI"},
    )
    assert r.status_code == 422
    assert "material_id" in r.json()["detail"]


def test_estimate_breakdown_adds_up(client, block_with_holes_stl):
    r = client.post(
        "/api/estimate",
        files={"file": ("block.stl", block_with_holes_stl)},
        data={"location_id": "IN-CHENNAI", "material_id": "AL6061", "quantity": "100"},
    )
    assert r.status_code == 200, r.text
    e = r.json()
    c = e["cost"]
    parts = sum(c[k] for k in ("material_cost_usd", "labour_cost_usd", "machine_cost_usd",
                               "overhead_usd", "profit_usd"))
    assert c["unit_price_usd"] == pytest.approx(parts, abs=0.001)
    assert c["total_price_usd"] == pytest.approx(c["unit_price_usd"] * 100, abs=0.01)
    assert c["currency"] == "INR"
    # stock block 105 x 65 x 25 mm of aluminium
    assert e["stock_mass_kg"] == pytest.approx(105 * 65 * 25 * 2.7e-6, rel=0.01)


def test_location_changes_cost(client, block_with_holes_stl):
    def unit(loc):
        r = client.post(
            "/api/estimate",
            files={"file": ("block.stl", block_with_holes_stl)},
            data={"location_id": loc, "material_id": "AL6061", "quantity": "50"},
        )
        return r.json()["cost"]["labour_cost_usd"]

    assert unit("US-MICHIGAN") > unit("IN-CHENNAI") * 3


def test_estimate_pdf_with_forced_routing(client, drawing_pdf):
    r = client.post(
        "/api/estimate",
        files={"file": ("bracket.pdf", drawing_pdf, "application/pdf")},
        data={"location_id": "DE-STUTTGART", "quantity": "10",
              "machine_ids": "CNC_MILL_5AX,SURFACE_GRINDER"},
    )
    assert r.status_code == 200, r.text
    ops = [o["machine_id"] for o in r.json()["operations"]]
    assert ops == ["CNC_MILL_5AX", "SURFACE_GRINDER"]


def test_estimate_scanned_pdf_needs_dims(client):
    from tests.conftest import make_pdf
    r = client.post(
        "/api/estimate",
        files={"file": ("scan.pdf", make_pdf([]), "application/pdf")},
        data={"location_id": "IN-PUNE", "material_id": "AISI4140"},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/estimate",
        files={"file": ("scan.pdf", make_pdf([]), "application/pdf")},
        data={"location_id": "IN-PUNE", "material_id": "AISI4140",
              "length_mm": "50", "width_mm": "50", "height_mm": "200", "shape": "rotational"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["routing"][0]["machine_id"] == "CNC_LATHE"


# ---------- Robustness ----------

def test_zero_envelope_dimension_is_rejected_before_costing():
    """[0, 25, 120] (the old STEP reading of a cylinder) used to divide by zero."""
    from app.schemas import PartFeatures
    from app.services import cost_engine, machine_selector

    f = PartFeatures(source="cad", file_name="c.step", bbox_mm=[0, 25, 120], shape="sheet")
    with pytest.raises(ValueError, match="zero dimension"):
        cost_engine.estimate(
            f, machine_selector.select_machines(f), catalog.get_material("AL6061"),
            catalog.get_location("IN-CHENNAI"), 100,
        )


def test_unexpected_costing_error_is_422(client, block_with_holes_stl, monkeypatch):
    from app.services import cost_engine

    def boom(*_):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(cost_engine, "estimate", boom)
    r = client.post(
        "/api/estimate",
        files={"file": ("block.stl", block_with_holes_stl)},
        data={"location_id": "IN-CHENNAI", "material_id": "AL6061"},
    )
    assert r.status_code == 422
    assert "ZeroDivisionError" in r.json()["detail"]
