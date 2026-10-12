"""Phase 5 assembly: housing sites, rent display, commutes, detail files.

Contract: docs/map/ARCHITECTURE.md, "Phase 5 housing and commute".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

import yaml
from pydantic import ValidationError

from .rent import baseline_value
from .schemas.housing import EMPLOYEE_HOUSING, HousingBatch, HousingSite
from .sources import SourceRegistry
from .transit import bus_commute, nearby

BASELINE_LABEL = {"fmr_studio": "HUD FMR studio", "fmr_one_br": "HUD FMR one bedroom",
                  "fmr_two_br": "HUD FMR two bedroom", "acs_median": "ACS median gross rent"}


def load_housing(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str
                 ) -> tuple[dict[str, list[HousingSite]], list[str]]:
    """Validate data/raw/housing/*.yaml. Returns ({resort_id: sites}, errors)."""
    from .tier_a import _register_sources, _yaml_files

    errors: list[str] = []
    files = _yaml_files(raw_dir / "housing")
    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    out: dict[str, list[HousingSite]] = {}
    seen: dict[str, str] = {}
    for p in files:
        if not isinstance(raws.get(p), dict):
            continue
        try:
            batch = HousingBatch.model_validate(raws[p], context=registry.context())
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                where = rel(p)
                if len(loc) >= 2 and loc[0] == "sites" and isinstance(loc[1], int):
                    try:
                        where = f"{rel(p)} site '{raws[p]['sites'][loc[1]].get('id')}'"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{where}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
            continue
        for s in batch.sites:
            if s.id in seen:
                errors.append(f"{rel(p)} site '{s.id}': id also used in {seen[s.id]}")
            seen[s.id] = rel(p)
            if s.resort_id not in known_ids:
                errors.append(f"{rel(p)} site '{s.id}': resort_id '{s.resort_id}' matches no resort in this build")
            out.setdefault(s.resort_id, []).append(s)
    for sites in out.values():
        sites.sort(key=lambda s: s.id)
    return out, errors


def display_rent(site: HousingSite, baseline: Optional[dict], preference: Sequence[str]) -> Optional[dict]:
    """The site's own rent; else the baseline as an estimated range with the baseline source; else None."""
    if site.rent_monthly is not None:
        return site.rent_monthly.model_dump(mode="json")
    pick = baseline_value(baseline, preference)
    if pick is None:
        return None
    key, s = pick
    return {"value": {"low": s["value"], "high": s["value"]}, "source_id": s["source_id"], "as_of": s["as_of"],
            "confidence": "estimated",
            "note": f"county baseline ({BASELINE_LABEL[key]}, {baseline['county_name']}); this site has no published rent"}


def cheapest_employee_site(sites: Sequence[HousingSite]) -> Optional[HousingSite]:
    """Employee housing (resort owned or master lease) with the lowest published rent low."""
    cands = [s for s in sites if s.type in EMPLOYEE_HOUSING and s.rent_monthly is not None]
    return min(cands, key=lambda s: (s.rent_monthly.value.low, s.id)) if cands else None


def rent_input(sites: Sequence[HousingSite], baseline: Optional[dict], order: Sequence[str],
               preference: Sequence[str]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Lucrativeness rent: (monthly USD, basis, source_id) following config ``rent.lucrativeness_rent``.

    basis is "employee_housing:<site id>" or the baseline key (fmr_studio, acs_median, ...).
    """
    for step in order:
        if step == "employee_housing":
            site = cheapest_employee_site(sites)
            if site is not None:
                return site.rent_monthly.value.low, f"employee_housing:{site.id}", site.rent_monthly.source_id
        else:
            pick = baseline_value(baseline, preference)
            if pick is not None:
                return float(pick[1]["value"]), pick[0], pick[1]["source_id"]
    return None, None, None


def housing_summary(sites: Sequence[HousingSite], has_transit: bool) -> Optional[dict]:
    if not sites:
        return None
    priced = [s for s in sites if s.rent_monthly is not None]
    cheapest = min(priced, key=lambda s: (s.rent_monthly.value.low, s.id)) if priced else None
    return {"site_count": len(sites),
            "cheapest_rent_monthly_low": cheapest.rent_monthly.value.low if cheapest else None,
            "cheapest_site_id": cheapest.id if cheapest else None, "has_transit": has_transit}


def assemble_detail(resort: dict, sites: Sequence[HousingSite], lifts_fc: dict, baseline: Optional[dict],
                    transit_extracts: Sequence[dict], router: Any, defs: Any, header: dict) -> dict:
    """data/build/detail/<resort_id>.json for one Tier A resort (unvalidated dict)."""
    base = (resort["lat"], resort["lon"])
    tdefs = defs.transit
    anchors = [base] + [(s.lat, s.lon) for s in sites]
    routes: list[dict] = []
    stops: list[dict] = []
    tsources: list[str] = []
    for ex in transit_extracts:
        hit = nearby(ex, anchors, tdefs.search_radius_km)
        if hit:
            routes += hit["routes"]
            stops += hit["stops"]
            tsources.append(ex["source"]["id"])
    transit = {"routes": routes, "stops": stops, "source_ids": sorted(set(tsources))} if stops else None

    housing = []
    for s in sites:
        commutes = []
        if router is not None:
            drive = router.route((s.lat, s.lon), base)
            if drive:
                commutes.append({"mode": "drive", **drive, "transit_route_ids": [], "source_id": router.source_id})
        bus = [c for ex in transit_extracts
               if (c := bus_commute((s.lat, s.lon), base, ex, tdefs.walk_speed_kmh, tdefs.max_walk_km))]
        if bus:
            commutes.append(min(bus, key=lambda c: c["minutes"]))
        housing.append({**s.model_dump(mode="json"),
                        "rent_monthly": display_rent(s, baseline, defs.rent.baseline_preference),
                        "commutes": commutes})

    isochrones = []
    if router is not None:
        isochrones = [{"minutes": i["minutes"], "mode": "drive", "geometry": i["geometry"], "source_id": router.source_id}
                      for i in router.isochrones(base)]
    return {**header, "resort_id": resort["id"],
            "base_area": {"lat": resort["lat"], "lon": resort["lon"], "source_id": resort["location_source_id"]},
            "lifts": lifts_fc, "housing": housing, "rent_baseline": baseline, "transit": transit,
            "isochrones": isochrones}
