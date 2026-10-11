"""Phase 3 climate ETL entry point.

    python -m pipeline.climate.run [--resorts data/build/resorts.json] [--tier A|all]
                                   [--only id,...] [--start-year Y --end-year Y]

For each resort: fetch (or reuse cached) Open Meteo ERA5 daily history,
aggregate to 12 ClimateMonth rows, add SNOTEL storm density where a usable
station exists (Tier A by default) and a temperature based estimate elsewhere,
validate against pipeline.schemas.climate, and write:

* ``data/build/climate/{resort_id}.json``  (contract in docs/map/ARCHITECTURE.md)
* ``data/build/climate/sources.json``      ({version, generated_at, sources}; pipeline/build.py
  merges these entries into data/build/sources.json)
* ``data/build/climate/provenance.json``   (per resort audit trail: ERA5 grid cell and
  elevation, SNOTEL station, distance, selection mode, QC counts)

Nothing is written to data/build when the network blocks a needed host.
Exit codes: 0 every resort written, 1 some resorts failed or deferred,
2 configuration or input error, 3 network blocked.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from pipeline.climate import http, open_meteo, snotel
from pipeline.climate.aggregate import aggregate_months, detect_storms
from pipeline.climate.definitions import ClimateSettings, DefinitionsError, load_definitions
from pipeline.climate.density import MonthDensity, monthly_density, pick_month_density
from pipeline.climate.density_estimate import SOURCES as ESTIMATE_SOURCES
from pipeline.climate.density_estimate import estimate_storm_densities
from pipeline.climate.seasons import default_period, period_dates
from pipeline.climate.units import m_to_ft
from pipeline.schemas.climate import ClimateFile

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = 1
REAL_RUN_COMMAND = "python -m pipeline.climate.run --tier all"


def _round(value: Optional[float], ndigits: int) -> Optional[float]:
    return None if value is None else round(value, ndigits)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def resort_elevation_ft(resort: dict[str, Any]) -> Optional[float]:
    """Sourced base elevation in feet when the resort record carries one."""
    for container in (resort, resort.get("size") or {}):
        for key in ("base_elevation_ft", "elevation_ft"):
            v = _number(container.get(key))
            if v is not None:
                return v
    return None


def load_resorts(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    resorts = data["resorts"] if isinstance(data, dict) else data
    for r in resorts:
        for key in ("id", "lat", "lon"):
            if r.get(key) is None:
                raise ValueError(f"resort {r.get('id')!r} has no {key}")
    return resorts


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- per resort


def choose_snotel(
    resort: dict[str, Any],
    settings: ClimateSettings,
    stations: list[snotel.Station],
    cache_dir: Path,
    start: date,
    end: date,
    offline: bool,
) -> tuple[Optional[snotel.StationEvaluation], dict[str, Any]]:
    elev = resort_elevation_ft(resort)
    cands = snotel.select_candidates(resort["lat"], resort["lon"], elev, stations, settings.snotel, (start, end))
    info: dict[str, Any] = {
        "selection_mode": "distance_only" if elev is None else "distance_and_elevation",
        "resort_elevation_ft": elev,
        "radius_km": settings.snotel.radius_km,
        "tried": [],
    }
    if elev is None:
        info["note"] = "resort elevation unknown, station chosen by distance only"
    for cand in cands:
        tried = {
            "triplet": cand.station.triplet,
            "name": cand.station.name,
            "distance_km": round(cand.distance_km, 2),
            "station_elevation_ft": cand.station.elevation_ft,
            "elevation_diff_ft": cand.elevation_diff_ft,
        }
        info["tried"].append(tried)
        try:
            if offline:
                data = snotel.load_station_daily(cache_dir, cand.station.triplet, start, end)
                if data is None:
                    tried["result"] = "not cached (offline)"
                    continue
            else:
                data = snotel.fetch_station_daily(cache_dir, cand.station.triplet, start, end)
            ev = snotel.evaluate_station(cand.station, data["response"], settings.snotel)
        except http.NetworkBlocked:
            raise
        except Exception as e:  # try the next station
            tried["result"] = f"error: {e}"
            continue
        tried["qc_counts"] = ev.qc_counts
        tried["valid_storms"] = len(ev.valid)
        if len(ev.valid) >= settings.snotel.min_valid_storms:
            tried["result"] = "used"
            info["station"] = cand.station.triplet
            return ev, info
        tried["result"] = f"too few valid storms ({len(ev.valid)} < {settings.snotel.min_valid_storms})"
    if not cands:
        info["result"] = "no SNOTEL station within radius and elevation band"
    return None, info


def build_resort(
    resort: dict[str, Any],
    settings: ClimateSettings,
    start_year: int,
    end_year: int,
    om_meta: dict[str, Any],
    daily: list,
    snotel_eval: Optional[snotel.StationEvaluation],
    generated_at: str,
) -> tuple[ClimateFile, dict[str, Any], list[str]]:
    """Returns the validated climate file, provenance and source ids used."""
    rid = resort["id"]
    stats = aggregate_months(
        daily,
        start_year,
        end_year,
        powder_day_in=settings.powder_day_in,
        banner_day_in=settings.banner_day_in,
        storm_min_daily_in=settings.storm_min_daily_in,
        min_month_coverage=settings.min_month_coverage,
    )
    start, end = period_dates(start_year, end_year)
    storms = detect_storms([r for r in daily if start <= r.date <= end], settings.storm_min_daily_in)
    estimated = monthly_density(
        estimate_storm_densities(storms, settings.density_estimate_method),
        settings.dry_below_pct,
        settings.moist_above_pct,
        settings.min_storms_per_month,
    )
    snotel_monthly: Optional[dict[int, MonthDensity]] = None
    if snotel_eval is not None:
        snotel_monthly = monthly_density(
            [(s.month, s.density_pct) for s in snotel_eval.valid],
            settings.dry_below_pct,
            settings.moist_above_pct,
            settings.min_storms_per_month,
        )

    months = []
    used_sources: set[str] = set()
    month_prov = {}
    for st in stats:
        dens, dsource = pick_month_density(st.month, snotel_monthly, estimated)
        if dsource:
            used_sources.add(dsource)
        months.append(
            {
                "resort_id": rid,
                "month": st.month,
                "snowfall_in": _round(st.snowfall_in, 1),
                "powder_days": _round(st.powder_days, 2),
                "banner_days": _round(st.banner_days, 2),
                "avg_storm_depth_in": _round(st.avg_storm_depth_in, 1),
                "density_pct_median": _round(dens.median_pct, 1) if dens else None,
                "density_mix": dict(dens.mix) if dens else None,
                "density_source": dsource,
                "temp_high_f": _round(st.temp_high_f, 1),
                "temp_low_f": _round(st.temp_low_f, 1),
                "wind_mph": _round(st.wind_mph, 1),
                "sun_pct": _round(st.sun_pct, 1),
            }
        )
        month_prov[str(st.month)] = {
            "seasons_used": st.seasons_used,
            "era5_storms": st.storm_count,
            "density_storms": dens.n_storms if dens else 0,
        }

    source_ids = [open_meteo.SOURCE_ID]
    if "snotel" in used_sources and snotel_eval is not None:
        source_ids.append(snotel.source_entry(snotel_eval.station, date.today())["id"])
    if "estimated" in used_sources:
        source_ids.append(ESTIMATE_SOURCES[settings.density_estimate_method]["id"])

    climate = ClimateFile.model_validate(
        {
            "version": VERSION,
            "generated_at": generated_at,
            "resort_id": rid,
            "source_ids": source_ids,
            "period": {"start_year": start_year, "end_year": end_year},
            "months": months,
        }
    )
    elevation_m = om_meta.get("elevation_m")
    provenance = {
        "period": {"start_year": start_year, "end_year": end_year, "seasons": end_year - start_year},
        "open_meteo": {
            "model": open_meteo.MODEL,
            "requested_lat": resort["lat"],
            "requested_lon": resort["lon"],
            "grid_latitude": om_meta.get("grid_latitude"),
            "grid_longitude": om_meta.get("grid_longitude"),
            "elevation_m": elevation_m,
            "elevation_ft": _round(m_to_ft(elevation_m), 0) if elevation_m is not None else None,
            "elevation_source": "Open Meteo 90 m DEM default (no elevation parameter sent)",
            "timezone": om_meta.get("timezone"),
            "fetched_at": om_meta.get("fetched_at"),
        },
        "density_estimate_method": settings.density_estimate_method,
        "era5_storms": len(storms),
        "months": month_prov,
    }
    return climate, provenance, source_ids


# ---------------------------------------------------------------- CLI


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pipeline.climate.run", description=__doc__.split("\n\n")[0])
    p.add_argument("--resorts", type=Path, default=REPO_ROOT / "data/build/resorts.json")
    p.add_argument("--tier", choices=["A", "all"], default="all", help="resorts to process")
    p.add_argument("--only", default=None, help="comma separated resort ids")
    p.add_argument("--start-year", type=int, default=None, help="first October of the period")
    p.add_argument("--end-year", type=int, default=None, help="last September of the period")
    p.add_argument("--snotel-tier", choices=["A", "all", "none"], default="A", help="resorts that get SNOTEL density")
    p.add_argument("--definitions", type=Path, default=REPO_ROOT / "config/definitions.yaml")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "data/build")
    p.add_argument("--cache", type=Path, default=REPO_ROOT / "data/raw/climate")
    p.add_argument("--offline", action="store_true", help="use cached responses only")
    p.add_argument("--plan", action="store_true", help="print the fetch plan and API weight, write nothing")
    p.add_argument("--daily-budget", type=float, default=open_meteo.LIMIT_PER_DAY * open_meteo.SAFETY)
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None, *, today: Optional[date] = None, log: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    today = today or date.today()

    try:
        settings = load_definitions(args.definitions)
    except DefinitionsError as e:
        log(f"climate: {e}")
        return 2
    try:
        resorts = load_resorts(args.resorts)
    except (OSError, ValueError, KeyError) as e:
        log(f"climate: cannot read resorts from {args.resorts}: {e}")
        return 2
    if args.tier == "A":
        resorts = [r for r in resorts if r.get("tier") == "A"]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - {r["id"] for r in resorts}
        if unknown:
            log(f"climate: unknown resort ids: {', '.join(sorted(unknown))}")
            return 2
        resorts = [r for r in resorts if r["id"] in wanted]
    if not resorts:
        log("climate: no resorts selected")
        return 2

    if args.start_year is None or args.end_year is None:
        d_start, d_end = default_period(today, settings.period_seasons, settings.era5_lag_days)
        start_year = args.start_year if args.start_year is not None else d_start
        end_year = args.end_year if args.end_year is not None else d_end
    else:
        start_year, end_year = args.start_year, args.end_year
    try:
        start, end = period_dates(start_year, end_year)
    except ValueError as e:
        log(f"climate: {e}")
        return 2

    om_cache = args.cache / "open_meteo"
    sn_cache = args.cache / "snotel"
    snotel_ids = {
        r["id"]
        for r in resorts
        if args.snotel_tier == "all" or (args.snotel_tier == "A" and r.get("tier") == "A")
    }
    locs = [open_meteo.Location(r["id"], float(r["lat"]), float(r["lon"])) for r in resorts]
    need_om = [l for l in locs if open_meteo.load_daily(om_cache, l, start_year, end_year) is None]
    stations = snotel.load_stations(sn_cache) if snotel_ids else []
    need_snotel = bool(snotel_ids) and (
        stations is None
        or any(
            snotel.load_station_daily(sn_cache, c.station.triplet, start, end) is None
            for r in resorts
            if r["id"] in snotel_ids
            for c in snotel.select_candidates(
                r["lat"], r["lon"], resort_elevation_ft(r), stations, settings.snotel, (start, end)
            )[:1]
        )
    )

    weight = open_meteo.estimated_total_weight(len(need_om), start_year, end_year)
    log(
        f"climate: {len(resorts)} resorts, period Oct {start_year} to Sep {end_year} "
        f"({end_year - start_year} seasons); {len(need_om)} need Open Meteo "
        f"(about {weight:,.0f} weighted calls, {open_meteo.days_needed(len(need_om), start_year, end_year)} "
        f"day(s) at {args.daily_budget:,.0f} per day); SNOTEL fetch needed: {need_snotel}"
    )
    if args.plan:
        return 0

    if (need_om or need_snotel) and not args.offline:
        hosts = []
        try:
            if need_om:
                hosts.append("archive-api.open-meteo.com")
                open_meteo.preflight()
            if need_snotel:
                hosts.append("wcc.sc.egov.usda.gov")
                snotel.preflight()
        except http.RateLimited as e:
            log(
                f"climate: {hosts[-1]} is rate limiting this IP ({e}). Nothing was written. "
                f"Wait for the limit to reset, then run:\n"
                f"  {REAL_RUN_COMMAND}"
            )
            return 3
        except http.NetworkBlocked as e:
            log(
                f"climate: network blocked ({e}). Nothing was written.\n"
                f"Allow {' and '.join(hosts)} (or rerun with --offline to use the cache only), then run:\n"
                f"  {REAL_RUN_COMMAND}"
            )
            return 3

    failures: dict[str, str] = {}
    deferred: dict[str, str] = {}
    try:
        if need_om and not args.offline:
            budget = open_meteo.WeightBudget(om_cache / "_usage.json", per_day=args.daily_budget)
            budget.events.append((datetime.now(timezone.utc).timestamp(), 1.0))  # preflight call
            try:
                failures.update(open_meteo.fetch_locations(need_om, start_year, end_year, om_cache, budget, log=log))
            except (open_meteo.BudgetExhausted, http.RateLimited) as e:
                log(f"climate: {e}")
                for l in need_om:
                    deferred.setdefault(l.resort_id, str(e))
        if snotel_ids and stations is None and not args.offline:
            stations = snotel.fetch_stations(sn_cache)

        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        outputs: dict[str, dict[str, Any]] = {}
        provenance: dict[str, Any] = {}
        sources: dict[str, dict[str, Any]] = {}
        for resort in resorts:
            rid = resort["id"]
            if rid in failures:
                continue
            loc = open_meteo.Location(rid, float(resort["lat"]), float(resort["lon"]))
            loaded = open_meteo.load_daily(om_cache, loc, start_year, end_year)
            if loaded is None:
                if rid not in deferred:
                    failures[rid] = "Open Meteo history not cached" + (" (offline)" if args.offline else "")
                continue
            deferred.pop(rid, None)
            om_meta, daily = loaded
            try:
                ev, sn_info = (None, {"result": "not attempted for this tier"})
                if rid in snotel_ids:
                    if stations is None:
                        sn_info = {"result": "station list not cached (offline)"}
                    else:
                        ev, sn_info = choose_snotel(resort, settings, stations, sn_cache, start, end, args.offline)
                climate, prov, source_ids = build_resort(
                    resort, settings, start_year, end_year, om_meta, daily, ev, generated_at
                )
            except http.NetworkBlocked:
                raise
            except (ValidationError, ValueError, KeyError, TypeError) as e:
                failures[rid] = f"{type(e).__name__}: {e}"
                continue
            prov["snotel"] = sn_info
            outputs[rid] = climate.model_dump(mode="json")
            provenance[rid] = prov
            accessed = today.isoformat()
            if open_meteo.SOURCE_ID in source_ids:
                sources[open_meteo.SOURCE_ID] = dict(open_meteo.SOURCE, accessed_date=accessed)
            if ev is not None:
                entry = snotel.source_entry(ev.station, today)
                if entry["id"] in source_ids:
                    sources[entry["id"]] = entry
            est = ESTIMATE_SOURCES[settings.density_estimate_method]
            if est["id"] in source_ids:
                sources[est["id"]] = dict(est, accessed_date=accessed)
    except http.NetworkBlocked as e:
        log(f"climate: network blocked mid run ({e}). Nothing was written to {args.out}; cached responses are kept.")
        return 3

    if outputs:
        write_outputs(args.out, outputs, sources, provenance, generated_at)

    log(f"climate: wrote {len(outputs)} resort file(s) to {args.out / 'climate'}")
    for rid, prov in sorted(provenance.items()):
        sn = prov.get("snotel", {})
        dens = sorted({m["density_source"] for m in outputs[rid]["months"] if m["density_source"]})
        log(f"  ok        {rid}: density {'/'.join(dens) or 'none'}; snotel {sn.get('station') or sn.get('result', 'none')}")
    for rid, why in sorted(deferred.items()):
        log(f"  deferred  {rid}: {why}")
    for rid, why in sorted(failures.items()):
        log(f"  failed    {rid}: {why}")
    return 0 if not failures and not deferred else 1


def write_outputs(
    out: Path,
    outputs: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
    generated_at: str,
) -> None:
    climate_dir = Path(out) / "climate"
    for rid, data in outputs.items():
        _write_json(climate_dir / f"{rid}.json", data)

    src_path = climate_dir / "sources.json"
    merged: dict[str, dict[str, Any]] = {}
    if src_path.exists():
        for s in json.loads(src_path.read_text(encoding="utf-8")).get("sources", []):
            merged[s["id"]] = s
    merged.update(sources)
    _write_json(
        src_path,
        {"version": VERSION, "generated_at": generated_at, "sources": [merged[k] for k in sorted(merged)]},
    )

    prov_path = climate_dir / "provenance.json"
    prov_all: dict[str, Any] = {}
    if prov_path.exists():
        prov_all = json.loads(prov_path.read_text(encoding="utf-8")).get("resorts", {})
    prov_all.update(provenance)
    _write_json(
        prov_path,
        {"version": VERSION, "generated_at": generated_at, "resorts": {k: prov_all[k] for k in sorted(prov_all)}},
    )


if __name__ == "__main__":
    sys.exit(main())
