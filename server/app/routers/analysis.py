from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.schemas import AnalysisResult, EstimateResult, PartFeatures
from app.services import catalog, cost_engine, machine_selector
from app.services.cad_parser import SUPPORTED_EXTS, parse_cad
from app.services.pdf_parser import parse_pdf

router = APIRouter(prefix="/api", tags=["analysis & estimate"])

Shape = Literal["rotational", "prismatic", "sheet"]
Units = Literal["mm", "inch", "m"]


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    ext = Path(file.filename or "").suffix.lower()
    if ext != ".pdf" and ext not in SUPPORTED_EXTS:
        raise HTTPException(
            415, f"Unsupported file type '{ext}'. Use .pdf or one of {sorted(SUPPORTED_EXTS)}"
        )
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(400, "Empty file")
    return data, ext


def _extract(data: bytes, ext: str, name: str, units: Units | None = None) -> PartFeatures:
    try:
        return parse_pdf(data, name) if ext == ".pdf" else parse_cad(data, name, ext, units)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise HTTPException(422, f"Could not parse file: {e}") from e


def _apply_overrides(
    f: PartFeatures,
    material_id: str | None,
    length_mm: float | None,
    width_mm: float | None,
    height_mm: float | None,
    shape: Shape | None,
) -> PartFeatures:
    if material_id:
        f.material_id = catalog.get_material(material_id).id
        f.material_text = "override"
    dims = [length_mm, width_mm, height_mm]
    if any(d is not None for d in dims):
        if not all(d and d > 0 for d in dims):
            raise HTTPException(422, "Provide all of length_mm, width_mm, height_mm (> 0)")
        f.bbox_mm = sorted(dims)
        f.volume_cm3 = None if f.source == "pdf" else f.volume_cm3
        f.notes.append("Envelope overridden by user.")
    if shape:
        f.shape = shape
    return f


PartFile = Annotated[
    UploadFile, File(description="Drawing (.pdf) or CAD (.step/.stp/.stl/.obj/...)")
]
MaterialOverride = Annotated[
    str | None, Form(description="Catalogue material id; overrides detection")
]
DimOverride = Annotated[float | None, Form(gt=0, description="Envelope dimension in mm")]
ShapeOverride = Annotated[Shape | None, Form(description="Overrides detected shape")]
UnitsOverride = Annotated[
    Units | None, Form(description="Units of a mesh file (STL/OBJ/...); default mm")
]


@router.post("/analyze", response_model=AnalysisResult)
async def analyze(
    file: PartFile,
    material_id: MaterialOverride = None,
    length_mm: DimOverride = None,
    width_mm: DimOverride = None,
    height_mm: DimOverride = None,
    shape: ShapeOverride = None,
    units: UnitsOverride = None,
):
    """Extract part features and recommend the machines needed to make it."""
    data, ext = await _read_upload(file)
    f = _extract(data, ext, file.filename, units)
    f = _apply_overrides(f, material_id, length_mm, width_mm, height_mm, shape)
    routing = machine_selector.select_machines(f)
    f.notes += machine_selector.envelope_warnings(routing, f.bbox_mm)
    return AnalysisResult(features=f, routing=routing)


@router.post("/estimate", response_model=EstimateResult)
async def estimate(
    file: PartFile,
    location_id: Annotated[str, Form(examples=["IN-CHENNAI"])],
    quantity: Annotated[int, Form(ge=1, le=1_000_000)] = 1,
    material_id: MaterialOverride = None,
    length_mm: DimOverride = None,
    width_mm: DimOverride = None,
    height_mm: DimOverride = None,
    shape: ShapeOverride = None,
    units: UnitsOverride = None,
    machine_ids: Annotated[
        str | None, Form(description="Comma-separated machine ids to force a routing")
    ] = None,
):
    """Full should-cost: material, labour and machine cost for the chosen location."""
    loc = catalog.get_location(location_id)
    data, ext = await _read_upload(file)
    f = _extract(data, ext, file.filename, units)
    f = _apply_overrides(f, material_id, length_mm, width_mm, height_mm, shape)

    if not f.material_id:
        raise HTTPException(
            422,
            "Material could not be detected from the file — pass material_id "
            f"(one of {sorted(catalog.materials())}).",
        )

    if machine_ids:
        ids = [m.strip() for m in machine_ids.split(",") if m.strip()]
        routing = machine_selector.routing_from_ids([catalog.get_machine(i).id for i in ids])
    else:
        routing = machine_selector.select_machines(f)
    f.notes += machine_selector.envelope_warnings(routing, f.bbox_mm)

    try:
        return cost_engine.estimate(f, routing, catalog.get_material(f.material_id), loc, quantity)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # never a bare 500: the user can usually fix it with overrides
        raise HTTPException(
            422, f"Could not cost this part ({type(e).__name__}: {e}). Check the envelope "
            "and shape, or pass length_mm, width_mm, height_mm and shape overrides."
        ) from e
