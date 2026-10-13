"""Load and validate config/definitions.yaml and config/clusters.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import yaml
from pydantic import Field, model_validator

from .schemas.common import Slug, StateCode, StrictModel
from .schemas.geo import Geometry

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- definitions
class SnowDefs(StrictModel):
    powder_day_in: float = Field(gt=0)
    banner_day_in: float = Field(gt=0)
    window_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> "SnowDefs":
        if self.banner_day_in <= self.powder_day_in:
            raise ValueError("banner_day_in must exceed powder_day_in")
        return self


class DensityDefs(StrictModel):
    dry_max_pct: float = Field(gt=0, lt=100)
    moist_min_pct: float = Field(gt=0, lt=100)

    @model_validator(mode="after")
    def _order(self) -> "DensityDefs":
        if self.moist_min_pct <= self.dry_max_pct:
            raise ValueError("moist_min_pct must exceed dry_max_pct")
        return self

    def classify(self, pct: float) -> Literal["dry", "average", "moist"]:
        if pct < self.dry_max_pct:
            return "dry"
        if pct > self.moist_min_pct:
            return "moist"
        return "average"


class SeasonDefs(StrictModel):
    length_rule: Literal["median_open_to_close"]
    lookback_seasons: int = Field(gt=0)


class DisplayDefs(StrictModel):
    priority: list[Literal["published", "reported", "estimated"]]
    size_rank_acres_breaks: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _check(self) -> "DisplayDefs":
        if sorted(set(self.priority)) != sorted(["published", "reported", "estimated"]):
            raise ValueError("priority must list published, reported and estimated once each")
        if self.size_rank_acres_breaks != sorted(self.size_rank_acres_breaks):
            raise ValueError("size_rank_acres_breaks must be ascending")
        return self

    def size_rank(self, acres: Optional[float]) -> int:
        if acres is None:
            return 1
        return 1 + sum(1 for b in self.size_rank_acres_breaks if acres >= b)


class ReportDefs(StrictModel):
    aggregation_min: int = Field(ge=1)


class SubmissionsDefs(StrictModel):
    endpoint: Optional[str] = Field(default=None, pattern=r"^https://")


class OperatorScaleDefs(StrictModel):
    values: dict[int, str]

    @model_validator(mode="after")
    def _check(self) -> "OperatorScaleDefs":
        if sorted(self.values) != [1, 2, 3]:
            raise ValueError("operator_scale.values needs exactly 1, 2 and 3")
        return self


class AccessDefs(StrictModel):
    ratings: list[Literal["convenient", "transit_friendly", "mixed", "car_centric"]]


class StalenessDefs(StrictModel):
    max_age_months: int = Field(gt=0)


class MatchingDefs(StrictModel):
    radius_km: float = Field(gt=0)
    name_similarity_min: float = Field(ge=0, le=1)
    strong_name_similarity_min: float = Field(ge=0, le=1)
    strong_name_radius_km: float = Field(gt=0)


class OpenSkiMapDefs(StrictModel):
    require_mapped_lifts_or_runs: bool
    dedupe_radius_km: Optional[float] = Field(default=None, gt=0)
    dedupe_name_similarity_min: float = Field(ge=0, le=1)

    def filter_kwargs(self) -> dict:
        return {
            "require_infrastructure": self.require_mapped_lifts_or_runs,
            "dedupe_radius_km": self.dedupe_radius_km,
            "dedupe_similarity_min": self.dedupe_name_similarity_min,
        }


class OwnershipDefs(StrictModel):
    missing_tier_a_record: Literal["warn", "fail"] = "warn"


class ProfessionalismWeights(StrictModel):
    cert_pay_differential: Optional[float] = None
    paid_training_hours: Optional[float] = None
    examiner_trainer_presence: Optional[float] = None
    private_lesson_share: Optional[float] = None
    year_round_staff: Optional[float] = None
    clinic_access: Optional[float] = None


class ProfessionalismDefs(StrictModel):
    scale_min: float
    scale_max: float
    weights: ProfessionalismWeights
    min_components: Optional[int] = Field(default=None, ge=1, le=6)

    @model_validator(mode="after")
    def _weights(self) -> "ProfessionalismDefs":
        vals = [v for v in self.weights.model_dump().values() if v is not None]
        if any(v < 0 for v in vals):
            raise ValueError("professionalism weights must not be negative")
        if self.weights_set and sum(vals) <= 0:
            raise ValueError("professionalism weights must not all be zero")
        return self

    @property
    def weights_set(self) -> bool:
        return all(v is not None for v in self.weights.model_dump().values())


class LucrativenessAssumptions(StrictModel):
    """Owner decision 4. Null hours or season months means assumptions_unset."""

    hours_per_week: Optional[float] = Field(default=None, gt=0)
    default_season_months: Optional[float] = Field(default=None, gt=0)
    days_per_month: float = Field(default=30.4, gt=0)
    weeks_per_month: float = Field(default=4.33, gt=0)
    tips_season_usd: Optional[float] = Field(default=None, ge=0)


class LucrativenessDefs(StrictModel):
    assumptions: LucrativenessAssumptions


BaselineKey = Literal["fmr_studio", "fmr_one_br", "fmr_two_br", "acs_median"]


class RentDefs(StrictModel):
    baseline_preference: list[BaselineKey] = Field(min_length=1)
    lucrativeness_rent: list[Literal["employee_housing", "reported_area", "baseline", "fmr_studio", "fmr_one_br",
                                     "fmr_two_br", "acs_median"]] = Field(min_length=1)
    hud_fmr_year: Optional[int] = None
    acs_year: int
    acs_dataset: str = "acs/acs5"


class TransitDefs(StrictModel):
    search_radius_km: float = Field(gt=0)
    walk_speed_kmh: Optional[float] = Field(default=None, gt=0)
    max_walk_km: Optional[float] = Field(default=None, gt=0)


class TrailmapDefs(StrictModel):
    simplify_tolerance_m: float = Field(gt=0, le=50)


class RoutingDefs(StrictModel):
    provider: Literal["valhalla", "osrm", "ors"]
    base_url: str = Field(pattern=r"^https?://")
    api_key_env: Optional[str] = None
    isochrone_minutes: list[int] = Field(default_factory=list)
    timeout_s: float = Field(default=30, gt=0)


class CareerVocab(StrictModel):
    client_profiles: list[str]
    enthusiast_types: list[str] = Field(default_factory=list)
    professional_types: list[str] = Field(default_factory=list)


class CareerDefs(StrictModel):
    vocab: CareerVocab



# ------------------------------------------------------------------- heat maps
HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


def oklab_lightness(hex_color: str) -> float:
    """OKLab L (0 to 1) of an sRGB hex color (Bjorn Ottosson's OKLab)."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_


class HeatRamp(StrictModel):
    name: str = Field(min_length=1)
    colors: list[Annotated[str, Field(pattern=HEX_COLOR)]] = Field(min_length=3)

    @model_validator(mode="after")
    def _ordered(self) -> "HeatRamp":
        ls = [oklab_lightness(c) for c in self.colors]
        if any(b <= a for a, b in zip(ls, ls[1:])):
            raise ValueError("heat.ramp.colors must rise in OKLab lightness at every stop (perceptual order)")
        return self


class HeatBox(StrictModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)

    @model_validator(mode="after")
    def _order(self) -> "HeatBox":
        if self.east <= self.west or self.north <= self.south:
            raise ValueError("a heat region needs west < east and south < north")
        return self


class HeatRegions(StrictModel):
    lower48: HeatBox
    alaska: HeatBox


class HeatField(StrictModel):
    neighbors: int = Field(ge=1, le=64)
    min_distance_km: float = Field(gt=0)
    decluster_km: float = Field(ge=0)
    full_opacity_km: float = Field(gt=0)
    fade_end_km: float = Field(gt=0)
    max_alpha: float = Field(gt=0, le=1)
    floor_alpha: float = Field(gt=0, le=1)
    px_per_degree: float = Field(gt=0, le=60)
    grid_step_px: int = Field(ge=1, le=16)
    max_pixels: int = Field(ge=10000)

    @model_validator(mode="after")
    def _check(self) -> "HeatField":
        if self.fade_end_km <= self.full_opacity_km:
            raise ValueError("heat.field.fade_end_km must exceed full_opacity_km")
        if self.floor_alpha > self.max_alpha:
            raise ValueError("heat.field.floor_alpha must not exceed max_alpha")
        return self


class HeatMode(StrictModel):
    scale: Literal["linear", "sqrt"] = "linear"
    domain: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    domain_min: Optional[float] = None
    ticks: Optional[list[float]] = None

    @model_validator(mode="after")
    def _check(self) -> "HeatMode":
        if self.domain and self.domain[1] <= self.domain[0]:
            raise ValueError("heat mode domain must be [min, max] with min < max")
        if self.ticks and self.ticks != sorted(self.ticks):
            raise ValueError("heat mode ticks must be ascending")
        if self.domain and self.ticks and not all(self.domain[0] <= t <= self.domain[1] for t in self.ticks):
            raise ValueError("heat mode ticks must sit inside the domain")
        return self


class HeatModes(StrictModel):
    lucrativeness: HeatMode
    professionalism: HeatMode
    season: HeatMode
    snowfall: HeatMode


class HeatSnowfall(StrictModel):
    accept_completeness: list[Literal["S", "R", "P", "E"]] = Field(min_length=1)
    accept_measurement_flags: list[Literal["", "M", "V", "W", "X", "Y", "Z"]] = Field(min_length=1)
    neighbors: int = Field(ge=1, le=64)
    min_distance_km: float = Field(gt=0)
    pocket_radius_km: float = Field(ge=5, le=50)
    px_per_degree: float = Field(gt=0, le=60)
    grid_step_px: int = Field(ge=1, le=16)


class HeatDefs(StrictModel):
    ramp: HeatRamp
    regions: HeatRegions
    field: HeatField
    modes: HeatModes
    snowfall: HeatSnowfall


class Definitions(StrictModel):
    version: int
    snow: SnowDefs
    density: DensityDefs
    season: SeasonDefs
    display: DisplayDefs
    reports: ReportDefs
    staleness: StalenessDefs
    operator_scale: OperatorScaleDefs
    access: AccessDefs
    submissions: SubmissionsDefs = Field(default_factory=SubmissionsDefs)
    matching: MatchingDefs
    openskimap: OpenSkiMapDefs
    ownership: OwnershipDefs = Field(default_factory=OwnershipDefs)
    professionalism: ProfessionalismDefs
    lucrativeness: LucrativenessDefs
    career: CareerDefs
    rent: RentDefs
    transit: TransitDefs
    routing: RoutingDefs
    trailmap: TrailmapDefs
    heat: HeatDefs
    # Phase 3 climate ETL block; validated by pipeline/climate/definitions.py.
    climate: Optional[dict[str, Any]] = None


# ------------------------------------------------------------------- clusters
class MembershipRule(StrictModel):
    tier_b_radius_km: float = Field(gt=0)
    require_state_match: bool = True
    fallback_id_prefix: Slug = "other"
    fallback_name_prefix: str = "Other"


class PolygonRule(StrictModel):
    buffer_km: float = Field(gt=0)
    arc_segments: int = Field(ge=8)
    coordinate_decimals: int = Field(ge=3, le=7)


class ClusterDef(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    states: list[StateCode] = Field(min_length=1)
    polygon: Optional[Geometry] = None
    sources: list[Slug] = Field(default_factory=list)


class ClustersConfig(StrictModel):
    version: int
    membership: MembershipRule
    polygon: PolygonRule
    clusters: list[ClusterDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "ClustersConfig":
        ids = [c.id for c in self.clusters]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate cluster ids: {dupes}")
        prefix = self.membership.fallback_id_prefix + "-"
        clash = [i for i in ids if i.startswith(prefix)]
        if clash:
            raise ValueError(f"cluster ids may not use the fallback prefix '{prefix}': {clash}")
        return self

    def by_id(self) -> dict[str, ClusterDef]:
        return {c.id: c for c in self.clusters}


class Config(StrictModel):
    definitions: Definitions
    clusters: ClustersConfig


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def load_definitions(path: Path) -> Definitions:
    return Definitions.model_validate(_load_yaml(path))


def load_clusters(path: Path) -> ClustersConfig:
    return ClustersConfig.model_validate(_load_yaml(path))


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = config_dir or REPO_ROOT / "config"
    return Config(
        definitions=load_definitions(config_dir / "definitions.yaml"),
        clusters=load_clusters(config_dir / "clusters.yaml"),
    )
