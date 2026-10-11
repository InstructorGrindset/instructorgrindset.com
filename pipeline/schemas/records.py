"""HousingSite, Commute, PayRecord, Report (Phases 4, 5 and 7)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Latitude, Longitude, Slug, StrictModel, check_source_ref


class HousingSite(StrictModel):
    id: Slug
    resort_id: Slug
    name: str = Field(min_length=1)
    type: Literal["resort_owned", "master_lease", "market"]
    lat: Latitude
    lon: Longitude
    precision: Literal["building", "neighborhood"]
    rent_monthly_low: Optional[float] = Field(default=None, ge=0)
    rent_monthly_high: Optional[float] = Field(default=None, ge=0)
    occupancy: Optional[str] = None
    deposit: Optional[float] = Field(default=None, ge=0)
    confidence: Confidence
    verified_date: dt.date
    sources: list[Slug] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def _sources_defined(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        return v

    @model_validator(mode="after")
    def _rent_order(self) -> "HousingSite":
        lo, hi = self.rent_monthly_low, self.rent_monthly_high
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("rent_monthly_low must not exceed rent_monthly_high")
        return self


class Commute(StrictModel):
    housing_id: Slug
    resort_id: Slug
    mode: Literal["drive", "bus", "walk", "shuttle"]
    minutes: float = Field(ge=0)
    distance_mi: float = Field(ge=0)
    transit_route_ids: list[str] = Field(default_factory=list)
    geometry: Optional[dict[str, Any]] = None


class PayRecord(StrictModel):
    resort_id: Slug
    season: str = Field(pattern=r"^\d{4}-\d{2}$")  # e.g. "2026-27"
    cert_level: str = Field(min_length=1)
    hourly_low: float = Field(ge=0)
    hourly_high: float = Field(ge=0)
    confidence: Confidence
    source: Slug
    verified_date: dt.date

    @field_validator("source")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _order(self) -> "PayRecord":
        if self.hourly_low > self.hourly_high:
            raise ValueError("hourly_low must not exceed hourly_high")
        return self


class Report(StrictModel):
    """Raw crowdsourced input. Never rendered directly; only aggregates of 3+."""

    id: str = Field(min_length=1)
    resort_id: Slug
    kind: Literal["pay", "rent", "housing"]
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    values: dict[str, Any]
    status: Literal["pending", "approved", "rejected"] = "pending"
