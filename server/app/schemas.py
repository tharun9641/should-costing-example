from typing import Literal

from pydantic import BaseModel, Field

# Where a rate came from: fetched within its TTL ("live"), an older fetch kept in
# memory ("cached"), the stored app/data value because no fetch succeeded
# ("fallback"), derived from a price index ("indexed"), entered by hand and never
# fetched ("manual"), or fixed by definition such as USD per USD ("fixed").
RateStatus = Literal["live", "cached", "fallback", "indexed", "manual", "fixed"]


# ---------- Catalog ----------

class MachineRateOverride(BaseModel):
    rate_usd_per_hour: float
    source: str
    as_of: str


class Location(BaseModel):
    id: str
    name: str
    country: str
    currency: str
    fx_per_usd: float
    labour_rates: dict[str, float]
    material_price_factor: float
    machine_rate_factor: float
    machine_rate_overrides: dict[str, MachineRateOverride] = Field(
        {}, description="Per-machine hourly rates that replace base rate × machine_rate_factor"
    )
    overhead_pct: float
    profit_pct: float
    fx_source: str = "Unverified"
    fx_as_of: str | None = None
    fx_status: RateStatus = "fallback"
    rate_source: str = "Unverified"
    rate_as_of: str | None = None


class MaterialPricing(BaseModel):
    """How a material's price follows the market (see services/catalog.material_rate)."""
    method: Literal["commodity", "indexed", "manual"] = "manual"
    # commodity: live price = Σ share × metal price × stock_markup
    basis: dict[str, float] = Field({}, description="Commodity -> mass share")
    stock_markup: float | None = Field(None, description="Raw metal -> bar/plate stock multiplier")
    # indexed: stored price × (latest US steel PPI / steel_index_base), blended with nickel
    steel_index_base: float | None = None
    steel_index_base_month: str | None = None
    ni_share: float | None = Field(None, description="Share of the price that follows nickel")
    ni_share_note: str | None = None
    nickel_base_usd_per_t: float | None = None
    nickel_base_month: str | None = None


class Material(BaseModel):
    id: str
    name: str
    category: str
    density_g_cm3: float
    price_usd_per_kg: float
    machinability: float
    aliases: list[str] = []
    source: str = "Unverified"
    as_of: str | None = None
    pricing: MaterialPricing = MaterialPricing()


class Machine(BaseModel):
    id: str
    name: str
    process: str
    hourly_rate_usd: float
    setup_hours: float
    operator_ratio: float
    operator_skill: str
    max_envelope_mm: list[float]
    mrr_cm3_per_min: float | None = None
    finish_cm2_per_min: float | None = None
    cut_mm_per_min: float | None = None
    seconds_per_bend: float | None = None
    seconds_per_hole: float | None = None
    source: str = "Unverified"
    as_of: str | None = None


class MaterialRate(BaseModel):
    material_id: str
    material_name: str
    location_id: str
    price_usd_per_kg: float
    price_local_per_kg: float
    currency: str
    source: str
    as_of: str | None
    status: RateStatus = "manual"
    assumption: str | None = Field(None, description="Added to an estimate's assumptions")


class LabourRate(BaseModel):
    location_id: str
    skill: str
    rate_usd_per_hour: float
    rate_local_per_hour: float
    currency: str
    source: str
    as_of: str | None
    status: RateStatus = "manual"


class MachineRate(BaseModel):
    machine_id: str
    machine_name: str
    location_id: str
    rate_usd_per_hour: float
    rate_local_per_hour: float
    currency: str
    source: str
    as_of: str | None
    status: RateStatus = "manual"


# ---------- Part analysis ----------

Shape = Literal["rotational", "prismatic", "sheet", "unknown"]


class PartFeatures(BaseModel):
    source: Literal["pdf", "cad"]
    file_name: str
    units_detected: str = "mm"
    bbox_mm: list[float] | None = Field(
        None, description="Bounding box [x, y, z] in mm, sorted ascending"
    )
    volume_cm3: float | None = None
    surface_area_cm2: float | None = None
    shape: Shape = "unknown"
    material_id: str | None = None
    material_text: str | None = None
    hole_count: int = 0
    thread_count: int = 0
    bend_count: int = 0
    tightest_tolerance_mm: float | None = None
    best_surface_finish_ra_um: float | None = None
    face_count: int | None = None
    complexity: Literal["low", "medium", "high"] = "medium"
    confidence: Literal["low", "medium", "high"] = "low"
    notes: list[str] = []


class MachineStep(BaseModel):
    machine_id: str
    machine_name: str
    process: str
    role: Literal["primary", "secondary", "finishing"] = "primary"
    reason: str


class AnalysisResult(BaseModel):
    features: PartFeatures
    routing: list[MachineStep]


# ---------- Cost estimate ----------

class OperationCost(BaseModel):
    machine_id: str
    machine_name: str
    cycle_time_min: float
    setup_time_min: float
    machine_cost_usd: float
    labour_cost_usd: float


class CostBreakdown(BaseModel):
    material_cost_usd: float
    labour_cost_usd: float
    machine_cost_usd: float
    overhead_usd: float
    profit_usd: float
    unit_price_usd: float
    unit_price_local: float
    total_price_usd: float
    total_price_local: float
    currency: str


class EstimateResult(BaseModel):
    location: Location
    material: Material
    material_rate: MaterialRate
    quantity: int
    stock_mass_kg: float
    part_mass_kg: float | None
    features: PartFeatures
    routing: list[MachineStep]
    operations: list[OperationCost]
    cost: CostBreakdown
    assumptions: list[str]
