import io
import os
from pathlib import Path

os.environ["SC_LIVE_RATES"] = "off"  # tests never hit the network; see the live_feeds fixture

import pytest
import trimesh
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal one-page PDF with the given text lines (Helvetica)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 10 Tf 40 800 Td 14 TL\n" + "".join(
        f"({esc(line)}) Tj T*\n" for line in lines
    ) + "ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def mesh_bytes(mesh: trimesh.Trimesh) -> bytes:
    return mesh.export(file_type="stl")


@pytest.fixture
def block_with_holes_stl() -> bytes:
    """100 x 60 x 20 mm block with two Ø10 through-holes."""
    block = trimesh.creation.box(extents=[100, 60, 20])
    holes = [
        trimesh.creation.cylinder(radius=5, height=30, sections=32).apply_translation([x, 0, 0])
        for x in (-30, 30)
    ]
    return mesh_bytes(block.difference(trimesh.util.concatenate(holes)))


@pytest.fixture
def shaft_stl() -> bytes:
    """Ø20 x 120 mm shaft."""
    return mesh_bytes(trimesh.creation.cylinder(radius=10, height=120, sections=64))


@pytest.fixture
def plate_stl() -> bytes:
    """2 mm flat sheet, 200 x 150 mm."""
    return mesh_bytes(trimesh.creation.box(extents=[200, 150, 2]))


@pytest.fixture
def drawing_pdf() -> bytes:
    return make_pdf([
        "TITLE: MOUNTING BRACKET   DWG NO. 12-345",
        "MATERIAL: AL 6061-T6",
        "UNLESS OTHERWISE SPECIFIED DIMENSIONS IN MM",
        "GENERAL TOLERANCE ±0.1",
        "OVERALL 120.0 x 80.0 x 25.0",
        "4X Ø6.6 THRU",
        "2X M5x0.8 TAPPED",
        "BORE Ø22 H7 +0.021/-0.000",
        "SURFACE FINISH Ra 1.6",
    ])


@pytest.fixture
def step_file() -> bytes:
    pts = [(0, 0, 0), (80, 0, 0), (80, 50, 0), (0, 50, 10), (40, 25, 10)]
    lines = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION((''),'2;1');",
        "ENDSEC;",
        "DATA;",
        "#1=PRODUCT('BRACKET','BRACKET SS304','',(#2));",
        "#3=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));",
    ]
    for i, p in enumerate(pts, start=10):
        lines.append(f"#{i}=CARTESIAN_POINT('',({p[0]:.1f},{p[1]:.1f},{p[2]:.1f}));")
        lines.append(f"#{i + 500}=VERTEX_POINT('',#{i});")
    for i in range(30, 42):
        lines.append(f"#{i}=ADVANCED_FACE('',(#1),#2,.T.);")
        lines.append(f"#{i + 100}=PLANE('',#1);")
    lines += ["#200=CYLINDRICAL_SURFACE('',#1,3.0);"] * 4
    lines += ["ENDSEC;", "END-ISO-10303-21;"]
    return "\n".join(lines).encode()


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def no_ocp(monkeypatch):
    """Force the STEP text-parser fallback, as on a server without cadquery-ocp."""
    from app.services import cad_parser

    monkeypatch.setattr(cad_parser, "_read_brep", lambda data, notes: None)


def cylinder_stl(sections: int) -> bytes:
    """Ø50 x 120 mm cylinder."""
    return mesh_bytes(trimesh.creation.cylinder(radius=25, height=120, sections=sections))


def block_3holes_stl(segments: int) -> bytes:
    """120 x 80 x 25 mm block with three Ø10 through-holes."""
    holes = trimesh.util.concatenate([
        trimesh.creation.cylinder(radius=5, height=40, sections=segments).apply_translation([x, 0, 0])
        for x in (-30, 0, 30)
    ])
    return mesh_bytes(trimesh.creation.box(extents=[120, 80, 25]).difference(holes))


def square_plate_4holes_stl() -> bytes:
    """100 x 100 x 30 mm plate with four Ø22 through-holes."""
    holes = trimesh.util.concatenate([
        trimesh.creation.cylinder(radius=11, height=40, sections=48).apply_translation([x, y, 0])
        for x in (-25, 25) for y in (-25, 25)
    ])
    return mesh_bytes(trimesh.creation.box(extents=[100, 100, 30]).difference(holes))


LIVE_FIXTURES = FIXTURES / "live"


def recorded_response(url: str) -> bytes:
    """Recorded body for a live-data URL (tests/fixtures/live)."""
    if "frankfurter" in url:
        return (LIVE_FIXTURES / "frankfurter_usd.json").read_bytes()
    if "fredgraph.csv?id=" in url:
        return (LIVE_FIXTURES / f"fred_{url.rsplit('=', 1)[1]}.csv").read_bytes()
    raise AssertionError(f"no recorded response for {url}")


@pytest.fixture
def live_feeds(monkeypatch):
    """Live rates on, served from recorded responses, feeds emptied before and after."""
    from app.services import live

    monkeypatch.setattr(live, "enabled", lambda: True)
    monkeypatch.setattr(live, "_http_get", recorded_response)

    def reset():
        for feed in live.FEEDS:
            feed.data, feed.fetched_at, feed.error, feed._refreshing = {}, None, None, False
            feed._retry_at = 0.0

    reset()
    yield live
    reset()
