"""Reference data (locations, materials, machines) and location-adjusted rate lookups.

Data lives in app/data/*.json so it can be updated without code changes. To plug in
live feeds (e.g. an LME/metals price API or a labour-statistics source), replace the
loader functions or override the rate functions below — the rest of the app only
talks to this module.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.schemas import (
    LabourRate,
    Location,
    Machine,
    MachineRate,
    Material,
    MaterialRate,
)


class NotFoundError(KeyError):
    pass


def _load(name: str, key: str) -> list[dict]:
    """Entries of a data file; `source` / `as_of` default to the file's _meta."""
    path = Path(settings.data_dir) / name
    with path.open() as f:
        data = json.load(f)
    defaults = {k: data["_meta"][k] for k in ("source", "as_of") if k in data.get("_meta", {})}
    return [{**defaults, **d} for d in data[key]]


@lru_cache
def locations() -> dict[str, Location]:
    return {d["id"]: Location(**d) for d in _load("locations.json", "locations")}


@lru_cache
def materials() -> dict[str, Material]:
    return {d["id"]: Material(**d) for d in _load("materials.json", "materials")}


@lru_cache
def machines() -> dict[str, Machine]:
    return {d["id"]: Machine(**d) for d in _load("machines.json", "machines")}


def reload() -> None:
    for fn in (locations, materials, machines):
        fn.cache_clear()


def get_location(location_id: str) -> Location:
    try:
        return locations()[location_id.upper()]
    except KeyError:
        raise NotFoundError(f"Unknown location '{location_id}'") from None


def get_material(material_id: str) -> Material:
    try:
        return materials()[material_id.upper()]
    except KeyError:
        raise NotFoundError(f"Unknown material '{material_id}'") from None


def get_machine(machine_id: str) -> Machine:
    try:
        return machines()[machine_id.upper()]
    except KeyError:
        raise NotFoundError(f"Unknown machine '{machine_id}'") from None


def material_rate(material_id: str, location_id: str) -> MaterialRate:
    mat = get_material(material_id)
    loc = get_location(location_id)
    usd = mat.price_usd_per_kg * loc.material_price_factor
    return MaterialRate(
        material_id=mat.id,
        material_name=mat.name,
        location_id=loc.id,
        price_usd_per_kg=round(usd, 4),
        price_local_per_kg=round(usd * loc.fx_per_usd, 2),
        currency=loc.currency,
        source=f"{mat.source}; × {loc.name} material factor {loc.material_price_factor:g}",
        as_of=mat.as_of,
    )


def labour_rate(location_id: str, skill: str = "machinist") -> LabourRate:
    loc = get_location(location_id)
    if skill not in loc.labour_rates:
        raise NotFoundError(
            f"Unknown skill '{skill}'. Available: {sorted(loc.labour_rates)}"
        )
    usd = loc.labour_rates[skill]
    return LabourRate(
        location_id=loc.id,
        skill=skill,
        rate_usd_per_hour=usd,
        rate_local_per_hour=round(usd * loc.fx_per_usd, 2),
        currency=loc.currency,
        source=loc.rate_source,
        as_of=loc.rate_as_of,
    )


def machine_rate(machine_id: str, location_id: str) -> MachineRate:
    m = get_machine(machine_id)
    loc = get_location(location_id)
    if override := loc.machine_rate_overrides.get(m.id):
        usd, source, as_of = override.rate_usd_per_hour, override.source, override.as_of
    else:
        usd = m.hourly_rate_usd * loc.machine_rate_factor
        source = f"{m.source}; × {loc.name} machine factor {loc.machine_rate_factor:g}"
        as_of = m.as_of
    return MachineRate(
        machine_id=m.id,
        machine_name=m.name,
        location_id=loc.id,
        rate_usd_per_hour=round(usd, 4),
        rate_local_per_hour=round(usd * loc.fx_per_usd, 2),
        currency=loc.currency,
        source=source,
        as_of=as_of,
    )


def match_material(text: str) -> tuple[str, str] | None:
    """Find the first catalogue material whose alias appears in `text`.

    Returns (material_id, matched_alias). Longer aliases are tried first so
    e.g. '316L' wins over a bare '316'.
    """
    haystack = text.upper()
    candidates = [
        (alias.upper(), m.id) for m in materials().values() for alias in m.aliases
    ]
    candidates.sort(key=lambda c: len(c[0]), reverse=True)
    for alias, mid in candidates:
        if re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", haystack):
            return mid, alias
    return None
