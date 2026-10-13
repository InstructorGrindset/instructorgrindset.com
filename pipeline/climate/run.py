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
from pipeline.climate.snotel_snowfall import derive_new_snow, era5_snow_allowed, with_snowfall
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


def resort_elevation_ft(resort: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """(base_ft, summit_ft) from ``resort.elevation``, each a Sourced number or null."""
    elevation = resort.get("elevation") or {}
    if not isinstance(elevation, dict):
        return None, None
    return _number(elevation.get("base_ft")), _number(elevation.get("summit_ft"))


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
) -> tuple[Optional[snotel.StationEvaluation], Optional[dict], dict[str, Any]]:
    """(evaluation, daily series {date: (swe, depth)}, provenance) for the first usable station."""
    elev, summit = resort_elevation_ft(resort)
    cands = snotel.select_candidates(
        resort["lat"], resort["lon"], elev, stations, settings.snotel, (start, end), summit_ft=summit
    )
    info: dict[str, Any] = {
        "selection_mode": "distance_only" if elev is None else "distance_and_elevation",
        "resort_base_ft": elev,
        "resort_summit_ft": summit,
        "radius_km": settings.snotel.radius_km,
        "tried": [],
    }
    if elev is None:
        info["note"] = "resort base elevation unknown, station chosen by distance only"
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
            series = snotel.parse_station_daily(data["response"], cand.station.triplet)
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
            return ev, series, info
        tried["result"] = f"too few valid storms ({len(ev.valid)} < {settings.snotel.min_valid_storms})"
    if not cands:
        info["result"] = "no SNOTEL station within radius and elevation band with enough record"
    return None, None, info


def published_annual(resort: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Published annual snowfall (``resort.snow.annual_avg_in``) and its source id."""
    snow = resort.get("snow") or {}
    v = snow.get("annual_avg_in") if isinstance(snow, dict) else None
    if isinstance(v, dict) and _number(v) is not None:
        return _number(v), v.get("source_id")
    return None, None


def validate_annual(
    annual_in: Optional[float], published_in: Optional[float], flag_pct: float, suppress_pct: float
) -> dict[str, Any]:
    if annual_in is None or published_in is None or published_in <= 0:
        return {"annual_in": annual_in, "published_in": published_in, "diff_pct": None, "status": "no comparison"}
    diff = 100.0 * (annual_in - published_in) / published_in
    status = "ok"
    if abs(diff) > suppress_pct:
        status = "suppressed"
    elif abs(diff) > flag_pct:
        status = "flagged"
    return {"annual_in": round(annual_in, 1), "published_in": published_in, "diff_pct": round(diff, 1), "status": status}


SNOW_KEYS = ("snowfall_in", "powder_days", "banner_days", "avg_storm_depth_in")


def build_resort(
    resort: dict[str, Any],
    settings: ClimateSettings,
    start_year: int,
    end_year: int,
    om_meta: dict[str, Any],
    daily: list,
    snotel_eval: Optional[snotel.StationEvaluation],
    generated_at: str,
    snotel_series: Optional[dict] = None,
    grid_elevation_m: Optional[float] = None,
) -> tuple[ClimateFile, dict[str, Any], list[str]]:
    """Returns the validated climate file, provenance and source ids used."""
    rid = resort["id"]
    start, end = period_dates(start_year, end_year)
    n_seasons = end_year - start_year
    base_ft, summit_ft = resort_elevation_ft(resort)
    vertical_ft = _number((resort.get("size") or {}).get("vertical_ft"))
    grid_ft = _round(m_to_ft(grid_elevation_m), 0) if grid_elevation_m is not None else None

    snotel_monthly: Optional[dict[int, MonthDensity]] = None
    if snotel_eval is not None:
        snotel_monthly = monthly_density(
            [(s.month, s.density_pct) for s in snotel_eval.valid],
            settings.dry_below_pct,
            settings.moist_above_pct,
            settings.min_storms_per_month,
        )

    # Snowfall source: SNOTEL SWE where usable, else ERA5 where its grid fits the resort, else null.
    snow_prov: dict[str, Any] = {"era5_grid_elevation_ft": grid_ft}
    snow_daily = daily
    snowfall_source: Optional[str] = None
    null_reason: Optional[str] = None
    swe_used_estimate = False
    if snotel_eval is not None and snotel_series is not None:
        sw = derive_new_snow(
            snotel_series,
            {r.date: r for r in daily},
            om_meta.get("elevation_m"),
            snotel_eval.station.elevation_ft,
            {m: d.median_pct for m, d in (snotel_monthly or {}).items()},
            settings.snotel,
            start_year,
            end_year,
            settings.min_month_coverage,
            settings.density_estimate_method,
        )
        snow_prov.update(
            station=snotel_eval.station.triplet,
            station_elevation_ft=snotel_eval.station.elevation_ft,
            seasons_with_data=sw.seasons_with_data,
            swe_annual_in=_round(sw.swe_annual_in, 1),
            depth_gain_annual_in=_round(sw.depth_gain_annual_in, 1),
            qc_counts=sw.qc_counts,
            density_from=sw.density_from,
        )
        if sw.seasons_with_data >= min(settings.snotel.min_record_seasons, n_seasons):
            snow_daily = with_snowfall(daily, sw.new_snow_in)
            snowfall_source = "snotel_swe"
            swe_used_estimate = sw.density_from.get("estimated", 0) > 0
            snow_prov["reason"] = f"SNOTEL {snotel_eval.station.triplet} daily SWE gains"
        else:
            snow_prov["snotel_rejected"] = (
                f"{sw.seasons_with_data} seasons of usable daily SWE, below {settings.snotel.min_record_seasons}"
            )
    if snowfall_source is None:
        ok, reason = era5_snow_allowed(
            base_ft,
            summit_ft,
            vertical_ft,
            grid_ft,
            settings.era5_snow_elevation_band_ft,
            settings.low_relief_max_vertical_ft,
            settings.low_relief_max_base_ft,
        )
        snow_prov["reason"] = reason
        if ok:
            snowfall_source = "era5"
        else:
            null_reason = reason

    stats = aggregate_months(
        snow_daily,
        start_year,
        end_year,
        powder_day_in=settings.powder_day_in,
        banner_day_in=settings.banner_day_in,
        storm_min_daily_in=settings.storm_min_daily_in,
        min_month_coverage=settings.min_month_coverage,
    )
    storms = detect_storms([r for r in snow_daily if start <= r.date <= end and r.snowfall_in is not None], settings.storm_min_daily_in)
    estimated = monthly_density(
        estimate_storm_densities(storms, settings.density_estimate_method),
        settings.dry_below_pct,
        settings.moist_above_pct,
        settings.min_storms_per_month,
    )

    # Validation against the published annual snowfall.
    annual = None
    if snowfall_source is not None:
        vals = [_round(st.snowfall_in, 1) for st in stats if st.snowfall_in is not None]
        annual = sum(vals) if vals else None
    pub, pub_source = published_annual(resort)
    validation = validate_annual(annual, pub, settings.validation_flag_pct, settings.validation_suppress_pct)
    validation["published_source_id"] = pub_source
    validation["snowfall_source"] = snowfall_source
    if validation["status"] == "suppressed":
        null_reason = (
            f"derived annual snowfall {validation['annual_in']:,.0f} in differs from the published "
            f"{pub:,.0f} in by {validation['diff_pct']:+.0f} percent, over the "
            f"{settings.validation_suppress_pct:.0f} percent limit"
        )
        snowfall_source = None

    months = []
    used_sources: set[str] = set()
    month_prov = {}
    for st in stats:
        dens, dsource = pick_month_density(st.month, snotel_monthly, estimated)
        if dsource:
            used_sources.add(dsource)
        row = {
            "resort_id": rid,
            "month": st.month,
            "snowfall_in": _round(st.snowfall_in, 1),
            "powder_days": _round(st.powder_days, 2),
            "banner_days": _round(st.banner_days, 2),
            "avg_storm_depth_in": _round(st.avg_storm_depth_in, 1),
            "snowfall_source": snowfall_source,
            "density_pct_median": _round(dens.median_pct, 1) if dens else None,
            "density_mix": dict(dens.mix) if dens else None,
            "density_source": dsource,
            "temp_high_f": _round(st.temp_high_f, 1),
            "temp_low_f": _round(st.temp_low_f, 1),
            "wind_mph": _round(st.wind_mph, 1),
            "sun_pct": _round(st.sun_pct, 1),
        }
        if snowfall_source is None or row["snowfall_in"] is None:
            row.update({k: None for k in SNOW_KEYS}, snowfall_source=None)
        months.append(row)
        month_prov[str(st.month)] = {
            "seasons_used": st.seasons_used,
            "storms": st.storm_count,
            "density_storms": dens.n_storms if dens else 0,
        }
    if snowfall_source is not None and all(m["snowfall_in"] is None for m in months):
        null_reason = "no month had enough days of snowfall data"

    source_ids = [open_meteo.SOURCE_ID]
    if snotel_eval is not None and ("snotel" in used_sources or snowfall_source == "snotel_swe"):
        source_ids.append(snotel.source_entry(snotel_eval.station, date.today())["id"])
    if "estimated" in used_sources or (snowfall_source == "snotel_swe" and swe_used_estimate):
        source_ids.append(ESTIMATE_SOURCES[settings.density_estimate_method]["id"])

    climate = ClimateFile.model_validate(
        {
            "version": VERSION,
            "generated_at": generated_at,
            "resort_id": rid,
            "source_ids": source_ids,
            "confidence": "estimated",
            "snowfall_null_reason": null_reason,
            "period": {"start_year": start_year, "end_year": end_year},
            "months": months,
        }
    )
    elevation_m = om_meta.get("elevation_m")
    snow_prov["snowfall_source"] = snowfall_source
    snow_prov["null_reason"] = null_reason
    provenance = {
        "name": resort.get("name"),
        "tier": resort.get("tier"),
        "period": {"start_year": start_year, "end_year": end_year, "seasons": n_seasons},
        "open_meteo": {
            "model": open_meteo.MODEL,
            "requested_lat": resort["lat"],
            "requested_lon": resort["lon"],
            "grid_latitude": om_meta.get("grid_latitude"),
            "grid_longitude": om_meta.get("grid_longitude"),
            "elevation_m": elevation_m,
            "elevation_ft": _round(m_to_ft(elevation_m), 0) if elevation_m is not None else None,
            "elevation_source": "Open Meteo 90 m DEM default (no elevation parameter sent)",
            "grid_cell_elevation_m": grid_elevation_m,
            "timezone": om_meta.get("timezone"),
            "fetched_at": om_meta.get("fetched_at"),
        },
        "snowfall": snow_prov,
        "validation": validation,
        "density_estimate_method": settings.density_estimate_method,
        "storms": len(storms),
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
    p.add_argument(
        "--om-cache-only",
        action="store_true",
        help="process only resorts whose Open Meteo history is fully cached (no history fetches)",
    )
    p.add_argument(
        "--validation-report",
        type=Path,
        default=REPO_ROOT / "docs/map/climate-validation.md",
        help="markdown report comparing derived and published annual snowfall",
    )
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
    if args.om_cache_only:
        skip = {l.resort_id for l in need_om}
        if skip:
            log(f"climate: --om-cache-only skips {len(skip)} resort(s) without a complete Open Meteo cache")
        resorts = [r for r in resorts if r["id"] not in skip]
        locs = [l for l in locs if l.resort_id not in skip]
        need_om = []
        if not resorts:
            log("climate: no resorts have a complete Open Meteo cache")
            return 1
    need_grid = [l for l in locs if open_meteo.cached_grid_elevation_m(om_cache, l) is None]
    stations = snotel.load_stations(sn_cache) if snotel_ids else []
    need_snotel = bool(snotel_ids) and (
        stations is None
        or any(
            snotel.load_station_daily(sn_cache, c.station.triplet, start, end) is None
            for r in resorts
            if r["id"] in snotel_ids
            for c in snotel.select_candidates(
                r["lat"], r["lon"], resort_elevation_ft(r)[0], stations, settings.snotel, (start, end),
                summit_ft=resort_elevation_ft(r)[1],
            )[:1]
        )
    )

    weight = open_meteo.estimated_total_weight(len(need_om), start_year, end_year)
    log(
        f"climate: {len(resorts)} resorts, period Oct {start_year} to Sep {end_year} "
        f"({end_year - start_year} seasons); {len(need_om)} need Open Meteo "
        f"(about {weight:,.0f} weighted calls, {open_meteo.days_needed(len(need_om), start_year, end_year)} "
        f"day(s) at {args.daily_budget:,.0f} per day); {len(need_grid)} need the ERA5 grid cell height; "
        f"SNOTEL fetch needed: {need_snotel}"
    )
    if args.plan:
        return 0

    if (need_om or need_grid or need_snotel) and not args.offline:
        hosts = []
        try:
            if need_om or need_grid:
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
        if (need_om or need_grid) and not args.offline:
            budget = open_meteo.WeightBudget(om_cache / "_usage.json", per_day=args.daily_budget)
            budget.events.append((datetime.now(timezone.utc).timestamp(), 1.0))  # preflight call
            if need_grid:
                try:
                    open_meteo.fetch_grid_elevations(need_grid, om_cache, budget)
                except (open_meteo.BudgetExhausted, http.RateLimited, http.HttpStatusError) as e:
                    log(f"climate: ERA5 grid cell heights unavailable ({e}); ERA5 snowfall stays null where it needs one")
            if need_om:
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
                ev, series, sn_info = (None, None, {"result": "not attempted for this tier"})
                if rid in snotel_ids:
                    if stations is None:
                        sn_info = {"result": "station list not cached (offline)"}
                    else:
                        ev, series, sn_info = choose_snotel(
                            resort, settings, stations, sn_cache, start, end, args.offline
                        )
                climate, prov, source_ids = build_resort(
                    resort,
                    settings,
                    start_year,
                    end_year,
                    om_meta,
                    daily,
                    ev,
                    generated_at,
                    snotel_series=series,
                    grid_elevation_m=open_meteo.cached_grid_elevation_m(om_cache, loc),
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
        write_outputs(args.out, outputs, sources, provenance, generated_at, args.validation_report)

    log(f"climate: wrote {len(outputs)} resort file(s) to {args.out / 'climate'}")
    for rid, prov in sorted(provenance.items()):
        sn = prov.get("snotel", {})
        dens = sorted({m["density_source"] for m in outputs[rid]["months"] if m["density_source"]})
        val = prov["validation"]
        snow = prov["snowfall"]
        annual = f"{val['annual_in']:,.0f} in" if val["annual_in"] is not None else "null"
        log(
            f"  ok        {rid}: snowfall {snow['snowfall_source'] or 'null'} {annual} "
            f"(validation {val['status']}); density {'/'.join(dens) or 'none'}; "
            f"snotel {sn.get('station') or sn.get('result', 'none')}"
        )
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
    validation_report: Optional[Path] = None,
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
    if validation_report is not None:
        write_validation_report(Path(validation_report), prov_all, generated_at)


def write_validation_report(path: Path, provenance: dict[str, Any], generated_at: str) -> None:
    """docs/map/climate-validation.md from the merged provenance file."""
    rows = []
    counts: dict[str, int] = {}
    for rid in sorted(provenance):
        p = provenance[rid]
        v = p.get("validation") or {}
        snow = p.get("snowfall") or {}
        status = v.get("status", "no comparison")
        counts[status] = counts.get(status, 0) + 1

        def num(x: Any) -> str:
            return "" if x is None else f"{x:,.0f}"

        diff = "" if v.get("diff_pct") is None else f"{v['diff_pct']:+.0f}%"
        note = snow.get("null_reason") or snow.get("reason") or ""
        rows.append(
            f"| {p.get('name') or rid} | {p.get('tier') or ''} | {snow.get('snowfall_source') or 'null'} "
            f"| {num(v.get('annual_in'))} | {num(v.get('published_in'))} | {v.get('published_source_id') or ''} "
            f"| {diff} | {status} | {note.replace('|', '/')} |"
        )
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    text = f"""# Climate validation

Generated by `python -m pipeline.climate.run` at {generated_at}. Do not edit by hand.

Each row compares the derived mean annual snowfall (sum of the monthly means, October to September)
with the published annual snowfall in `Resort.snow.annual_avg_in`. A difference over the
`climate.validation_flag_pct` limit in config/definitions.yaml is flagged. Over
`climate.validation_suppress_pct` the monthly snowfall, powder days, banner days and storm depth
are published as null with the reason in `snowfall_null_reason`.

Snowfall sources:

* `snotel_swe`: daily SWE gains at the chosen NRCS SNOTEL station divided by new snow density,
  never below the daily depth gain. Values describe the station elevation.
* `era5`: Open Meteo ERA5 daily snowfall, kept only for low relief resorts or where the ERA5 grid
  cell height is close to the resort base.
* `null`: neither source fits; the reason column says why.

Summary: {summary or 'no resorts'}.

| Resort | Tier | Snowfall source | Derived annual (in) | Published annual (in) | Published source | Difference | Status | Note |
|---|---|---|---|---|---|---|---|---|
""" + "\n".join(rows) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
