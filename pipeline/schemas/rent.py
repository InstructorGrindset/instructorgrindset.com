"""Rent used by lucrativeness: sourced median rent figures (research input) and the rent block in the build.

* data/raw/rent/median_rent_<batch>.yaml: ``{batch, researched_date, sources: [Source], figures: [MedianRentFigure]}``.
  Each figure is one published "median rent" for a resort's town from one source (Zillow, Redfin, Apartments.com,
  a news article, a blog). Raw files never carry ``reported`` figures: reported values come only from aggregates of
  3 or more approved reports (data/raw/reports).
* ``Resort.career.lucrativeness.rent``: the town's rent (RentUsed), with every source it came from. It shows in the
  Housing section and no longer feeds lucrativeness (owner decision 2026-10-14).

Contract: docs/map/ARCHITECTURE.md, "Phase 4 career" (Lucrativeness rent).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref

RentUnit = Literal["all", "studio", "1br", "2br", "3br"]
FigureGeography = Literal["city", "zip", "county", "metro"]
Publisher = Literal["zillow", "redfin", "apartments_com", "rentcafe", "rent_com", "zumper", "news", "blog", "other"]
PERIOD_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")


class MedianRentFigure(StrictModel):
    resort_ids: list[Slug] = Field(min_length=1)
    town: str = Field(min_length=1)
    geography: FigureGeography
    unit: RentUnit
    median_rent_usd: float = Field(gt=0, lt=100000)
    period: str                              # the data period: "2026", "2026-09" or "2026-09-30"
    publisher: Publisher
    publisher_name: Optional[str] = None     # shown for news, blog and other ("Summit Daily"); else the source title
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("period", mode="before")
    @classmethod
    def _period(cls, v: object) -> object:
        if isinstance(v, (dt.date, int)):
            v = v.isoformat() if isinstance(v, dt.date) else str(v)
        if not isinstance(v, str) or not PERIOD_RE.match(v):
            raise ValueError("period must be YYYY, YYYY-MM or YYYY-MM-DD")
        return v

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @field_validator("confidence")
    @classmethod
    def _not_reported(cls, v: str) -> str:
        if v == "reported":
            raise ValueError("a research file cannot hold a reported figure; reported values come only from "
                             "aggregates of 3 or more approved reports (data/raw/reports)")
        return v


class MedianRentBatch(StrictModel):
    batch: str = Field(min_length=1)
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    figures: list[MedianRentFigure] = Field(default_factory=list)


# ------------------------------------------------------------------ build output
class RentGeography(StrictModel):
    level: Literal["zip", "city", "county", "metro"]
    id: Optional[str] = None
    name: str = Field(min_length=1)


class RentPeriod(StrictModel):
    first: str
    last: str


class RentUsed(StrictModel):
    """The rent lucrativeness used: one number with every source behind it.

    ``basis`` is the ``rent.lucrativeness_rent`` step that supplied it. ``median_rent`` carries ``unit``, ``count``,
    ``publishers`` and ``period``; ``zillow_zori`` and the county baselines carry ``geography``."""
    value: float = Field(ge=0)
    basis: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_ids: list[Slug] = Field(min_length=1)
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None
    unit: Optional[RentUnit] = None
    count: Optional[int] = Field(default=None, ge=1)
    publishers: list[str] = Field(default_factory=list)
    period: Optional[RentPeriod] = None
    geography: Optional[RentGeography] = None
    stale: bool = False

    @field_validator("source_ids")
    @classmethod
    def _sources_defined(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        return v

    @model_validator(mode="after")
    def _median_fields(self) -> "RentUsed":
        if self.basis == "median_rent" and (self.count is None or self.unit is None or not self.publishers):
            raise ValueError("a median rent needs unit, count and publishers")
        return self
