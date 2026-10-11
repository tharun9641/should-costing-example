"""Regression tests for the accuracy review (reference parts in tests/fixtures)."""

import pytest

from tests.conftest import fixture_bytes


def analyze(client, name, data=None, **form):
    r = client.post("/api/analyze", files={"file": (name, data or fixture_bytes(name))}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- STEP: exact geometry with OpenCascade ----------

@pytest.fixture
def with_ocp():
    pytest.importorskip("OCP", reason="cadquery-ocp not installed")


def test_step_cylinder_is_turned_not_laser_cut(client, with_ocp):
    body = analyze(client, "cylinder_d50x120.step")
    f = body["features"]
    assert f["bbox_mm"] == pytest.approx([50, 50, 120], abs=0.01)
    assert f["shape"] == "rotational"
    assert f["confidence"] == "high"
    assert body["routing"][0]["process"] == "turning"


def test_step_disc_envelope(client, with_ocp):
    f = analyze(client, "disc_d100x20.step")["features"]
    assert f["bbox_mm"] == pytest.approx([20, 100, 100], abs=0.01)


def test_step_block_holes_and_thickness(client, with_ocp):
    """Each OCC hole is a single cylindrical face; axis origins lie outside the solid."""
    f = analyze(client, "block_120x80x25_3holes.step")["features"]
    assert f["bbox_mm"] == pytest.approx([25, 80, 120], abs=0.01)
    assert f["hole_count"] == 3


@pytest.mark.parametrize("name, volume_cm3", [
    ("box_100x60x40.step", 240.0),
    ("housing_150x100x50.step", 150 * 100 * 50 / 1000 - 130 * 80 * 40 / 1000),
])
def test_step_volume_is_exact(client, with_ocp, name, volume_cm3):
    assert analyze(client, name)["features"]["volume_cm3"] == pytest.approx(volume_cm3, rel=0.001)


# ---------- STEP: text fallback without OpenCascade ----------

def test_step_fallback_envelope_from_vertices_and_edges(client, no_ocp):
    f = analyze(client, "cylinder_d50x120.step")["features"]
    assert f["bbox_mm"] == pytest.approx([50, 50, 120], abs=0.01)
    assert f["shape"] == "rotational"
    assert f["confidence"] == "low"
    assert any("estimated" in n for n in f["notes"])


def test_step_fallback_ignores_axis_origins_and_counts_single_face_holes(client, no_ocp):
    f = analyze(client, "block_120x80x25_3holes.step")["features"]
    assert f["bbox_mm"] == pytest.approx([25, 80, 120], abs=0.01)
    assert f["hole_count"] == 3
    assert min(f["bbox_mm"]) > 0


# ---------- PDF envelope ----------

TITLE_BLOCK = [
    "MATERIAL: SS 304", "DATE 2026-10-11", "DRG NO 4521 REV 3", "SHEET 1 OF 1",
    "GENERAL TOL ISO 2768 m", "WEIGHT 0.5 KG", "90", "60", "12",
]


def test_pdf_envelope_ignores_title_block_numbers():
    from app.services.pdf_parser import parse_drawing_text

    f = parse_drawing_text("\n".join(TITLE_BLOCK))
    assert f.bbox_mm == [12, 60, 90]  # was 304 x 2026 x 2768
    assert f.material_id == "SS304"


def test_pdf_title_block_prices_sensibly(client):
    from tests.conftest import make_pdf

    r = client.post(
        "/api/estimate",
        files={"file": ("plate.pdf", make_pdf(TITLE_BLOCK), "application/pdf")},
        data={"location_id": "IN-CHENNAI", "quantity": "100"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["cost"]["unit_price_local"] < 5000  # was ~9.57 million INR


def test_pdf_implausible_envelope_asks_for_overrides(client):
    from tests.conftest import make_pdf

    pdf = make_pdf(["MATERIAL: AL 6061", "PLATE 600 x 500", "CHAMFER 1", "NOTE 2"])
    f = analyze(client, "odd.pdf", pdf)["features"]
    assert f["bbox_mm"] is None
    assert any("overrides" in n for n in f["notes"])


# ---------- Complexity independent of tessellation ----------

def _cx_and_route(client, name, data):
    body = analyze(client, name, data)
    return body["features"]["complexity"], tuple(s["machine_id"] for s in body["routing"])


@pytest.mark.parametrize("make, resolutions, step", [
    ("cylinder_stl", (32, 128, 256), "cylinder_d50x120.step"),
    ("block_3holes_stl", (16, 32, 128), "block_120x80x25_3holes.step"),
])
def test_complexity_and_routing_ignore_mesh_resolution(client, make, resolutions, step):
    from tests import conftest

    results = {_cx_and_route(client, f"{make}_{n}.stl", getattr(conftest, make)(n)) for n in resolutions}
    assert len(results) == 1, results  # was medium/high and lathe vs lathe+mill, 3- vs 5-axis
    pytest.importorskip("OCP")
    assert _cx_and_route(client, step, fixture_bytes(step)) in results


# ---------- Shape from geometry, not the envelope ----------

def test_square_plate_with_holes_is_milled_not_turned(client):
    """Two equal envelope sides and a low fill ratio used to mean 'rotational'."""
    from tests.conftest import square_plate_4holes_stl

    body = analyze(client, "plate.stl", square_plate_4holes_stl())
    assert body["features"]["shape"] == "prismatic"
    assert body["routing"][0]["process"] == "milling"


@pytest.mark.parametrize("ocp_mode", ["ocp", "fallback"])
def test_square_plate_step_is_prismatic(client, monkeypatch, ocp_mode):
    if ocp_mode == "ocp":
        pytest.importorskip("OCP")
    else:
        from app.services import cad_parser
        monkeypatch.setattr(cad_parser, "_read_brep", lambda data, notes: None)
    assert analyze(client, "plate_100x100x30_4holes.step")["features"]["shape"] == "prismatic"
    assert analyze(client, "disc_d100x20.step")["features"]["shape"] == "rotational"


# ---------- Mesh units ----------

def test_mesh_units_inch(client):
    import trimesh

    from tests.conftest import mesh_bytes

    stl = mesh_bytes(trimesh.creation.box(extents=[4, 2, 1]))  # a 4 x 2 x 1 inch block
    f = analyze(client, "block.stl", stl)["features"]
    assert f["bbox_mm"] == pytest.approx([1, 2, 4])
    assert any("inch" in n for n in f["notes"])

    f = analyze(client, "block.stl", stl, units="inch")["features"]
    assert f["bbox_mm"] == pytest.approx([25.4, 50.8, 101.6])
    assert f["volume_cm3"] == pytest.approx(8 * 2.54**3, rel=1e-4)
    assert f["units_detected"] == "inch"


def test_mesh_units_rejects_unknown(client, shaft_stl):
    r = client.post("/api/analyze", files={"file": ("s.stl", shaft_stl)}, data={"units": "ft"})
    assert r.status_code == 422


# ---------- Rate data ----------

def _all_in_inr_per_hour(client, machine_id, location_id="IN-CHENNAI"):
    """Machine + operator, plus overhead and profit, for one machine hour."""
    loc = next(l for l in client.get("/api/locations").json() if l["id"] == location_id)
    m = next(m for m in client.get("/api/machines").json() if m["id"] == machine_id)
    rate = client.get("/api/rates/machine", params={"machine_id": machine_id, "location_id": location_id}).json()
    conversion = rate["rate_usd_per_hour"] + m["operator_ratio"] * loc["labour_rates"][m["operator_skill"]]
    return conversion * (1 + loc["overhead_pct"] / 100) * (1 + loc["profit_pct"] / 100) * loc["fx_per_usd"]


@pytest.mark.parametrize("location_id", ["IN-CHENNAI", "IN-PUNE"])
@pytest.mark.parametrize("machine_id, lo, hi", [("CNC_MILL_3AX", 750, 1040), ("CNC_MILL_5AX", 3240, 3900)])
def test_india_machine_hour_within_published_range(client, location_id, machine_id, lo, hi):
    assert lo <= _all_in_inr_per_hour(client, machine_id, location_id) <= hi


def test_inr_fx_and_rate_provenance(client):
    body = client.get("/api/rates/IN-CHENNAI").json()
    assert body["location"]["fx_per_usd"] == 96.8
    for kind in ("materials", "labour", "machines"):
        assert all(r["source"] and r["as_of"] for r in body[kind]), kind
    lathe = next(r for r in body["machines"] if r["machine_id"] == "CNC_LATHE")
    assert "turning" in lathe["source"]
    others = [l for l in client.get("/api/locations").json() if l["currency"] not in ("INR", "USD")]
    assert all("Unverified" in l["fx_source"] for l in others)


# ---------- Bends and bar stock ----------

def test_single_bend_counted_from_step(client):
    pytest.importorskip("OCP")
    body = analyze(client, "l_bracket_1bend.step")
    assert body["features"]["shape"] == "sheet"
    assert body["features"]["bend_count"] == 1  # was assumed 2
    assert "1 bend" in next(s["reason"] for s in body["routing"] if s["machine_id"] == "PRESS_BRAKE")


def test_mesh_bend_count_is_flagged_as_assumed(client):
    import trimesh

    from tests.conftest import mesh_bytes

    l_bracket = trimesh.creation.box(extents=[50, 40, 2]).apply_translation([25, 0, 1]).union(
        trimesh.creation.box(extents=[2, 40, 30]).apply_translation([1, 0, 17])
    )
    f = analyze(client, "bracket.stl", mesh_bytes(l_bracket))["features"]
    assert f["shape"] == "sheet"
    assert any("assumed" in n for n in f["notes"])


def test_hex_bar_is_turned_square_bar_is_milled(client):
    import numpy as np
    import trimesh

    from tests.conftest import mesh_bytes

    hex_bar = trimesh.creation.cylinder(radius=30 / np.sqrt(3), height=100, sections=6)  # 30 A/F
    body = analyze(client, "hex.stl", mesh_bytes(hex_bar))
    assert body["features"]["shape"] == "rotational"
    assert body["routing"][0]["machine_id"] == "CNC_LATHE"  # Ø34.6 across corners > Swiss limit
    square = analyze(client, "square.stl", mesh_bytes(trimesh.creation.box(extents=[30, 30, 100])))
    assert square["features"]["shape"] == "prismatic"
    pytest.importorskip("OCP")
    assert analyze(client, "hex_af30x100.step")["features"]["shape"] == "rotational"
