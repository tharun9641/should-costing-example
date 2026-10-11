"""Should-cost model: material + labour + machine + overhead + profit.

Per unit:
  material  = stock mass × location-adjusted price/kg
  machine   = (cycle + amortised setup) × location machine rate, per operation
  labour    = cycle × operator ratio × operator rate
              + amortised setup × machinist rate
              + amortised CNC programming × programmer rate
              + inspection × inspector rate
  overhead  = overhead % × (labour + machine)
  profit    = profit % × (material + labour + machine + overhead)
"""

import math

from app.schemas import (
    CostBreakdown,
    EstimateResult,
    Location,
    MachineStep,
    Material,
    OperationCost,
    PartFeatures,
)
from app.services import catalog
from app.services.geometry import rotational_dims

STOCK_ALLOWANCE_MM = 5.0         # per side for bar / block
SHEET_NESTING_UTILISATION = 0.80
LOAD_UNLOAD_MIN = 1.0
MIN_PER_HOLE = 0.5
MIN_PER_THREAD = 0.75
GRIND_AREA_FRACTION = 0.3
INSPECTION_MIN = 2.0
PROGRAMMING_HOURS = {"low": 0.5, "medium": 1.5, "high": 4.0}
FILL_FACTOR = {"rotational": 0.85 * math.pi / 4, "prismatic": 0.65, "sheet": 0.9, "unknown": 0.65}


def _r(x: float, n: int = 4) -> float:
    return round(x, n)


def _part_volume_cm3(f: PartFeatures) -> float | None:
    if f.volume_cm3:
        return f.volume_cm3
    if f.bbox_mm:
        a, b, c = f.bbox_mm
        return a * b * c * FILL_FACTOR[f.shape] / 1000
    return None


def _sheet_flat_dims(f: PartFeatures) -> tuple[float, float, float]:
    """(thickness, width, length) of the flat blank, in mm."""
    a, b, c = f.bbox_mm
    vol = (f.volume_cm3 or a * b * c * FILL_FACTOR["sheet"] / 1000) * 1000
    t = a
    if f.surface_area_cm2 and f.volume_cm3:
        t = 2 * vol / (f.surface_area_cm2 * 100)
    if t <= 0:
        raise ValueError("Sheet thickness is zero — provide length_mm, width_mm and height_mm.")
    flat_area = vol / t
    # Keep the blank's aspect ratio close to the envelope's two larger dims.
    ratio = c / b if b else 1
    width = math.sqrt(flat_area / ratio)
    return t, width, width * ratio


def _stock_volume_cm3(f: PartFeatures) -> float:
    a, b, c = f.bbox_mm
    s = STOCK_ALLOWANCE_MM
    if f.shape == "rotational":
        d, length = rotational_dims(f.bbox_mm)
        return math.pi / 4 * (d + s) ** 2 * (length + s) / 1000
    if f.shape == "sheet":
        t, w, l = _sheet_flat_dims(f)
        return t * w * l / SHEET_NESTING_UTILISATION / 1000
    return (a + s) * (b + s) * (c + s) / 1000


def _cycle_minutes(step: MachineStep, f: PartFeatures, mat: Material,
                   removal_cm3: float, area_cm2: float) -> float:
    m = catalog.get_machine(step.machine_id)
    feature_min = f.hole_count * MIN_PER_HOLE + f.thread_count * MIN_PER_THREAD

    if m.process in ("turning", "milling"):
        if step.role == "secondary":
            return LOAD_UNLOAD_MIN + feature_min
        rough = removal_cm3 / (m.mrr_cm3_per_min * mat.machinability)
        finish = area_cm2 / (m.finish_cm2_per_min * math.sqrt(mat.machinability))
        if f.tightest_tolerance_mm is not None and f.tightest_tolerance_mm <= 0.01:
            finish *= 1.3
        if f.best_surface_finish_ra_um is not None and f.best_surface_finish_ra_um <= 0.8:
            finish *= 1.2
        return LOAD_UNLOAD_MIN + rough + finish + feature_min

    if m.process == "grinding":
        return LOAD_UNLOAD_MIN + area_cm2 * GRIND_AREA_FRACTION / m.finish_cm2_per_min

    if m.process == "edm":
        return LOAD_UNLOAD_MIN + area_cm2 * GRIND_AREA_FRACTION / m.finish_cm2_per_min

    if m.process == "sheet_cutting":
        t, w, l = _sheet_flat_dims(f)
        cut_len = 2 * (w + l) + f.hole_count * math.pi * 10
        speed = m.cut_mm_per_min / max(1.0, t / 2)
        pierce_min = (1 + f.hole_count) * 2 / 60
        return 0.5 + cut_len / speed + pierce_min

    if m.process == "bending":
        return 0.5 + max(1, f.bend_count) * m.seconds_per_bend / 60

    if m.process == "drilling":
        return 0.5 + max(1, f.hole_count) * m.seconds_per_hole / 60

    return LOAD_UNLOAD_MIN


def estimate(
    f: PartFeatures,
    routing: list[MachineStep],
    mat: Material,
    loc: Location,
    quantity: int,
) -> EstimateResult:
    if not f.bbox_mm:
        raise ValueError(
            "Part envelope unknown — provide length_mm, width_mm and height_mm."
        )
    if min(f.bbox_mm) <= 0:
        raise ValueError(
            f"Part envelope {f.bbox_mm} mm has a zero dimension — "
            "provide length_mm, width_mm and height_mm."
        )

    assumptions: list[str] = []
    part_vol = _part_volume_cm3(f)
    stock_vol = _stock_volume_cm3(f)
    removal = max(0.0, stock_vol - (part_vol or 0))
    area = f.surface_area_cm2 or 2 * sum(
        x * y for x, y in ((f.bbox_mm[0], f.bbox_mm[1]), (f.bbox_mm[1], f.bbox_mm[2]),
                           (f.bbox_mm[0], f.bbox_mm[2]))
    ) / 100

    density_kg_cm3 = mat.density_g_cm3 / 1000
    stock_kg = stock_vol * density_kg_cm3
    part_kg = part_vol * density_kg_cm3 if part_vol else None

    mrate = catalog.material_rate(mat.id, loc.id)
    material_cost = stock_kg * mrate.price_usd_per_kg

    machinist = loc.labour_rates["machinist"]
    operations: list[OperationCost] = []
    machine_total = labour_total = 0.0
    needs_cnc_program = False

    for step in routing:
        m = catalog.get_machine(step.machine_id)
        rate = catalog.machine_rate(m.id, loc.id).rate_usd_per_hour
        cycle = _cycle_minutes(step, f, mat, removal, area)
        setup = m.setup_hours * 60 / quantity
        op_rate = loc.labour_rates.get(m.operator_skill, machinist)

        machine_cost = (cycle + setup) / 60 * rate
        labour_cost = cycle / 60 * m.operator_ratio * op_rate + setup / 60 * machinist

        operations.append(OperationCost(
            machine_id=m.id,
            machine_name=m.name,
            cycle_time_min=_r(cycle, 2),
            setup_time_min=_r(setup, 2),
            machine_cost_usd=_r(machine_cost),
            labour_cost_usd=_r(labour_cost),
        ))
        machine_total += machine_cost
        labour_total += labour_cost
        needs_cnc_program |= m.process in ("turning", "milling", "edm", "sheet_cutting")

    if needs_cnc_program:
        prog_h = PROGRAMMING_HOURS[f.complexity]
        labour_total += prog_h * loc.labour_rates["programmer"] / quantity
        assumptions.append(f"CNC programming {prog_h} h, amortised over {quantity} pcs.")

    insp_min = INSPECTION_MIN * (2 if (f.tightest_tolerance_mm or 1) <= 0.01 else 1)
    labour_total += insp_min / 60 * loc.labour_rates["inspector"]
    assumptions.append(f"Inspection {insp_min:g} min/part.")

    overhead = (labour_total + machine_total) * loc.overhead_pct / 100
    subtotal = material_cost + labour_total + machine_total + overhead
    profit = subtotal * loc.profit_pct / 100
    unit = subtotal + profit

    if f.shape == "sheet":
        assumptions.append(f"Sheet blank with {SHEET_NESTING_UTILISATION:.0%} nesting utilisation.")
    else:
        assumptions.append(f"Stock allowance {STOCK_ALLOWANCE_MM:g} mm over the part envelope.")
    if not f.volume_cm3:
        assumptions.append("Part volume estimated from envelope fill factor.")
    assumptions.append(
        f"Overhead {loc.overhead_pct:g}% of conversion cost, profit {loc.profit_pct:g}%."
    )
    if mrate.assumption:
        assumptions.append(mrate.assumption)
    fallbacks = []
    if loc.fx_status == "fallback":
        fallbacks.append(f"exchange rate {loc.currency}/USD {loc.fx_per_usd:g}")
    if mrate.status == "fallback":
        fallbacks.append(f"{mat.name} price")
    if fallbacks:
        assumptions.append(
            "Live rate unavailable; stored fallback value used for " + ", ".join(fallbacks) + "."
        )

    fx = loc.fx_per_usd
    return EstimateResult(
        location=loc,
        material=mat,
        material_rate=mrate,
        quantity=quantity,
        stock_mass_kg=_r(stock_kg),
        part_mass_kg=_r(part_kg) if part_kg is not None else None,
        features=f,
        routing=routing,
        operations=operations,
        cost=CostBreakdown(
            material_cost_usd=_r(material_cost),
            labour_cost_usd=_r(labour_total),
            machine_cost_usd=_r(machine_total),
            overhead_usd=_r(overhead),
            profit_usd=_r(profit),
            unit_price_usd=_r(unit),
            unit_price_local=round(unit * fx, 2),
            total_price_usd=round(unit * quantity, 2),
            total_price_local=round(unit * quantity * fx, 2),
            currency=loc.currency,
        ),
        assumptions=assumptions,
    )
