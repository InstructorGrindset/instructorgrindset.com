"""Resort and its nested sections (ROADMAP Section 6, ARCHITECTURE resorts.json)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .career import Career
from .housing import HousingSummary
from .common import (
    Latitude,
    Longitude,
    Slug,
    Sourced,
    SourcedMonthDay,
    SourcedNumber,
    SourcedString,
    StateCode,
    StrictModel,
    check_source_ref,
)

import datetime as dt

Criterion = Literal[
    "diehard", "luxury", "athlete_development", "heritage", "operator_compensation", "year_round_training", "owner_selection",
]
FacilityType = Literal["ski_area", "indoor", "training_center"]
Tier = Literal["A", "B"]
TierAStatus = Literal["draft", "approved"]
EditorialStatus = Literal["draft", "reviewed"]


class TierACriterion(StrictModel):
    criterion: Criterion
    rationale: str = Field(min_length=1)
    source_id: Slug

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class Approval(StrictModel):
    """Who approved a Tier A record, and when (ROADMAP Section 3 gate)."""

    by: str = Field(min_length=1)
    date: dt.date
    note: Optional[str] = None


class Size(StrictModel):
    acres: Optional[SourcedNumber] = None
    vertical_ft: Optional[SourcedNumber] = None
    lifts: Optional[SourcedNumber] = None
    ski_school_staff_est: Optional[SourcedNumber] = None


class Elevation(StrictModel):
    base_ft: Optional[SourcedNumber] = None
    summit_ft: Optional[SourcedNumber] = None

    @model_validator(mode="after")
    def _order(self) -> "Elevation":
        if self.base_ft is not None and self.summit_ft is not None and self.base_ft.value > self.summit_ft.value:
            raise ValueError("elevation base_ft must not exceed summit_ft")
        return self


class Season(StrictModel):
    open_typical: Optional[SourcedMonthDay] = None
    close_typical: Optional[SourcedMonthDay] = None
    length_days: Optional[SourcedNumber] = None
    training_start: Optional[SourcedMonthDay] = None


class Terrain(StrictModel):
    beginner_pct: Optional[SourcedNumber] = None
    intermediate_pct: Optional[SourcedNumber] = None
    advanced_pct: Optional[SourcedNumber] = None
    expert_pct: Optional[SourcedNumber] = None
    character_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _pct_range(self) -> "Terrain":
        for name in ("beginner_pct", "intermediate_pct", "advanced_pct", "expert_pct"):
            v = getattr(self, name)
            if v is not None and not (0 <= v.value <= 100):
                raise ValueError(f"{name} must be between 0 and 100")
        return self


class Fit(StrictModel):
    enthusiast_types: list[str] = Field(default_factory=list)
    professional_types: list[str] = Field(default_factory=list)


class Clients(StrictModel):
    profile_tags: list[str] = Field(default_factory=list)
    notes_md: Optional[str] = None


class Editorial(StrictModel):
    """Phase 6. Only reviewed editorial reaches the build output."""

    overview_md: Optional[str] = None
    culture_md: Optional[str] = None
    alexandre_take_md: Optional[str] = None
    status: EditorialStatus = "draft"


class Resort(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    tier: Tier
    facility_type: FacilityType = "ski_area"
    tier_a_criteria: list[TierACriterion] = Field(default_factory=list)
    tier_a_status: Optional[TierAStatus] = None
    approval: Optional[Approval] = None
    owner: Optional[SourcedString] = None
    lat: Latitude
    lon: Longitude
    location_source_id: Slug
    cluster_id: Slug
    state: StateCode
    size: Size = Field(default_factory=Size)
    elevation: Elevation = Field(default_factory=Elevation)
    season: Season = Field(default_factory=Season)
    terrain: Terrain = Field(default_factory=Terrain)
    fit: Fit = Field(default_factory=Fit)
    clients: Clients = Field(default_factory=Clients)
    career: Optional[Career] = None
    editorial: Optional[Editorial] = None
    climate_available: bool = False
    detail_available: bool = False
    housing_summary: Optional[HousingSummary] = None
    sources: list[Slug] = Field(default_factory=list)

    @field_validator("location_source_id")
    @classmethod
    def _loc_source(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @field_validator("sources")
    @classmethod
    def _sources_defined(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        return v

    @model_validator(mode="after")
    def _tier_rules(self) -> "Resort":
        if self.tier == "A":
            if not self.tier_a_criteria:
                raise ValueError("a Tier A resort needs at least one sourced tier_a_criteria entry")
            if self.tier_a_status is None:
                raise ValueError("a Tier A resort needs tier_a_status (draft or approved)")
            check_approval(self.tier_a_status, self.approval)
        else:
            if self.tier_a_status is not None:
                raise ValueError("tier_a_status must be null for Tier B")
            if self.approval is not None:
                raise ValueError("approval must be null for Tier B")
            if self.facility_type != "ski_area":
                raise ValueError("Tier B records are ski_area facilities")
        if self.editorial is not None and self.editorial.status != "reviewed":
            raise ValueError("only reviewed editorial may be emitted")
        missing = sorted(referenced_source_ids(self) - set(self.sources))
        if missing:
            raise ValueError(f"sources is missing referenced ids: {missing}")
        return self


def check_approval(status: str, approval: Optional[Approval], label: str = "") -> None:
    prefix = f"{label}: " if label else ""
    if status == "approved" and approval is None:
        raise ValueError(f"{prefix}an approved Tier A record needs an approval block {{by, date, note}}")
    if status != "approved" and approval is not None:
        raise ValueError(f"{prefix}approval is set but tier_a_status is {status}; set tier_a_status: approved or remove approval")


def referenced_source_ids(resort: Resort) -> set[str]:
    """Every source_id referenced anywhere inside ``resort``."""
    found: set[str] = {resort.location_source_id}

    def walk(obj: object) -> None:
        if isinstance(obj, StrictModel):
            sid = getattr(obj, "source_id", None)
            if isinstance(sid, str):
                found.add(sid)
            for name in type(obj).model_fields:
                walk(getattr(obj, name))
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for name in ("tier_a_criteria", "owner", "size", "elevation", "season", "terrain", "career"):
        walk(getattr(resort, name))
    return found
