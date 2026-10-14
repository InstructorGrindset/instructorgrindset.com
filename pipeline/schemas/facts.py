"""Resort facts overlay: data/raw/facts/<batch>.yaml (contract: ARCHITECTURE.md, "Resort facts overlay")."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import Field, field_validator, model_validator

from .common import Slug, Source, SourcedMonthDay, SourcedNumber, StrictModel


class PositiveNumber(SourcedNumber):
    @field_validator("value")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be a positive number")
        return v


class SeasonDays(PositiveNumber):
    @field_validator("value")
    @classmethod
    def _days(cls, v: float) -> float:
        if v > 366:
            raise ValueError("a season length is at most 366 days")
        return v


class FactsRecord(StrictModel):
    resort_id: Slug
    open_typical: Optional[SourcedMonthDay] = None
    close_typical: Optional[SourcedMonthDay] = None
    length_days: Optional[SeasonDays] = None
    training_start: Optional[SourcedMonthDay] = None
    annual_snowfall_in: Optional[PositiveNumber] = None
    acres: Optional[PositiveNumber] = None
    vertical_ft: Optional[PositiveNumber] = None
    lifts: Optional[PositiveNumber] = None

    @model_validator(mode="after")
    def _not_empty(self) -> "FactsRecord":
        if all(getattr(self, f) is None for f in FACT_FIELDS):
            raise ValueError("a facts record needs at least one field")
        return self


FACT_FIELDS = ("open_typical", "close_typical", "length_days", "training_start", "annual_snowfall_in",
               "acres", "vertical_ft", "lifts")


class FactsBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    records: list[FactsRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "FactsBatch":
        ids = [r.resort_id for r in self.records]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate resort_id: {dupes}")
        return self
