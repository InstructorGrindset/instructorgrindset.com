"""Load and validate config/definitions.yaml and config/clusters.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import Field, model_validator

from .schemas.common import Slug, StateCode, StrictModel
from .schemas.geo import Geometry

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- definitions
class SnowDefs(StrictModel):
    powder_day_min_in: float = Field(gt=0)
    banner_day_min_in: float = Field(gt=0)
    window_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> "SnowDefs":
        if self.banner_day_min_in <= self.powder_day_min_in:
            raise ValueError("banner_day_min_in must exceed powder_day_min_in")
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


class StalenessDefs(StrictModel):
    max_age_months: int = Field(gt=0)


class MatchingDefs(StrictModel):
    radius_km: float = Field(gt=0)
    name_similarity_min: float = Field(ge=0, le=1)
    strong_name_similarity_min: float = Field(ge=0, le=1)
    strong_name_radius_km: float = Field(gt=0)


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

    @property
    def weights_set(self) -> bool:
        return all(v is not None for v in self.weights.model_dump().values())


class LucrativenessAssumptions(StrictModel):
    cert_level: Optional[str] = None
    hours_per_week: Optional[float] = None
    season_months: Optional[float] = None
    housing_choice: Optional[str] = None
    food_monthly_usd: Optional[float] = None
    transport_monthly_usd: Optional[float] = None
    pass_offset_usd: Optional[float] = None
    gear_offset_usd: Optional[float] = None


class RangeSpread(StrictModel):
    low: Optional[float] = None
    high: Optional[float] = None


class LucrativenessDefs(StrictModel):
    assumptions: LucrativenessAssumptions
    range_spread: RangeSpread


class Definitions(StrictModel):
    version: int
    snow: SnowDefs
    density: DensityDefs
    season: SeasonDefs
    display: DisplayDefs
    reports: ReportDefs
    staleness: StalenessDefs
    matching: MatchingDefs
    professionalism: ProfessionalismDefs
    lucrativeness: LucrativenessDefs


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
