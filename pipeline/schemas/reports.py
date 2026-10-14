"""Phase 7 crowdsourced reports and their aggregates.

Individual reports live only in data/raw/reports/queue and data/raw/reports/moderated
(gitignored) and never reach data/build. Contract: docs/map/ARCHITECTURE.md, "Phase 7".
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from .career import CertLevel, PayRecord, check_season
from .common import Slug, Source, StrictModel

ReportKind = Literal["pay", "rent", "housing"]
ReportStatus = Literal["pending", "approved", "rejected"]


class ReportValues(StrictModel):
    hourly_usd: Optional[float] = Field(default=None, gt=0)
    monthly_usd: Optional[float] = Field(default=None, gt=0)


class Report(StrictModel):
    id: Slug
    resort_id: Slug
    kind: ReportKind
    season: str
    cert_level: Optional[CertLevel] = None
    housing_site_id: Optional[Slug] = None
    values: ReportValues
    proof_url: Optional[str] = Field(default=None, pattern=r"^https?://")
    submitted_at: dt.datetime
    status: ReportStatus = "pending"
    moderation_note: Optional[str] = None
    moderated_at: Optional[dt.datetime] = None

    @field_validator("season")
    @classmethod
    def _season(cls, v: str) -> str:
        return check_season(v)

    @model_validator(mode="after")
    def _rules(self) -> "Report":
        v = self.values
        if self.kind == "pay":
            if self.cert_level is None or v.hourly_usd is None or v.monthly_usd is not None:
                raise ValueError("a pay report needs cert_level and values.hourly_usd only")
            if self.housing_site_id is not None:
                raise ValueError("housing_site_id belongs to housing reports")
        else:
            if v.monthly_usd is None or v.hourly_usd is not None or self.cert_level is not None:
                raise ValueError(f"a {self.kind} report needs values.monthly_usd only, and no cert_level")
            if (self.kind == "housing") != (self.housing_site_id is not None):
                raise ValueError("housing reports need housing_site_id; rent reports must not have one")
        if self.status == "pending" and self.moderated_at is not None:
            raise ValueError("a pending report has no moderated_at")
        if self.status != "pending" and self.moderated_at is None:
            raise ValueError("a moderated report needs moderated_at")
        if self.status == "rejected" and not (self.moderation_note or "").strip():
            raise ValueError("a rejected report needs a moderation_note")
        return self


class RentAggregate(StrictModel):
    resort_id: Slug
    season: str
    housing_site_id: Optional[Slug] = None
    low: float = Field(ge=0)
    high: float = Field(ge=0)
    count: int = Field(ge=1)
    verified_date: dt.date

    @model_validator(mode="after")
    def _order(self) -> "RentAggregate":
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        return self


class AggregatesFile(StrictModel):
    """data/raw/reports/aggregates.json: aggregates only, never individual values."""

    version: int = 1
    generated_at: str
    source: Optional[Source] = None
    pay: list[PayRecord] = Field(default_factory=list)
    rent: list[RentAggregate] = Field(default_factory=list)
    housing: list[RentAggregate] = Field(default_factory=list)
