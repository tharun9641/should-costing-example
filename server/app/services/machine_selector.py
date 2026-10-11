"""Rule-based process routing: which machines a part needs, and why."""

from app.schemas import MachineStep, PartFeatures
from app.services import catalog
from app.services.geometry import rotational_dims

GRIND_TOLERANCE_MM = 0.005
GRIND_RA_UM = 0.4
SWISS_MAX_DIA_MM = 32
SWISS_MAX_LENGTH_MM = 300


def _step(machine_id: str, role: str, reason: str) -> MachineStep:
    m = catalog.get_machine(machine_id)
    return MachineStep(
        machine_id=m.id, machine_name=m.name, process=m.process, role=role, reason=reason
    )


def _fits(machine_id: str, bbox: list[float] | None) -> bool:
    if not bbox:
        return True
    env = sorted(catalog.get_machine(machine_id).max_envelope_mm)
    return all(p <= e for p, e in zip(sorted(bbox), env))


def select_machines(f: PartFeatures) -> list[MachineStep]:
    routing: list[MachineStep] = []
    bbox = f.bbox_mm

    if f.shape == "sheet":
        routing.append(_step("LASER_CUTTER", "primary", "Sheet part: profile and holes cut from flat blank."))
        if f.bend_count:
            routing.append(_step("PRESS_BRAKE", "secondary", f"{f.bend_count} bend(s) to form the part."))
        if f.thread_count:
            routing.append(_step("DRILL_TAP", "secondary", f"{f.thread_count} tapped hole(s)."))

    elif f.shape == "rotational":
        dia, length = rotational_dims(bbox) if bbox else (None, None)
        if dia and dia <= SWISS_MAX_DIA_MM and length <= SWISS_MAX_LENGTH_MM:
            routing.append(_step(
                "SWISS_LATHE", "primary",
                f"Small rotational part (Ø{dia:g} ≤ {SWISS_MAX_DIA_MM} mm): bar-fed Swiss turning.",
            ))
        else:
            routing.append(_step("CNC_LATHE", "primary", "Rotational geometry: CNC turning."))
        if f.complexity == "high" or f.hole_count > 4:
            routing.append(_step(
                "CNC_MILL_3AX", "secondary",
                "Off-axis features (cross holes / flats / slots) need a milling op.",
            ))

    else:
        if f.complexity == "high":
            routing.append(_step(
                "CNC_MILL_5AX", "primary",
                "Prismatic part with high complexity / multi-sided features: 5-axis milling.",
            ))
        else:
            routing.append(_step("CNC_MILL_3AX", "primary", "Prismatic geometry: 3-axis milling."))

    tol, ra = f.tightest_tolerance_mm, f.best_surface_finish_ra_um
    if (tol is not None and tol <= GRIND_TOLERANCE_MM) or (ra is not None and ra <= GRIND_RA_UM):
        why = []
        if tol is not None and tol <= GRIND_TOLERANCE_MM:
            why.append(f"tolerance ±{tol:g} mm")
        if ra is not None and ra <= GRIND_RA_UM:
            why.append(f"finish Ra {ra:g} µm")
        routing.append(_step("SURFACE_GRINDER", "finishing", "Grinding for " + " and ".join(why) + "."))

    return routing


def envelope_warnings(routing: list[MachineStep], bbox: list[float] | None) -> list[str]:
    return [
        f"Part envelope {bbox} mm exceeds the typical envelope of {s.machine_name}."
        for s in routing
        if not _fits(s.machine_id, bbox)
    ]


def routing_from_ids(machine_ids: list[str]) -> list[MachineStep]:
    return [
        _step(mid, "primary" if i == 0 else "secondary", "Specified by user.")
        for i, mid in enumerate(machine_ids)
    ]
