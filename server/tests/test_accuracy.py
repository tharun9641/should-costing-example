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
