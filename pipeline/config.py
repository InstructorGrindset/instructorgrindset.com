"""Load and validate config/definitions.yaml and config/clusters.yaml."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import yaml
from pydantic import Field, field_validator, model_validator

from .schemas.common import Slug, StateCode, StrictModel
from .color import delta_e, oklab
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
    # One build cycle in days: a Vail Resorts or Alterra ownership source accessed longer ago fails the build.
    max_source_age_days: int = Field(default=400, ge=1)


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


class LucrativenessDefs(StrictModel):
    assumptions: LucrativenessAssumptions


BaselineKey = Literal["fmr_studio", "fmr_one_br", "fmr_two_br", "acs_median"]


class MedianRentDefs(StrictModel):
    """Median rent across sources (pipeline/rent_median.py)."""

    # Unit sizes in order of preference: the first with any current figure is used (all, then one bedroom).
    units: list[Literal["all", "studio", "1br", "2br", "3br"]] = Field(default_factory=lambda: ["all", "1br"], min_length=1)
    # Only the best geography level with a usable figure is used; levels are never mixed.
    geography_order: list[Literal["zip", "city", "county", "metro"]] = Field(
        default_factory=lambda: ["zip", "city", "county", "metro"], min_length=1)
    # Figures whose period ended longer ago than this are ignored.
    max_age_months: int = Field(default=18, ge=1)
    # The publisher is the registered domain of the source URL; this maps it to the name shown.
    publishers: dict[str, str] = Field(default_factory=dict)
    # Domains used only when no other publisher has a figure (any unit) for the same town at the same level.
    last_resort_publishers: list[str] = Field(default_factory=list)


class ZoriDefs(StrictModel):
    """Zillow Observed Rent Index (pipeline/rent_zillow.py)."""

    match_levels: list[Literal["zip", "county", "metro"]] = Field(
        default_factory=lambda: ["zip", "county", "metro"], min_length=1)
    zip_radius_km: float = Field(default=10, gt=0, le=50)
    # latest: the latest month with a value; trailing_mean: the mean of the trailing_months months to then.
    value: Literal["latest", "trailing_mean"] = "latest"
    trailing_months: int = Field(default=12, ge=1, le=36)


RentStep = Literal["median_rent", "zillow_zori", "employee_housing", "reported_area", "baseline", "fmr_studio",
                   "fmr_one_br", "fmr_two_br", "acs_median"]


class RentDefs(StrictModel):
    baseline_preference: list[BaselineKey] = Field(min_length=1)
    lucrativeness_rent: list[RentStep] = Field(min_length=1)
    median: MedianRentDefs = Field(default_factory=MedianRentDefs)
    zori: ZoriDefs = Field(default_factory=ZoriDefs)
    hud_fmr_year: Optional[int] = None
    acs_year: int
    acs_dataset: str = "acs/acs5"


class DateWindow(StrictModel):
    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _order(self) -> "DateWindow":
        if self.end < self.start:
            raise ValueError("end is before start")
        return self


class MonthDayWindow(StrictModel):
    """A yearly window of "MM-DD" days; an end before the start runs into the next year (Dec 1 to Mar 31)."""
    start: str = Field(pattern=r"^\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{2}-\d{2}$")

    @model_validator(mode="after")
    def _real_days(self) -> "MonthDayWindow":
        for v in (self.start, self.end):
            dt.date(2024, int(v[:2]), int(v[3:]))   # a leap year, so 02-29 is allowed
        return self


class TransitDefs(StrictModel):
    search_radius_km: float = Field(gt=0)
    simplify_tolerance_m: Optional[float] = Field(default=None, gt=0, le=50)
    target_season: Optional[DateWindow] = None   # a feed overlapping this window is current
    max_validity_years: float = Field(default=3, gt=0)
    winter_window: MonthDayWindow = Field(default_factory=lambda: MonthDayWindow(start="12-01", end="03-31"))
    winter_min_overlap_days: int = Field(default=30, ge=1)
    walk_speed_kmh: Optional[float] = Field(default=None, gt=0)
    max_walk_km: Optional[float] = Field(default=None, gt=0)


class TrailmapDefs(StrictModel):
    simplify_tolerance_m: float = Field(gt=0, le=50)
    # Operator image CDNs an official trail map file (image_url) may load from, besides the resort's own site.
    official_image_domains: list[str] = Field(default_factory=list)


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
HexColor = Annotated[str, Field(pattern=HEX_COLOR)]
# Neighbouring classes of a classed palette must differ by at least this much (OKLab x 100, normal vision).
CLASS_MIN_DELTA_E = 6.0


def oklab_lightness(hex_color: str) -> float:
    """OKLab L (0 to 1) of an sRGB hex color (Bjorn Ottosson's OKLab)."""
    return oklab(hex_color)[0]


class HeatRamp(StrictModel):
    name: str = Field(min_length=1)
    colors: list[HexColor] = Field(min_length=3)

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
    fade_to: Literal["neutral", "alpha"] = "neutral"
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
    scale: Literal["linear", "sqrt", "classed"] = "linear"
    domain: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    domain_min: Optional[float] = None
    ticks: Optional[list[float]] = None
    breaks: Optional[list[float]] = None
    colors: Optional[list[HexColor]] = None
    legend_compress_below: Optional[float] = None
    legend_ticks: Optional[list[float]] = None

    @model_validator(mode="after")
    def _check(self) -> "HeatMode":
        if self.domain and self.domain[1] <= self.domain[0]:
            raise ValueError("heat mode domain must be [min, max] with min < max")
        if self.ticks and self.ticks != sorted(self.ticks):
            raise ValueError("heat mode ticks must be ascending")
        if self.domain and self.ticks and not all(self.domain[0] <= t <= self.domain[1] for t in self.ticks):
            raise ValueError("heat mode ticks must sit inside the domain")
        if self.scale == "classed":
            # The lightness rule of `ramp` does not apply: a classed palette may change lightness in both
            # directions (snowfall alternates it on purpose) as long as neighbours stay distinct.
            if not self.breaks or not self.colors:
                raise ValueError("a classed heat mode needs breaks and colors")
            if self.breaks != sorted(set(self.breaks)):
                raise ValueError("heat mode breaks must be strictly ascending")
            if len(self.colors) != len(self.breaks) + 1:
                raise ValueError("a classed heat mode needs one more color than breaks")
            weak = [(a, b) for a, b in zip(self.colors, self.colors[1:]) if delta_e(a, b) < CLASS_MIN_DELTA_E]
            if weak:
                raise ValueError(f"neighbouring heat classes too alike (OKLab below {CLASS_MIN_DELTA_E}): {weak}")
            if self.legend_ticks and self.legend_ticks != sorted(self.legend_ticks):
                raise ValueError("heat mode legend_ticks must be ascending")
        elif self.breaks or self.colors:
            raise ValueError("breaks and colors belong to scale: classed")
        return self


class HeatModes(StrictModel):
    lucrativeness: HeatMode
    season: HeatMode
    snowfall: HeatMode


class HeatIsolines(StrictModel):
    start: float = Field(gt=0)
    end: float = Field(gt=0)
    step: float = Field(gt=0)
    major_step: float = Field(gt=0)
    simplify_px: float = Field(ge=0, le=5)
    supersample: int = Field(default=1, ge=1, le=8)
    min_length_px: float = Field(default=0, ge=0)
    label_min_px: float = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "HeatIsolines":
        if self.end < self.start:
            raise ValueError("heat.snowfall.isolines end must not be below start")
        if abs(self.major_step / self.step - round(self.major_step / self.step)) > 1e-9:
            raise ValueError("heat.snowfall.isolines major_step must be a multiple of step")
        return self

    def levels(self) -> list[float]:
        n = int(round((self.end - self.start) / self.step))
        return [self.start + i * self.step for i in range(n + 1)]


class HeatSnowfallSnotel(StrictModel):
    """NRCS SNOTEL station snowfall (pipeline/climate/snotel_stations.py): the water years and the completeness
    rule of the committed extract."""
    first_water_year: int = Field(ge=1980, le=2100)
    last_water_year: int = Field(ge=1980, le=2100)
    min_complete_years: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def _check(self) -> "HeatSnowfallSnotel":
        if self.last_water_year < self.first_water_year:
            raise ValueError("heat.snowfall.snotel.last_water_year must not be before first_water_year")
        if self.min_complete_years > self.last_water_year - self.first_water_year + 1:
            raise ValueError("heat.snowfall.snotel.min_complete_years exceeds the water years in the window")
        return self


class HeatSnowfallElevation(StrictModel):
    """Elevation aware snowfall field (pipeline/heat.py ``gids_node``): a local snowfall gradient with
    elevation from each node's neighbours, applied at every pixel's terrain height (pipeline/terrain.py)."""
    log_offset_in: float = Field(gt=0, le=100)
    max_gradient_per_km: float = Field(ge=0, le=5)
    spread_prior_m: float = Field(gt=0, le=2000)
    max_extrapolation_m: float = Field(ge=0, le=1000)
    terrain_smooth_cells: int = Field(default=0, ge=0, le=8)
    resort_points: bool = False
    pocket_terrain: bool = True


class HeatSnowfall(StrictModel):
    accept_completeness: list[Literal["S", "R", "P", "E"]] = Field(min_length=1)
    accept_measurement_flags: list[Literal["", "M", "V", "W", "X", "Y", "Z"]] = Field(min_length=1)
    snotel: Optional[HeatSnowfallSnotel] = None
    elevation: Optional[HeatSnowfallElevation] = None
    neighbors: int = Field(ge=1, le=64)
    min_distance_km: float = Field(gt=0)
    pocket_radius_km: float = Field(ge=5, le=50)
    px_per_degree: float = Field(gt=0, le=60)
    grid_step_px: int = Field(ge=1, le=16)
    full_opacity_km: float = Field(gt=0)
    fade_end_km: float = Field(gt=0)
    floor_alpha: float = Field(gt=0, le=1)
    fade_to: Literal["neutral", "alpha"] = "alpha"
    isolines: HeatIsolines


class HeatSeason(StrictModel):
    """Season length rasters: resolution and fade (the interpolation itself is heat.field)."""
    px_per_degree: float = Field(gt=0, le=60)
    grid_step_px: int = Field(ge=1, le=16)
    full_opacity_km: float = Field(gt=0)
    fade_end_km: float = Field(gt=0)
    floor_alpha: float = Field(ge=0, le=1)
    fade_to: Literal["neutral", "alpha"] = "neutral"
    # In states with no resort data the colors end this far from the nearest resort with data.
    no_data_reach_km: float = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "HeatSeason":
        if self.fade_end_km <= self.full_opacity_km:
            raise ValueError("heat.season.fade_end_km must exceed full_opacity_km")
        return self


class HeatDefs(StrictModel):
    ramp: HeatRamp
    neutral: HexColor
    regions: HeatRegions
    field: HeatField
    modes: HeatModes
    snowfall: HeatSnowfall
    season: HeatSeason


# ---------------------------------------------------------------- webcams and jobs (links, live postings)
class WebcamDefs(StrictModel):
    max_named_cams: int = Field(default=4, ge=0, le=12)


DOMAIN_RE = r"^[a-z0-9-]+(\.[a-z0-9-]+)+$"


class JobsFeedDefs(StrictModel):
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    require_keywords: dict[Slug, list[str]] = Field(default_factory=dict)
    role_keywords: dict[Literal["management", "coaching", "ski_school"], list[str]] = Field(default_factory=dict)
    location_aliases: dict[Slug, list[str]] = Field(default_factory=dict)
    max_postings_per_board: int = Field(default=150, ge=1, le=1000)
    max_age_days: int = Field(default=14, ge=1)
    refresh_days: int = Field(default=7, ge=1)
    timeout_s: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def _words(self) -> "JobsFeedDefs":
        words = self.keywords + self.exclude_keywords + [w for ws in self.role_keywords.values() for w in ws]
        words += [w for ws in self.location_aliases.values() for w in ws]
        words += [w for ws in self.require_keywords.values() for w in ws]
        bad = [w for w in words if not w.strip()]
        if bad:
            raise ValueError("jobs.feed keywords and aliases must not be empty")
        return self


class HiringWindowDefs(StrictModel):
    """How pipeline/hiring.py reads a hiring window's text into months (the "Hiring now" filter)."""

    max_months: int = Field(default=9, ge=1, le=12)


class JobsDefs(StrictModel):
    ats_domains: dict[str, list[Annotated[str, Field(pattern=DOMAIN_RE)]]] = Field(default_factory=dict)
    blocked_domains: list[Annotated[str, Field(pattern=DOMAIN_RE)]] = Field(default_factory=list)
    feed: JobsFeedDefs = Field(default_factory=JobsFeedDefs)
    hiring_windows: HiringWindowDefs = Field(default_factory=HiringWindowDefs)

    @model_validator(mode="after")
    def _vendors(self) -> "JobsDefs":
        from .schemas.jobs import ATS_VENDORS
        unknown = sorted(set(self.ats_domains) - set(ATS_VENDORS))
        if unknown:
            raise ValueError(f"jobs.ats_domains has unknown vendors: {unknown}")
        return self

    def domains(self, vendor: str) -> set[str]:
        return set(self.ats_domains.get(vendor) or [])


class PassesDefs(StrictModel):
    """Multi resort pass membership (data/raw/passes, ROADMAP item 12)."""

    # Only memberships of this season reach data/build, plus (when fallback_previous_season) the previous season's
    # record for a resort and pass that has none for this season, marked season_fallback.
    season: str = "2026/27"
    fallback_previous_season: bool = True
    # A membership must cite a page on its pass company's own site: registered domains per pass id.
    domains: dict[Literal["epic", "ikon", "mountain_collective", "indy"], list[Annotated[str, Field(pattern=DOMAIN_RE)]]] = \
        Field(default_factory=dict)
    # Reference sites a membership may cite in addition (for example a Wikipedia list), marked estimated.
    supplemental_domains: list[Annotated[str, Field(pattern=DOMAIN_RE)]] = Field(default_factory=list)

    @field_validator("season")
    @classmethod
    def _season(cls, v: str) -> str:
        from .schemas.passes import check_season
        return check_season(v)

    def domain_sets(self) -> dict[str, set[str]]:
        return {k: set(v) for k, v in self.domains.items()}


class HousingSearchDefs(StrictModel):
    """Housing search links (data/raw/housing_search, ROADMAP item 8c)."""

    # A resort shows at most this many housing search tools (resort scoped first, then its cluster's).
    max_tools: int = Field(default=8, ge=1, le=20)
    # A Furnished Finder link must sit on one of these registered domains.
    furnished_finder_domains: list[Annotated[str, Field(pattern=DOMAIN_RE)]] = Field(
        default_factory=lambda: ["furnishedfinder.com"])


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
    webcams: WebcamDefs = Field(default_factory=WebcamDefs)
    jobs: JobsDefs = Field(default_factory=JobsDefs)
    passes: PassesDefs = Field(default_factory=PassesDefs)
    housing_search: HousingSearchDefs = Field(default_factory=HousingSearchDefs)
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
