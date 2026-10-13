"""Load climate thresholds and ETL assumptions from config/definitions.yaml.

Nothing here has a numeric default. Every threshold comes from
``config/definitions.yaml`` (CLAUDE.md: all thresholds, weights and
assumptions live there). When a value is missing the loader raises
:class:`DefinitionsError` with the YAML block that needs to be added.

Tests pass a definitions dict straight to :func:`climate_settings`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

DEFAULT_DEFINITIONS_PATH = Path("config/definitions.yaml")

# Accepted locations for the four Section 6 thresholds. The first path that
# exists wins. A value may be a bare number or a mapping with a "value" key.
POWDER_PATHS = (
    "snow.powder_day_in",
    "climate.powder_day_in",
    "thresholds.powder_day_in",
    "powder_day_in",
    "powder_day.min_new_snow_in",
    "powder_day.threshold_in",
    "powder_day.inches",
    "snow.powder_day.min_new_snow_in",
    "snow.powder_day.threshold_in",
)
BANNER_PATHS = tuple(p.replace("powder", "banner") for p in POWDER_PATHS)
DRY_BELOW_PATHS = (
    "density_classes_pct.dry_below",
    "density_classes.dry_below_pct",
    "density_classes.dry_max_pct",
    "density.dry_below_pct",
    "density.dry_max_pct",
    "snow.density_classes_pct.dry_below",
    "snow.density_classes.dry_below_pct",
    "snow.density.dry_below_pct",
    "climate.density_classes_pct.dry_below",
)
MOIST_ABOVE_PATHS = (
    "density_classes_pct.moist_above",
    "density_classes.moist_above_pct",
    "density_classes.moist_min_pct",
    "density.moist_above_pct",
    "density.moist_min_pct",
    "snow.density_classes_pct.moist_above",
    "snow.density_classes.moist_above_pct",
    "snow.density.moist_above_pct",
    "climate.density_classes_pct.moist_above",
)

# Keys required under the top level ``climate:`` block.
CLIMATE_KEYS = (
    "period_seasons",
    "era5_lag_days",
    "storm_min_daily_in",
    "min_month_coverage",
    "min_storms_per_month",
    "density_estimate_method",
    "era5_snow_elevation_band_ft",
    "low_relief_max_vertical_ft",
    "low_relief_max_base_ft",
    "validation_flag_pct",
    "validation_suppress_pct",
)
SNOTEL_KEYS = (
    "radius_km",
    "elevation_band_below_ft",
    "elevation_band_above_ft",
    "max_candidates",
    "storm_min_daily_depth_gain_in",
    "min_storm_depth_gain_in",
    "density_min_pct",
    "density_max_pct",
    "swe_noise_tolerance_in",
    "max_daily_depth_gain_in",
    "min_valid_storms",
    "min_record_seasons",
    "swe_min_daily_gain_in",
    "swe_max_daily_gain_in",
    "max_daily_new_snow_in",
    "rain_snow_threshold_c",
    "lapse_rate_c_per_km",
)

SUGGESTED_YAML = """\
# Section 6 thresholds (any of the accepted key paths works; this is one)
snow:
  powder_day_in: 6        # 6 in or more new snow in 24 hours
  banner_day_in: 12       # 12 in or more new snow in 24 hours
density_classes_pct:
  dry_below: 7            # dry under 7 percent water content
  moist_above: 12         # moist over 12 percent; 7 to 12 is average

# Phase 3 climate ETL assumptions
climate:
  period_seasons: 25            # climatology length in full ski seasons
  era5_lag_days: 7              # wait this long after Sep 30 before a season counts as complete
  storm_min_daily_in: 1.0       # a storm is consecutive days with at least this much new snow
  min_month_coverage: 0.9       # share of days with data before a season month counts
  min_storms_per_month: 5       # storms needed before a month gets a density value
  density_estimate_method: anderson_1976   # or hedstrom_pomeroy_1998
  # ERA5 snowfall is kept only where no usable SNOTEL station exists and either the
  # ERA5 grid cell height is within this band of the resort base, or the resort is low relief.
  era5_snow_elevation_band_ft: 1000
  low_relief_max_vertical_ft: 1500   # low relief: vertical under this ...
  low_relief_max_base_ft: 4000       # ... and base under this
  validation_flag_pct: 35       # flag annual totals this far from the published annual snowfall
  validation_suppress_pct: 50   # null monthly snowfall when the annual total is this far off
  snotel:
    radius_km: 25               # search radius around the resort base
    elevation_band_below_ft: 1500   # station may sit this far below the resort elevation
    elevation_band_above_ft: 3500   # station may sit this far above the resort elevation
    max_candidates: 3           # stations tried, nearest first
    storm_min_daily_depth_gain_in: 1   # daily depth gain that marks a storm day
    min_storm_depth_gain_in: 3  # storms with less total depth gain are dropped
    density_min_pct: 2          # QC lower bound for storm density
    density_max_pct: 40         # QC upper bound for storm density
    swe_noise_tolerance_in: 0.1 # a daily SWE drop larger than this inside a storm is sensor noise
    max_daily_depth_gain_in: 40 # a daily depth jump above this is a sensor spike
    min_valid_storms: 20        # station is usable with at least this many QC passed storms
    min_record_seasons: 10      # station needs this many seasons of daily SWE in the period
    swe_min_daily_gain_in: 0.1  # smallest daily SWE gain counted as snowfall (WTEQ precision is 0.1 in)
    swe_max_daily_gain_in: 6.0  # a daily SWE gain above this is a sensor spike
    max_daily_new_snow_in: 60   # cap on derived daily new snow
    rain_snow_threshold_c: 1.0  # SWE gains without a depth gain count as snow at or below this air temperature
    lapse_rate_c_per_km: 6.5    # standard atmosphere lapse rate, ERA5 temperature to station elevation
"""


class DefinitionsError(RuntimeError):
    """config/definitions.yaml is missing or lacks a climate value."""


@dataclass(frozen=True)
class SnotelSettings:
    radius_km: float
    elevation_band_below_ft: float
    elevation_band_above_ft: float
    max_candidates: int
    storm_min_daily_depth_gain_in: float
    min_storm_depth_gain_in: float
    density_min_pct: float
    density_max_pct: float
    swe_noise_tolerance_in: float
    max_daily_depth_gain_in: float
    min_valid_storms: int
    min_record_seasons: int
    swe_min_daily_gain_in: float
    swe_max_daily_gain_in: float
    max_daily_new_snow_in: float
    rain_snow_threshold_c: float
    lapse_rate_c_per_km: float


@dataclass(frozen=True)
class ClimateSettings:
    powder_day_in: float
    banner_day_in: float
    dry_below_pct: float
    moist_above_pct: float
    period_seasons: int
    era5_lag_days: int
    storm_min_daily_in: float
    min_month_coverage: float
    min_storms_per_month: int
    density_estimate_method: str
    era5_snow_elevation_band_ft: float
    low_relief_max_vertical_ft: float
    low_relief_max_base_ft: float
    validation_flag_pct: float
    validation_suppress_pct: float
    snotel: SnotelSettings


def _lookup(defs: Mapping[str, Any], path: str) -> Any:
    node: Any = defs
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    if isinstance(node, Mapping) and "value" in node:
        node = node["value"]
    return node


def _first_number(defs: Mapping[str, Any], paths: tuple[str, ...], label: str) -> float:
    for path in paths:
        value = _lookup(defs, path)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DefinitionsError(f"{label} at '{path}' must be a number, got {value!r}")
            return float(value)
    raise DefinitionsError(
        f"config/definitions.yaml has no {label}. Looked for: {', '.join(paths)}.\n"
        f"Suggested block:\n{SUGGESTED_YAML}"
    )


def _require(block: Any, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(block, Mapping):
        raise DefinitionsError(
            f"config/definitions.yaml has no '{where}' block.\nSuggested block:\n{SUGGESTED_YAML}"
        )
    missing = [k for k in keys if block.get(k) is None]
    if missing:
        raise DefinitionsError(
            f"config/definitions.yaml '{where}' is missing: {', '.join(missing)}.\n"
            f"Suggested block:\n{SUGGESTED_YAML}"
        )
    out = {}
    for k in keys:
        v = block[k]
        if isinstance(v, Mapping) and "value" in v:
            v = v["value"]
        out[k] = v
    return out


def climate_settings(defs: Mapping[str, Any]) -> ClimateSettings:
    """Build :class:`ClimateSettings` from a parsed definitions mapping."""
    if not isinstance(defs, Mapping):
        raise DefinitionsError("definitions must be a mapping")
    powder = _first_number(defs, POWDER_PATHS, "powder day threshold (in)")
    banner = _first_number(defs, BANNER_PATHS, "banner day threshold (in)")
    dry = _first_number(defs, DRY_BELOW_PATHS, "dry density cutoff (pct)")
    moist = _first_number(defs, MOIST_ABOVE_PATHS, "moist density cutoff (pct)")
    if banner < powder:
        raise DefinitionsError("banner day threshold must be at least the powder day threshold")
    if moist < dry:
        raise DefinitionsError("moist density cutoff must be at least the dry cutoff")

    climate = _require(defs.get("climate"), CLIMATE_KEYS, "climate")
    snotel = _require((defs.get("climate") or {}).get("snotel"), SNOTEL_KEYS, "climate.snotel")
    method = str(climate["density_estimate_method"])
    from pipeline.climate.density_estimate import METHODS

    if method not in METHODS:
        raise DefinitionsError(
            f"climate.density_estimate_method must be one of {sorted(METHODS)}, got {method!r}"
        )
    return ClimateSettings(
        powder_day_in=powder,
        banner_day_in=banner,
        dry_below_pct=dry,
        moist_above_pct=moist,
        period_seasons=int(climate["period_seasons"]),
        era5_lag_days=int(climate["era5_lag_days"]),
        storm_min_daily_in=float(climate["storm_min_daily_in"]),
        min_month_coverage=float(climate["min_month_coverage"]),
        min_storms_per_month=int(climate["min_storms_per_month"]),
        density_estimate_method=method,
        era5_snow_elevation_band_ft=float(climate["era5_snow_elevation_band_ft"]),
        low_relief_max_vertical_ft=float(climate["low_relief_max_vertical_ft"]),
        low_relief_max_base_ft=float(climate["low_relief_max_base_ft"]),
        validation_flag_pct=float(climate["validation_flag_pct"]),
        validation_suppress_pct=float(climate["validation_suppress_pct"]),
        snotel=SnotelSettings(
            radius_km=float(snotel["radius_km"]),
            elevation_band_below_ft=float(snotel["elevation_band_below_ft"]),
            elevation_band_above_ft=float(snotel["elevation_band_above_ft"]),
            max_candidates=int(snotel["max_candidates"]),
            storm_min_daily_depth_gain_in=float(snotel["storm_min_daily_depth_gain_in"]),
            min_storm_depth_gain_in=float(snotel["min_storm_depth_gain_in"]),
            density_min_pct=float(snotel["density_min_pct"]),
            density_max_pct=float(snotel["density_max_pct"]),
            swe_noise_tolerance_in=float(snotel["swe_noise_tolerance_in"]),
            max_daily_depth_gain_in=float(snotel["max_daily_depth_gain_in"]),
            min_valid_storms=int(snotel["min_valid_storms"]),
            min_record_seasons=int(snotel["min_record_seasons"]),
            swe_min_daily_gain_in=float(snotel["swe_min_daily_gain_in"]),
            swe_max_daily_gain_in=float(snotel["swe_max_daily_gain_in"]),
            max_daily_new_snow_in=float(snotel["max_daily_new_snow_in"]),
            rain_snow_threshold_c=float(snotel["rain_snow_threshold_c"]),
            lapse_rate_c_per_km=float(snotel["lapse_rate_c_per_km"]),
        ),
    )


def load_definitions(path: Optional[Path] = None) -> ClimateSettings:
    """Read config/definitions.yaml and return climate settings."""
    import yaml

    path = Path(path or DEFAULT_DEFINITIONS_PATH)
    if not path.exists():
        raise DefinitionsError(
            f"{path} does not exist yet. The climate ETL reads every threshold from it.\n"
            f"Suggested block:\n{SUGGESTED_YAML}"
        )
    with path.open(encoding="utf-8") as fh:
        defs = yaml.safe_load(fh) or {}
    return climate_settings(defs)
