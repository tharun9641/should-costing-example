from fastapi import APIRouter, Query

from app.schemas import (
    LabourRate,
    Location,
    Machine,
    MachineRate,
    Material,
    MaterialRate,
)
from app.services import catalog

router = APIRouter(prefix="/api", tags=["catalog & rates"])


@router.get("/locations", response_model=list[Location])
def list_locations():
    return catalog.all_locations()


@router.get("/materials", response_model=list[Material])
def list_materials():
    return list(catalog.materials().values())


@router.get("/machines", response_model=list[Machine])
def list_machines():
    return list(catalog.machines().values())


@router.get("/rates/material", response_model=MaterialRate)
def get_material_rate(
    material_id: str = Query(..., examples=["AL6061"]),
    location_id: str = Query(..., examples=["IN-CHENNAI"]),
):
    return catalog.material_rate(material_id, location_id)


@router.get("/rates/labour", response_model=LabourRate)
def get_labour_rate(
    location_id: str = Query(..., examples=["IN-CHENNAI"]),
    skill: str = Query("machinist", examples=["operator", "machinist", "programmer", "inspector"]),
):
    return catalog.labour_rate(location_id, skill)


@router.get("/rates/machine", response_model=MachineRate)
def get_machine_rate(
    machine_id: str = Query(..., examples=["CNC_MILL_3AX"]),
    location_id: str = Query(..., examples=["IN-CHENNAI"]),
):
    return catalog.machine_rate(machine_id, location_id)


@router.get("/rates/{location_id}")
def get_all_rates(location_id: str):
    """Every material, labour and machine rate for one location."""
    loc = catalog.get_location(location_id)
    return {
        "location": loc,
        "materials": [catalog.material_rate(m, loc.id) for m in catalog.materials()],
        "labour": [catalog.labour_rate(loc.id, s) for s in loc.labour_rates],
        "machines": [catalog.machine_rate(m, loc.id) for m in catalog.machines()],
    }
