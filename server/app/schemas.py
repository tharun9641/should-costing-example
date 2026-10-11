from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class CostInput(BaseModel):
    value: float
    source: str
    as_of: str | None = None


class MachineCostInputs(BaseModel):
    """Inputs for an auditable machine-hour rate; money values in the location's currency."""
    purchase_price: CostInput | None = None
    life_years: CostInput | None = None
    hours_per_year: CostInput | None = None
    power_kw: CostInput | None = Field(None, description="Average electrical draw while cutting")
    electricity_price_per_kwh: CostInput | None = None
    annual_maintenance_pct: CostInput | None = Field(None, description="% of purchase price per year")
    tooling_per_hour: CostInput | None = None
    floor_space_per_hour: CostInput | None = None

    def missing(self) -> list[str]:
        return [k for k in type(self).model_fields if getattr(self, k) is None]

    def build_up(self) -> dict[str, float] | None:
        """Hourly cost components (local currency), or None while any input is missing."""
        if self.missing():
            return None
        v = {k: getattr(self, k).value for k in type(self).model_fields}
        return {
            "depreciation": v["purchase_price"] / (v["life_years"] * v["hours_per_year"]),
            "power": v["power_kw"] * v["electricity_price_per_kwh"],
            "maintenance": v["purchase_price"] * v["annual_maintenance_pct"] / 100 / v["hours_per_year"],
            "tooling": v["tooling_per_hour"],
            "floor_space": v["floor_space_per_hour"],
        }


class LabourInput(BaseModel):
    base_wage_usd_per_hour: float
    burden_factor: float = Field(description="Employer cost per wage unit: benefits, social charges, leave")
    source: str
    as_of: str | None = None


class Location(BaseModel):
    id: str
    name: str
    country: str
    currency: str
    fx_per_usd: float
    labour_rates: dict[str, float] = Field(description="Effective USD/h: base wage x burden factor")
    labour_inputs: dict[str, LabourInput] = {}
    material_price_factor: float
    machine_rate_factor: float
    machine_rate_overrides: dict[str, MachineRateOverride] = Field(
        {}, description="Per-machine hourly rates that replace base rate × machine_rate_factor"
    )
    machine_cost_inputs: dict[str, MachineCostInputs] = Field(
        {}, description="Per-machine build-up inputs; when complete they set the rate"
    )
    overhead_pct: float
    profit_pct: float
    fx_source: str = "Unverified"
    fx_as_of: str | None = None
    fx_status: RateStatus = "fallback"

    @model_validator(mode="before")
    @classmethod
    def _labour_from_inputs(cls, data):
        """labour_rates in the data file holds per-skill inputs; the engine uses base x burden."""
        rates = data.get("labour_rates") if isinstance(data, dict) else None
        if rates and isinstance(next(iter(rates.values())), dict):
            data = {
                **data,
                "labour_inputs": rates,
                "labour_rates": {
                    k: v["base_wage_usd_per_hour"] * v["burden_factor"] for k, v in rates.items()
                },
            }
        return data


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
    base_wage_usd_per_hour: float | None = None
    burden_factor: float | None = None
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
    method: Literal["build_up", "override", "factor"] = "factor"
    build_up_local: dict[str, float] | None = Field(None, description="Hourly components, local currency")
    missing_inputs: list[str] = []
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
