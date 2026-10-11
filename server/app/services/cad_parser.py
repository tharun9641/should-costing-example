"""Extract costing-relevant geometry from 3D CAD files.

- Mesh formats (STL, OBJ, PLY, OFF, 3MF, GLB) are loaded with trimesh: exact
  bounding box, volume, surface area, through-hole count (from genus) and planar
  face groups.
- STEP files are read with a lightweight text parser (no OpenCascade dependency):
  units, bounding box from CARTESIAN_POINTs, face/surface-type counts and any
  material named in the product data. Volume is estimated from the envelope.
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

SHEET_MAX_THICKNESS_MM = 6.0


class UnsupportedCADError(ValueError):
    pass


def parse_cad(data: bytes, file_name: str, ext: str) -> PartFeatures:
    if ext in MESH_EXTS:
        return _parse_mesh(data, file_name, ext)
    if ext in STEP_EXTS:
        return _parse_step(data, file_name)
    raise UnsupportedCADError(
        f"Unsupported CAD format '{ext}'. Supported: {sorted(SUPPORTED_EXTS)}"
    )


def _complexity(score: int) -> str:
    return "low" if score < 20 else "medium" if score < 80 else "high"


def _classify_envelope(bbox: list[float]) -> str:
    """Rotational if two of the three envelope dimensions are (nearly) equal."""
    a, b, c = bbox
    return "rotational" if nearly_equal(a, b) or nearly_equal(b, c) else "prismatic"


# ---------- Mesh ----------

def _parse_mesh(data: bytes, file_name: str, ext: str) -> PartFeatures:
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=ext.lstrip("."), force="mesh")
    except Exception as e:  # trimesh raises a variety of errors on bad input
        raise UnsupportedCADError(f"Could not read {ext} file: {e}") from e
    mesh: trimesh.Trimesh = loaded
    if mesh.is_empty:
        raise UnsupportedCADError("CAD file contains no geometry")

    notes: list[str] = ["Mesh units assumed to be millimetres."]
    bbox = sorted(float(v) for v in mesh.extents)
    volume_mm3 = abs(float(mesh.volume)) if mesh.is_watertight else None
    area_mm2 = float(mesh.area)

    f = PartFeatures(
        source="cad",
        file_name=file_name,
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

    # Sheet test: a plate of thickness t has t ≈ 2V/A.
    if volume_mm3:
        t = 2 * volume_mm3 / area_mm2
        if t <= SHEET_MAX_THICKNESS_MM and bbox[2] >= 15 * t:
            f.shape = "sheet"
            notes.append(f"Detected sheet metal, thickness ≈ {t:.2f} mm.")
            if bbox[0] > 3 * t:
                f.bend_count = 2  # bent part; exact bend count needs B-rep data
                notes.append("Part is formed (envelope thicker than sheet); assumed 2 bends.")
    if f.shape == "unknown":
        f.shape = _classify_envelope(bbox)
        if f.shape == "rotational" and volume_mm3:
            # Confirm with fill ratio: a solid cylinder fills π/4 of its box.
            fill = volume_mm3 / (bbox[0] * bbox[1] * bbox[2])
            if fill > 0.9:
                f.shape = "prismatic"

    try:
        facets = len(mesh.facets)
    except Exception:
        facets = 0
    f.face_count = facets
    f.complexity = _complexity(facets + 3 * f.hole_count)
    f.notes = notes
    return f


# ---------- STEP ----------

RE_POINT = re.compile(
    r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-+\dEe.]+)\s*,\s*([-+\dEe.]+)\s*,\s*([-+\dEe.]+)\s*\)"
)
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

    pts = np.array(RE_POINT.findall(text), dtype=float)
    if pts.size == 0:
        raise UnsupportedCADError("No geometry found in STEP file")
    extents = (pts.max(axis=0) - pts.min(axis=0)) * scale
    bbox = sorted(float(v) for v in extents)

    counts = {
        k: len(re.findall(rf"\b{k}\s*\(", text))
        for k in ("ADVANCED_FACE", "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
                  "TOROIDAL_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS", "SPHERICAL_SURFACE")
    }
    faces = counts["ADVANCED_FACE"]
    cyl = counts["CYLINDRICAL_SURFACE"]
    freeform = counts["B_SPLINE_SURFACE_WITH_KNOTS"]

    f = PartFeatures(
        source="cad",
        file_name=file_name,
        units_detected=units,
        bbox_mm=[round(v, 3) for v in bbox],
        face_count=faces,
        confidence="medium",
    )

    # Material from product name/description or a material representation.
    candidates = [" ".join(p) for p in RE_PRODUCT.findall(text)]
    candidates += RE_MATERIAL_REP.findall(text)
    for c in candidates:
        if mat := match_material(c):
            f.material_id, f.material_text = mat[0], c.strip()
            break
    if not f.material_id:
        notes.append("No material found in STEP product data.")

    # Shape
    a, b, c = bbox
    if a <= SHEET_MAX_THICKNESS_MM and b >= 15 * a and cyl <= faces * 0.4:
        f.shape = "sheet"
    else:
        f.shape = _classify_envelope(bbox)
        if f.shape == "rotational" and faces and cyl / faces < 0.2:
            f.shape = "prismatic"

    # Each cylindrical hole is typically split into 2 half-cylinder faces.
    # On a turned part the outer diameters are cylinders too, so discount them.
    hole_cyl = cyl if f.shape != "rotational" else max(0, cyl - 4)
    f.hole_count = hole_cyl // 2

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
        "STEP volume and surface area are estimated from the envelope "
        "(install an OpenCascade-based reader for exact values)."
    )

    f.complexity = "high" if freeform > 5 else _complexity(faces)
    f.notes = notes
    return f
