"""Extract costing-relevant features from a 2D engineering drawing (PDF).

This is a rule-based, best-effort reader of the drawing's text layer: material
callout, tolerances, surface finish, threads, holes, bends and dimension values.
Scanned drawings (no text layer) yield little — the result's `confidence` and
`notes` say so, and callers can pass explicit overrides.
"""

import io
import re

from pypdf import PdfReader

from app.schemas import PartFeatures
from app.services.catalog import match_material

INCH = 25.4

# N-grade roughness numbers (ISO 1302) -> Ra in µm
N_GRADES = {
    "N1": 0.025, "N2": 0.05, "N3": 0.1, "N4": 0.2, "N5": 0.4, "N6": 0.8,
    "N7": 1.6, "N8": 3.2, "N9": 6.3, "N10": 12.5, "N11": 25, "N12": 50,
}

NUM = r"(\d+(?:\.\d+)?|\.\d+)"
DIA = r"(?:Ø|⌀|∅|DIA\.?\s*)"
MULT = r"(?:(\d+)\s*[Xx×]\s*)?"

RE_TOL_SYM = re.compile(r"±\s*" + NUM)
RE_TOL_ASYM = re.compile(r"\+\s*" + NUM + r"\s*/?\s*-\s*" + NUM)
RE_RA = re.compile(r"\bRa\s*[:=]?\s*" + NUM, re.IGNORECASE)
RE_NGRADE = re.compile(r"\b(N(?:1[0-2]|[1-9]))\b")
RE_METRIC_THREAD = re.compile(MULT + r"\bM(\d{1,2}(?:\.\d+)?)(?:\s*[Xx×]\s*\d+(?:\.\d+)?)?\b")
RE_UNIFIED_THREAD = re.compile(MULT + r"\b(?:\d+/\d+|#\d+)\s*-\s*\d+\s*UN[CF]?\b")
RE_HOLE = re.compile(MULT + DIA + NUM + r"[^\n]{0,25}?\b(THRU|THROUGH|DRILL|HOLE|DP|DEEP|C'?BORE|CSK)\b",
                     re.IGNORECASE)
RE_DIAMETER = re.compile(DIA + NUM)
RE_BEND = re.compile(r"\b(?:(\d+)\s*[Xx×]\s*)?(?:BEND|(?:UP|DOWN|DN)\s*\d+\s*°)", re.IGNORECASE)
RE_THICKNESS = re.compile(r"(?:\bTHK\.?|\bTHICKNESS|\bT\s*=)\s*[:=]?\s*" + NUM, re.IGNORECASE)
RE_THICKNESS_AFTER = re.compile(NUM + r"\s*(?:MM)?\s*THK\b", re.IGNORECASE)
RE_MATERIAL_LINE = re.compile(r"\bMAT(?:ERIAL|'L|L)?\b\.?\s*[:\-]?\s*([^\n]{2,60})", re.IGNORECASE)
RE_NUMBER = re.compile(r"(?<![\w.±+\-/:])" + NUM + r"(?![\w.:/%°])")
RE_INCH = re.compile(r"\b(INCH(?:ES)?|DIMENSIONS\s+(?:ARE\s+)?IN\s+IN)\b", re.IGNORECASE)

ROTATIONAL_HINTS = re.compile(r"\b(TURN(?:ED|ING)?|LATHE|SHAFT|PIN|BUSH(?:ING)?|SPACER|SLEEVE|KNURL)\b", re.IGNORECASE)
SHEET_HINTS = re.compile(r"\b(SHEET\s*METAL|FLAT\s*PATTERN|K-?FACTOR|GAUGE|GA\b|BEND\s*RADIUS|CRCA|HRPO)\b", re.IGNORECASE)
FIVE_AXIS_HINTS = re.compile(r"\b(5[- ]?AXIS|UNDERCUT|COMPOUND\s+ANGLE|FREEFORM)\b", re.IGNORECASE)


def extract_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _mult(m: re.Match, group: int = 1) -> int:
    return int(m.group(group)) if m.group(group) else 1


def parse_pdf(data: bytes, file_name: str) -> PartFeatures:
    text = extract_text(data)
    return parse_drawing_text(text, file_name)


def parse_drawing_text(text: str, file_name: str = "drawing.pdf") -> PartFeatures:
    notes: list[str] = []
    f = PartFeatures(source="pdf", file_name=file_name)

    if len(text.strip()) < 20:
        f.notes = [
            "PDF has little or no text layer (likely a scanned drawing). "
            "Supply material and dimensions as overrides, or upload the CAD model."
        ]
        return f

    scale = 1.0
    if RE_INCH.search(text):
        scale = INCH
        f.units_detected = "inch"
        notes.append("Drawing appears to be in inches; values converted to mm.")

    # Material: prefer the title-block MATERIAL line, fall back to the whole sheet.
    mat = None
    for m in RE_MATERIAL_LINE.finditer(text):
        mat = match_material(m.group(1))
        if mat:
            f.material_text = m.group(1).strip()
            break
    if not mat:
        mat = match_material(text)
        if mat:
            f.material_text = mat[1]
    if mat:
        f.material_id = mat[0]
    else:
        notes.append("No recognised material callout found.")

    # Tolerances
    tols = [float(v) for v in RE_TOL_SYM.findall(text)]
    for hi, lo in RE_TOL_ASYM.findall(text):
        band = float(hi) + float(lo)
        if band > 0:
            tols.append(band / 2)
    tols = [t * scale for t in tols if 0 < t < 5]
    if tols:
        f.tightest_tolerance_mm = round(min(tols), 4)

    # Surface finish
    ras = [float(v) for v in RE_RA.findall(text)]
    ras += [N_GRADES[g] for g in RE_NGRADE.findall(text)]
    ras = [r for r in ras if 0 < r <= 50]
    if ras:
        f.best_surface_finish_ra_um = min(ras)

    # Threads, holes, bends
    f.thread_count = sum(_mult(m) for m in RE_METRIC_THREAD.finditer(text))
    f.thread_count += sum(_mult(m) for m in RE_UNIFIED_THREAD.finditer(text))
    f.hole_count = sum(_mult(m) for m in RE_HOLE.finditer(text)) + f.thread_count
    f.bend_count = sum(_mult(m) for m in RE_BEND.finditer(text))

    # Shape classification
    diameters = [float(v) * scale for v in RE_DIAMETER.findall(text)]
    thickness = None
    if tm := (RE_THICKNESS.search(text) or RE_THICKNESS_AFTER.search(text)):
        thickness = float(tm.group(1)) * scale

    if SHEET_HINTS.search(text) or (f.bend_count and thickness):
        f.shape = "sheet"
    elif ROTATIONAL_HINTS.search(text):
        f.shape = "rotational"
    else:
        f.shape = "prismatic"

    # Envelope: the largest dimension values are usually the overall sizes.
    nums = sorted(
        {float(v) * scale for v in RE_NUMBER.findall(text)} - {0.0}, reverse=True
    )
    nums = [n for n in nums if 0.5 <= n <= 3000]
    bbox = None
    if f.shape == "rotational" and diameters:
        d = max(diameters)
        length = next((n for n in nums if n != d), d)
        bbox = [d, d, length]
    elif f.shape == "sheet" and thickness:
        rest = [n for n in nums if n > thickness * 3][:2]
        if len(rest) == 2:
            bbox = [thickness, rest[1], rest[0]]
    elif len(nums) >= 3:
        bbox = nums[:3]
    if bbox:
        f.bbox_mm = sorted(round(v, 3) for v in bbox)
        notes.append(
            "Envelope inferred from the largest dimension values on the drawing; "
            "verify or override length/width/height."
        )
    else:
        notes.append("Could not infer part envelope; pass length/width/height overrides.")

    if FIVE_AXIS_HINTS.search(text):
        f.complexity = "high"
    else:
        callouts = f.hole_count + len(tols) + len(ras)
        f.complexity = "low" if callouts < 5 else "medium" if callouts < 20 else "high"

    f.confidence = "medium" if (f.material_id and f.bbox_mm) else "low"
    f.notes = notes
    return f
