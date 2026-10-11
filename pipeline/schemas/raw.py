"""Raw research inputs: data/raw/tier_a/*.yaml and data/raw/ownership/*.yaml.

The format is documented in data/raw/tier_a/README.md.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Latitude, Longitude, Slug, Source, SourcedString, StateCode, StrictModel, check_source_ref
from .resort import Clients, Fit, Season, Size, Terrain, TierACriterion


class TierARecord(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    state: StateCode
    owner: Optional[SourcedString] = None
    lat: Latitude
    lon: Longitude
    location_source_id: Slug
    cluster_hint: Slug
    tier_a_criteria: list[TierACriterion] = Field(min_length=1)
    size: Size = Field(default_factory=Size)
    season: Season = Field(default_factory=Season)
    terrain: Terrain = Field(default_factory=Terrain)
    fit: Fit = Field(default_factory=Fit)
    clients: Clients = Field(default_factory=Clients)

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
        return self


class OwnershipEntry(StrictModel):
    name: str = Field(min_length=1)
    state: StateCode
    source_id: Slug
    tier_a_candidate: bool
    exclusion_reason: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _reason(self) -> "OwnershipEntry":
        if not self.tier_a_candidate and not (self.exclusion_reason or "").strip():
            raise ValueError(f"{self.name}: exclusion_reason is required when tier_a_candidate is false")
        return self


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
