"""Extract costing-relevant geometry from 3D CAD files.

- Mesh formats (STL, OBJ, PLY, OFF, 3MF, GLB) are loaded with trimesh: exact
  bounding box, volume, surface area, through-hole count (from genus) and planar
  face groups.
- STEP files are read with OpenCascade (`cadquery-ocp`, see services/brep.py) when
  it is installed: exact bounding box, volume, surface area and bores. Without it,
  a text parser reads the envelope from vertices and circular edges and estimates
  volume from the envelope (low confidence).
"""

import io
import math
import re

import numpy as np
import trimesh

from app.schemas import PartFeatures
from app.services.catalog import match_material
from app.services.geometry import nearly_equal, rotational_dims

MESH_EXTS = {".stl", ".obj", ".ply", ".off", ".3mf", ".glb"}
STEP_EXTS = {".step", ".stp"}
SUPPORTED_EXTS = MESH_EXTS | STEP_EXTS
MESH_UNITS_MM = {"mm": 1.0, "inch": 25.4, "m": 1000.0}
SMALL_PART_MM = 10.0  # a mesh smaller than this is probably in inches

SHEET_MAX_THICKNESS_MM = 6.0
# Adjacent mesh triangles bending less than this belong to one smooth surface.
# 35° (not 15°) so a 16-segment hole (22.5° steps) still reads as one bore; real
# edges such as 45° chamfers and hex flats (60°) stay separate.
SMOOTH_ANGLE_RAD = math.radians(35)


class UnsupportedCADError(ValueError):
    pass


def parse_cad(data: bytes, file_name: str, ext: str, units: str | None = None) -> PartFeatures:
    """`units` applies to mesh files, which carry none; STEP files declare their own."""
    if ext in MESH_EXTS:
        return _parse_mesh(data, file_name, ext, units)
    if ext in STEP_EXTS:
        return _parse_step(data, file_name)
    raise UnsupportedCADError(
        f"Unsupported CAD format '{ext}'. Supported: {sorted(SUPPORTED_EXTS)}"
    )


def _complexity(score: int) -> str:
    return "low" if score < 20 else "medium" if score < 80 else "high"


def _part_complexity(surfaces: int, holes: int) -> str:
    """Scored on distinct surfaces + holes, so it does not depend on tessellation."""
    return _complexity(surfaces + 3 * holes)


def _mesh_surface_count(mesh: trimesh.Trimesh) -> int:
    smooth = mesh.face_adjacency[mesh.face_adjacency_angles < SMOOTH_ANGLE_RAD]
    return len(trimesh.graph.connected_components(smooth, nodes=np.arange(len(mesh.faces)), min_len=1))


def _cross2(u: np.ndarray, v: np.ndarray) -> float:
    return float(u[0] * v[1] - u[1] * v[0])


def _convex_hull(pts: np.ndarray) -> np.ndarray:
    """2D convex hull (Andrew's monotone chain), collinear points dropped."""
    pts = np.unique(np.round(pts, 6), axis=0)  # also sorts by x, then y
    if len(pts) < 3:
        return pts
    eps = 1e-9 * float(np.ptp(pts, axis=0).max()) ** 2

    def half(points):
        chain: list[np.ndarray] = []
        for p in points:
            while len(chain) >= 2 and _cross2(chain[-1] - chain[-2], p - chain[-2]) <= eps:
                chain.pop()
            chain.append(p)
        return chain[:-1]

    return np.array(half(pts) + half(pts[::-1]))


def _section_kind(pts2d: np.ndarray) -> str | None:
    """'round' if the cross-section outline is a circle (hull area ≥ 95% of the circle
    through its farthest point: a 12-gon is 95.5%, an octagon 90%, a square 64%);
    'hex' for a regular hexagon (bar stock); otherwise None."""
    hull = _convex_hull(pts2d)
    if len(hull) < 3:
        return None
    centre = (hull.min(axis=0) + hull.max(axis=0)) / 2
    r = float(np.linalg.norm(hull - centre, axis=1).max())
    x, y = hull[:, 0], hull[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
    if area >= 0.95 * math.pi * r * r:
        return "round"
    sides = np.linalg.norm(hull - np.roll(hull, 1, axis=0), axis=1)
    if len(hull) == 6 and np.ptp(sides) <= 0.02 * sides.mean():
        return "hex"
    return None


def _turned_section(points: np.ndarray) -> str | None:
    """'round' or 'hex' if the part's cross-section along some axis is one."""
    # shortcut: assumes the turning axis is a coordinate axis, as exported from CAD;
    # use the principal inertia axes if arbitrarily rotated parts show up.
    kinds = {_section_kind(np.delete(points, k, axis=1)) for k in range(3)}
    return "round" if "round" in kinds else "hex" if "hex" in kinds else None


def _round_envelope(bbox: list[float], diameters) -> bool:
    """An outer diameter equals the envelope's (square) cross-section."""
    a, b, c = bbox
    return any(
        (nearly_equal(a, b) and nearly_equal(d, b)) or (nearly_equal(b, c) and nearly_equal(d, c))
        for d in diameters
    )


def _classify_solid(f: PartFeatures, bbox: list[float], volume_mm3: float | None,
                    area_mm2: float, notes: list[str], section: str | None,
                    count_bends=None) -> None:
    """Shape and bends from exact solid geometry; shared by mesh and B-rep.

    `section` is 'round', 'hex' or None. Rule: round and regular-hex sections are
    turned (hex parts are made from hex bar on a lathe); square bar and everything
    else is milled. `count_bends(t)` counts bends from B-rep data when available.
    """
    # Sheet test: a plate of thickness t has t ≈ 2V/A.
    if volume_mm3:
        t = 2 * volume_mm3 / area_mm2
        if t <= SHEET_MAX_THICKNESS_MM and bbox[2] >= 15 * t:
            f.shape = "sheet"
            notes.append(f"Detected sheet metal, thickness ≈ {t:.2f} mm.")
            if count_bends:
                f.bend_count = count_bends(t)
                if f.bend_count:
                    notes.append(f"{f.bend_count} bend(s) counted from the CAD model.")
            elif bbox[0] > 3 * t:
                f.bend_count = 2
                notes.append(
                    "Part is formed (envelope thicker than sheet); bend count assumed to be 2 "
                    "(a STEP file read with OpenCascade gives the exact count)."
                )
    if f.shape == "unknown":
        f.shape = "rotational" if section else "prismatic"
        if section == "hex":
            notes.append(
                "Hexagonal section: turned from hex bar; stock priced as round bar "
                "over the corners (conservative)."
            )


# ---------- Mesh ----------

def _parse_mesh(data: bytes, file_name: str, ext: str, units: str | None) -> PartFeatures:
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=ext.lstrip("."), force="mesh")
    except Exception as e:  # trimesh raises a variety of errors on bad input
        raise UnsupportedCADError(f"Could not read {ext} file: {e}") from e
    mesh: trimesh.Trimesh = loaded
    if mesh.is_empty:
        raise UnsupportedCADError("CAD file contains no geometry")

    if units:
        mesh.apply_scale(MESH_UNITS_MM[units])
        notes: list[str] = [f"Mesh units set to {units}."]
    else:
        notes = ["Mesh units assumed to be millimetres."]
    bbox = sorted(float(v) for v in mesh.extents)
    if not units and bbox[2] < SMALL_PART_MM:
        notes.append(
            f"Largest dimension is only {bbox[2]:.2f} mm — if the file is in inches, "
            "set units to inch."
        )
    volume_mm3 = abs(float(mesh.volume)) if mesh.is_watertight else None
    area_mm2 = float(mesh.area)

    f = PartFeatures(
        source="cad",
        file_name=file_name,
        units_detected=units or "mm",
        bbox_mm=[round(v, 3) for v in bbox],
        surface_area_cm2=round(area_mm2 / 100, 3),
        confidence="high" if mesh.is_watertight else "medium",
    )

    if volume_mm3 is not None:
        f.volume_cm3 = round(volume_mm3 / 1000, 4)
        # Through-holes ≈ genus for a single closed body: χ = 2 − 2g
        bodies = max(1, len(mesh.split(only_watertight=False)))
        genus = max(0, (2 * bodies - int(mesh.euler_number)) // 2)
        f.hole_count = genus
    else:
        notes.append("Mesh is not watertight; volume will be estimated from the envelope.")

    _classify_solid(f, bbox, volume_mm3, area_mm2, notes, _turned_section(mesh.vertices))

    f.face_count = _mesh_surface_count(mesh)
    f.complexity = _part_complexity(f.face_count, f.hole_count)
    f.notes = notes
    return f


# ---------- STEP ----------

RE_ENTITY = re.compile(r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(([^;]*)\)\s*;", re.DOTALL)
RE_FLOAT = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?")
RE_REF = re.compile(r"#(\d+)")
RE_PRODUCT = re.compile(r"PRODUCT\s*\(\s*'([^']*)'\s*,\s*'([^']*)'", re.IGNORECASE)
RE_MATERIAL_REP = re.compile(
    r"(?:MATERIAL|DESCRIPTIVE_REPRESENTATION_ITEM)[^;]*?'([^']{2,80})'", re.IGNORECASE
)


def _step_units_scale(text: str) -> tuple[float, str]:
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text, re.IGNORECASE):
        return 25.4, "inch"
    if re.search(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.", text):
        return 1.0, "mm"
    if re.search(r"SI_UNIT\s*\(\s*\.CENTI\.\s*,\s*\.METRE\.", text):
        return 10.0, "cm"
    if re.search(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.", text):
        return 1000.0, "m"
    return 1.0, "mm"


def _parse_step(data: bytes, file_name: str) -> PartFeatures:
    text = data.decode("latin-1", errors="replace")
    if "ISO-10303" not in text[:200].upper():
        raise UnsupportedCADError("File does not look like a STEP (ISO-10303-21) file")

    scale, units = _step_units_scale(text)
    notes: list[str] = []
    f = PartFeatures(source="cad", file_name=file_name, units_detected=units)

    # Material from product name/description or a material representation.
    candidates = [" ".join(p) for p in RE_PRODUCT.findall(text)]
    candidates += RE_MATERIAL_REP.findall(text)
    for c in candidates:
        if mat := match_material(c):
            f.material_id, f.material_text = mat[0], c.strip()
            break
    if not f.material_id:
        notes.append("No material found in STEP product data.")

    b = _read_brep(data, notes)
    if b:
        _step_from_brep(b, f, notes)
    else:
        _step_from_text(text, scale, f, notes)
    f.notes = notes
    return f


def _read_brep(data: bytes, notes: list[str]):
    try:
        from app.services import brep  # lazy: OCP is large and optional
    except ImportError:
        return None
    try:
        return brep.read_step(data)
    except Exception as e:  # malformed geometry: fall back to the text parser
        notes.append(f"OpenCascade could not read the solid ({e}); used the text parser.")
        return None


def _step_from_brep(b, f: PartFeatures, notes: list[str]) -> None:
    from app.services import brep

    f.bbox_mm = [round(v, 3) for v in b.bbox]
    f.volume_cm3 = round(b.volume_mm3 / 1000, 4)
    f.surface_area_cm2 = round(b.area_mm2 / 100, 3)
    f.face_count = b.face_count
    f.hole_count = len(brep.full_cylinders(b.cylinders, concave=True))
    f.confidence = "high"
    outer = [2 * g[0].radius for g in brep.full_cylinders(b.cylinders, concave=False)]
    section = "round" if _round_envelope(b.bbox, outer) else _turned_section(b.vertices)
    if section == "round" and _turned_section(b.vertices) == "hex":
        section = "hex"  # e.g. a hex fitting with a turned spigot: the bar is hex
    _classify_solid(f, b.bbox, b.volume_mm3, b.area_mm2, notes, section,
                    count_bends=lambda t: brep.bend_count(b.cylinders, t))
    f.complexity = _part_complexity(b.surface_count, f.hole_count)


def _step_from_text(text: str, scale: float, f: PartFeatures, notes: list[str]) -> None:
    """Fallback without a B-rep kernel: envelope from topology, volume estimated."""
    ents = {int(i): (name, args) for i, name, args in RE_ENTITY.findall(text)}

    def floats(i: int) -> np.ndarray | None:
        e = ents.get(i)
        if not e or e[0] not in ("CARTESIAN_POINT", "DIRECTION"):
            return None
        v = [float(x) for x in RE_FLOAT.findall(e[1].split(",", 1)[1])]
        return np.array(v) if len(v) == 3 else None  # skip 2D (parameter-space) points

    def refs(i: int) -> list[int]:
        return [int(r) for r in RE_REF.findall(ents[i][1])]

    def placement(i: int) -> tuple[np.ndarray, np.ndarray] | None:
        e = ents.get(i)
        if not e or e[0] != "AXIS2_PLACEMENT_3D":
            return None
        r = refs(i)
        loc = floats(r[0]) if r else None
        axis = floats(r[1]) if len(r) > 1 else np.array([0.0, 0.0, 1.0])
        if loc is None or axis is None:
            return None
        return loc * scale, axis / np.linalg.norm(axis)

    # Envelope from vertices plus the extremes of circular edges (a cylinder's
    # only vertices sit on its seam). Surface-axis origins are never used.
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    circle_dias = []
    for i, (name, _) in ents.items():
        if name == "VERTEX_POINT" and (p := floats(refs(i)[0])) is not None:
            lo, hi = np.minimum(lo, p * scale), np.maximum(hi, p * scale)
        elif name == "CIRCLE" and (pl := placement(refs(i)[0])) is not None:
            r = float(RE_FLOAT.findall(ents[i][1].rsplit(",", 1)[1])[0]) * scale
            circle_dias.append(2 * r)
            reach = r * np.sqrt(np.clip(1 - pl[1] ** 2, 0, 1))
            lo, hi = np.minimum(lo, pl[0] - reach), np.maximum(hi, pl[0] + reach)
    if not np.isfinite(lo).all():
        pts = [p for i in ents if (p := floats(i)) is not None and ents[i][0] == "CARTESIAN_POINT"]
        if not pts:
            raise UnsupportedCADError("No geometry found in STEP file")
        notes.append("STEP file has no vertex topology; envelope taken from all points.")
        lo, hi = np.min(pts, axis=0) * scale, np.max(pts, axis=0) * scale
    bbox = sorted(float(v) for v in hi - lo)
    if bbox[0] <= 1e-6:
        raise UnsupportedCADError(
            "Could not determine a 3D envelope from the STEP file — "
            "pass length_mm, width_mm and height_mm."
        )

    counts = {
        k: len(re.findall(rf"\b{k}\s*\(", text))
        for k in ("ADVANCED_FACE", "CYLINDRICAL_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS")
    }
    faces = counts["ADVANCED_FACE"]
    cyl = counts["CYLINDRICAL_SURFACE"]

    f.bbox_mm = [round(v, 3) for v in bbox]
    f.face_count = faces
    f.confidence = "low"

    a, b, c = bbox
    if a <= SHEET_MAX_THICKNESS_MM and b >= 15 * a and cyl <= faces * 0.4:
        f.shape = "sheet"
    else:
        f.shape = "rotational" if _round_envelope(bbox, circle_dias) else "prismatic"

    # Distinct cylinders (a bore may be one face or two half-faces). Unresolvable
    # surfaces fall back to the half-face assumption.
    cyls = []
    for i, (name, args) in ents.items():
        if name == "CYLINDRICAL_SURFACE":
            pl = placement(refs(i)[0]) if refs(i) else None
            if pl is None:
                cyls = None
                break
            loc, axis = pl
            axis = axis if axis[np.argmax(np.abs(axis))] > 0 else -axis
            foot = loc - np.dot(loc, axis) * axis
            r = float(RE_FLOAT.findall(args.rsplit(",", 1)[1])[0]) * scale
            cyls.append((round(r, 3), *np.round(axis, 4), *np.round(foot, 2)))
    if cyls is None:
        f.hole_count = (cyl if f.shape != "rotational" else max(0, cyl - 4)) // 2
    else:
        unique = set(cyls)
        if f.shape == "rotational" and unique:
            # Turned diameters share the part axis: count only off-axis bores.
            main = max(unique)[1:]
            unique = {u for u in unique if u[1:] != main}
        f.hole_count = len(unique)

    # Volume / area estimate from the envelope
    box = a * b * c
    if f.shape == "rotational":
        d, length = rotational_dims(bbox)
        vol = math.pi / 4 * d * d * length * 0.85
    elif f.shape == "sheet":
        vol = box * 0.9
    else:
        vol = box * 0.65
    f.volume_cm3 = round(vol / 1000, 3)
    f.surface_area_cm2 = round(2 * (a * b + b * c + a * c) * 1.2 / 100, 3)
    notes.append(
        "Volume and surface area are estimated from the envelope (OpenCascade not "
        "installed; install cadquery-ocp for exact STEP geometry)."
    )
    f.complexity = "high" if counts["B_SPLINE_SURFACE_WITH_KNOTS"] > 5 else _complexity(faces)
