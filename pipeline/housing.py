"""Phase 5 assembly: housing sites, rent display, commutes, detail files.

Contract: docs/map/ARCHITECTURE.md, "Phase 5 housing and commute".
"""
from __future__ import annotations

import re
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


CONFIDENCE_RANK = {"published": 0, "reported": 1, "estimated": 2}

_STATE_ZIP = re.compile(r"^(?:(?P<town>.*?)\s+)?(?P<state>[A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")


def town_from_address(address: Optional[str]) -> Optional[str]:
    """The town of a US street address written "street, town, ST 12345" (or "street, town ST 12345"); None when the
    address does not have that shape. Reads only the sourced address text."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if parts and parts[-1].upper() in ("USA", "US", "UNITED STATES"):
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    m = _STATE_ZIP.match(parts[-1])
    if not m:
        return None
    town = m.group("town") if m.group("town") else (parts[-2] if len(parts) >= 3 else None)
    if not town or any(ch.isdigit() for ch in town):
        return None
    return town


def site_town(site: HousingSite) -> Optional[str]:
    """The site's own sourced town, else the town of its publicly listed address."""
    if site.town:
        return site.town
    return town_from_address(site.address.value) if site.address is not None else None


def _site_rent(site: HousingSite, site_reported: dict) -> Optional[dict]:
    """Best of the site's own rent and its report aggregate, Published > Reported > Estimated (never the baseline)."""
    cands = []
    if site.rent_monthly is not None:
        cands.append(site.rent_monthly.model_dump(mode="json"))
    if site_reported.get(site.id) is not None:
        cands.append(site_reported[site.id])
    return min(cands, key=lambda r: CONFIDENCE_RANK[r["confidence"]]) if cands else None


def display_rent(site: HousingSite, baseline: Optional[dict], preference: Sequence[str],
                 reported: Optional[dict] = None) -> Optional[dict]:
    """Published > Reported > Estimated: a published site rent, else the site's aggregate of 3+ approved
    reports, else an estimated site rent, else the county baseline as an estimated range; else None."""
    best = _site_rent(site, {site.id: reported} if reported else {})
    if best is not None:
        return best
    pick = baseline_value(baseline, preference)
    if pick is None:
        return None
    key, s = pick
    return {"value": {"low": s["value"], "high": s["value"]}, "source_id": s["source_id"], "as_of": s["as_of"],
            "confidence": "estimated",
            "note": f"county baseline ({BASELINE_LABEL[key]}, {baseline['county_name']}); this site has no published rent"}


def cheapest_employee_site(sites: Sequence[HousingSite], site_reported: Optional[dict] = None
                           ) -> Optional[tuple[HousingSite, dict]]:
    """Employee housing (resort owned or master lease) with the lowest published or reported rent low."""
    site_reported = site_reported or {}
    cands = [(s, r) for s in sites if s.type in EMPLOYEE_HOUSING and (r := _site_rent(s, site_reported))]
    return min(cands, key=lambda sr: (sr[1]["value"]["low"], sr[0].id)) if cands else None


def employee_rent_mean(sites: Sequence[HousingSite], site_reported: Optional[dict] = None
                       ) -> Optional[tuple[float, list[str], list[str]]]:
    """Mean of the employee housing (resort owned, master lease) rent mids, display priority applied.
    Returns (mean, site ids, source ids) or None when no employee site has a rent."""
    site_reported = site_reported or {}
    rows = [(s, r) for s in sites if s.type in EMPLOYEE_HOUSING and (r := _site_rent(s, site_reported))]
    if not rows:
        return None
    mids = [(r["value"]["low"] + r["value"]["high"]) / 2 for _, r in rows]
    return sum(mids) / len(mids), [s.id for s, _ in rows], sorted({r["source_id"] for _, r in rows})


def rent_input(sites: Sequence[HousingSite], baseline: Optional[dict], order: Sequence[str],
               preference: Sequence[str], site_reported: Optional[dict] = None, area_reported: Optional[dict] = None
               ) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Housing based rent steps: (monthly USD, basis, source ids) for an order of employee_housing, reported_area and
    baseline keys. Lucrativeness now takes its median rent from pipeline/rent_used.py (owner decision 2026-10-14).

    Steps (the former owner decision 4 default was employee_housing, acs_median, fmr_one_br):
    * employee_housing: mean of the employee housing rent mids; basis "employee_housing_mean:<site ids>"
    * reported_area: the area rent from 3+ approved reports (midpoint); basis "reported_area"
    * acs_median, fmr_studio, fmr_one_br, fmr_two_br: that county baseline value; basis is the key
    * baseline: the first of ``rent.baseline_preference``
    Several sources are joined with ", ".
    """
    for step in order:
        if step == "employee_housing":
            hit = employee_rent_mean(sites, site_reported)
            if hit is not None:
                return hit[0], "employee_housing_mean:" + ",".join(hit[1]), ", ".join(hit[2])
        elif step == "reported_area":
            if area_reported is not None:
                v = area_reported["value"]
                return (v["low"] + v["high"]) / 2, "reported_area", area_reported["source_id"]
        else:
            pick = baseline_value(baseline, preference if step == "baseline" else [step])
            if pick is not None:
                return float(pick[1]["value"]), pick[0], pick[1]["source_id"]
    return None, None, None


def housing_summary(sites: Sequence[HousingSite], has_transit: bool, site_reported: Optional[dict] = None) -> Optional[dict]:
    """Counts and the cheapest published or reported site rent (never the baseline)."""
    if not sites:
        return None
    site_reported = site_reported or {}
    priced = [(s, r) for s in sites if (r := _site_rent(s, site_reported))]
    cheapest = min(priced, key=lambda sr: (sr[1]["value"]["low"], sr[0].id)) if priced else None
    return {"site_count": len(sites),
            "cheapest_rent_monthly_low": cheapest[1]["value"]["low"] if cheapest else None,
            "cheapest_site_id": cheapest[0].id if cheapest else None, "has_transit": has_transit}


def assemble_detail(resort: dict, sites: Sequence[HousingSite], lifts_fc: dict, baseline: Optional[dict],
                    transit_extracts: Sequence[dict], router: Any, defs: Any, header: dict,
                    site_reported: Optional[dict] = None, area_reported: Optional[dict] = None) -> dict:
    """data/build/detail/<resort_id>.json for one Tier A resort (unvalidated dict)."""
    base = (resort["lat"], resort["lon"])
    tdefs = defs.transit
    anchors = [base] + [(s.lat, s.lon) for s in sites]
    routes: list[dict] = []
    stops: list[dict] = []
    tsources: list[str] = []
    feeds: list[dict] = []
    for ex in transit_extracts:
        hit = nearby(ex, anchors, tdefs.search_radius_km)
        if hit:
            routes += hit["routes"]
            stops += hit["stops"]
            tsources.append(ex["source"]["id"])
            feed = ex.get("feed") or {}
            if feed.get("as_of"):
                ops = list(dict.fromkeys((r["agency_name"], r.get("agency_url")) for r in hit["routes"] if r.get("agency_name")))
                flags = ex.get("schedule") or {}
                feeds.append({"source_id": ex["source"]["id"], "agency": ex["agency"], "as_of": feed["as_of"],
                              "valid_to": feed.get("valid_to"), "agencies": [{"name": n, "url": u} for n, u in ops],
                              "covers_winter": flags.get("covers_winter"), "ended": flags.get("ended")})
    transit = ({"routes": routes, "stops": stops, "source_ids": sorted(set(tsources)),
                "feeds": sorted(feeds, key=lambda f: f["source_id"])} if stops else None)

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
        housing.append({**s.model_dump(mode="json"), "town": site_town(s),
                        "rent_monthly": display_rent(s, baseline, defs.rent.baseline_preference,
                                                     (site_reported or {}).get(s.id)),
                        "commutes": commutes})

    isochrones = []
    if router is not None:
        isochrones = [{"minutes": i["minutes"], "mode": "drive", "geometry": i["geometry"], "source_id": router.source_id}
                      for i in router.isochrones(base)]
    return {**header, "resort_id": resort["id"],
            "base_area": {"lat": resort["lat"], "lon": resort["lon"], "source_id": resort["location_source_id"]},
            "lifts": lifts_fc, "housing": housing, "rent_baseline": baseline, "rent_reported": area_reported,
            "transit": transit,
            "isochrones": isochrones}
