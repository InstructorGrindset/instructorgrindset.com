"""Raw research inputs: data/raw/tier_a/*.yaml and data/raw/ownership/*.yaml.

The format is documented in data/raw/tier_a/README.md.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Latitude, Longitude, Slug, Source, SourcedString, StateCode, StrictModel, check_source_ref
from .resort import (
    Access, Approval, Clients, Elevation, FacilityType, Fit, Season, Size, Snow, SourcedOperatorScale, Terrain,
    TierACriterion, TierAStatus,
    check_approval,
)


class TierARecord(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    facility_type: FacilityType = "ski_area"
    # Overrides the batch status for this record. Approved records need `approval`.
    tier_a_status: Optional[TierAStatus] = None
    approval: Optional[Approval] = None
    state: StateCode
    owner: Optional[SourcedString] = None
    lat: Latitude
    lon: Longitude
    location_source_id: Slug
    cluster_hint: Slug
    tier_a_criteria: list[TierACriterion] = Field(min_length=1)
    size: Size = Field(default_factory=Size)
    elevation: Elevation = Field(default_factory=Elevation)
    snow: Optional[Snow] = None
    operator_scale: Optional[SourcedOperatorScale] = None   # 1 family, 2 independent business, 3 large corporate
    access: Optional[Access] = None                         # convenient | transit_friendly | mixed | car_centric
    season: Season = Field(default_factory=Season)
    terrain: Terrain = Field(default_factory=Terrain)
    fit: Fit = Field(default_factory=Fit)
    clients: Clients = Field(default_factory=Clients)
    # OpenSkiMap area names this record stands for when it covers several mapped areas
    # (Palisades Tahoe: Olympic Valley and Alpine Meadows). Empty: the single best match.
    openskimap_names: list[str] = Field(default_factory=list)

    @field_validator("location_source_id")
    @classmethod
    def _loc_source(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class TierABatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    status: Literal["draft", "approved"] = "draft"
    sources: list[Source] = Field(default_factory=list)
    resorts: list[TierARecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "TierABatch":
        _require_unique([s.id for s in self.sources], "source id")
        _require_unique([r.id for r in self.resorts], "resort id")
        problems = []
        for r in self.resorts:
            try:
                check_approval(self.status_of(r), r.approval, f"resort '{r.id}'")
            except ValueError as e:
                problems.append(str(e))
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def status_of(self, record: TierARecord) -> str:
        """Effective status: the record's own tier_a_status, else the batch status."""
        return record.tier_a_status or self.status


class OwnershipEntry(StrictModel):
    name: str = Field(min_length=1)
    state: StateCode
    source_id: Slug
    # Optional exact link to the Tier A record; otherwise matched by state and name.
    resort_id: Optional[Slug] = None
    # Kept for history; every Vail and Alterra US resort is Tier A (operator compensation).
    tier_a_candidate: Optional[bool] = None
    exclusion_reason: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class OwnershipFile(StrictModel):
    operator: str = Field(min_length=1)
    verified_date: dt.date
    sources: list[Source] = Field(min_length=1)
    resorts: list[OwnershipEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "OwnershipFile":
        _require_unique([s.id for s in self.sources], "source id")
        _require_unique([f"{r.name}|{r.state}" for r in self.resorts], "resort name and state")
        return self


def _require_unique(items: list[str], what: str) -> None:
    seen: set[str] = set()
    dupes = sorted({i for i in items if i in seen or seen.add(i)})
    if dupes:
        raise ValueError(f"duplicate {what}: {dupes}")


class SourcesFile(StrictModel):
    """data/raw/sources/*.yaml: shared sources any research file may cite."""

    sources: list[Source] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "SourcesFile":
        _require_unique([s.id for s in self.sources], "source id")
        return self
