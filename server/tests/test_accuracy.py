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
