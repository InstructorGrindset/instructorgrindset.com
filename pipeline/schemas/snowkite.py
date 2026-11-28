"""Snowkite zones: data/raw/snowkite/zones_<lane>.yaml (contract: docs/map/SNOWKITE.md; ROADMAP item 24).

One file per research lane lists its sources and its zones. Every displayed value is a cited block (source_id, as_of,
confidence); a value no source gives is null. Text limits, the coordinate boxes and the vocabulary labels live in
config/definitions.yaml ``snowkite`` and are checked by pipeline/snowkite.py, which also runs every check that needs
another file or the build (unique ids across files, event ids, sources used).
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Slug, Source, StateCode, StrictModel, check_source_ref
from .events import Cited, SourcedText, SourcedUrl, _clean, _null_values

ZoneKind = Literal["lake", "reservoir", "plateau", "valley", "glacier", "prairie", "dunes", "alpine_basin", "river",
                   "other"]
ZONE_KINDS: tuple[str, ...] = ZoneKind.__args__  # type: ignore[attr-defined]
Surface = Literal["frozen_lake", "open_snowfield", "glacier", "alpine_basin", "prairie", "frozen_river", "dunes_snow"]
SURFACES: tuple[str, ...] = Surface.__args__  # type: ignore[attr-defined]
ZoneStatus = Literal["established", "candidate"]
Precision = Literal["spot", "area", "region"]
ReportStatus = Literal["draft", "reviewed"]

CITED_KEYS = ("elevation_ft", "months", "wind", "snow_ice", "terrain", "website", "access")


class Location(Cited):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    precision: Precision


class Elevation(Cited):
    value: int = Field(ge=-300, le=20500)

    @field_validator("value", mode="before")
    @classmethod
    def _int(cls, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("expected a number of feet")
        if float(v) != int(v):
            raise ValueError("give whole feet")
        return int(v)


class Months(Cited):
    """The months a zone is usable, 1 to 12; the note says how a source words the season."""

    value: list[int] = Field(min_length=1, max_length=12)

    @field_validator("value")
    @classmethod
    def _months(cls, v: list[int]) -> list[int]:
        if any(isinstance(m, bool) or not 1 <= m <= 12 for m in v):
            raise ValueError("months are numbers 1 to 12")
        if len(set(v)) != len(v):
            raise ValueError("a month is listed twice")
        return v


class Access(Cited):
    """Road, parking, fees and permit as one block with one source. Every field is optional; one at least is set."""

    road: Optional[str] = None
    parking: Optional[str] = None
    fees: Optional[str] = None
    permit: Optional[str] = None

    @field_validator("road", "parking", "fees", "permit", mode="before")
    @classmethod
    def _text(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("expected text")
        return _clean(v)

    @model_validator(mode="after")
    def _some(self) -> "Access":
        if not any((self.road, self.parking, self.fees, self.permit)):
            raise ValueError("access gives none of road, parking, fees and permit; leave it null instead")
        return self


class Report(StrictModel):
    """The zone report's summary: editorial text drafted from the cited sources. Only `reviewed` renders."""

    status: ReportStatus
    summary: str = Field(min_length=1)
    source_ids: list[Slug] = Field(min_length=1)

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("summary is empty")
        return v

    @field_validator("source_ids")
    @classmethod
    def _sources(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for s in v:
            check_source_ref(s, info)
        if len(set(v)) != len(v):
            raise ValueError("a source is listed twice")
        return v


class ZoneIn(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    status: ZoneStatus
    kind: ZoneKind
    state: StateCode
    location: Optional[Location] = None    # null: no map point yet (the zone lists, searches and reports, no marker)
    elevation_ft: Optional[Elevation] = None
    surface: list[Surface] = Field(default_factory=list)
    months: Optional[Months] = None
    wind: Optional[SourcedText] = None
    snow_ice: Optional[SourcedText] = None
    terrain: Optional[SourcedText] = None
    hazards: list[SourcedText] = Field(default_factory=list)
    access: Optional[Access] = None
    website: Optional[SourcedUrl] = None
    events: list[Slug] = Field(default_factory=list)
    report: Optional[Report] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v) or v

    @field_validator("surface")
    @classmethod
    def _surface(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("a surface is listed twice")
        return v

    @field_validator("hazards", "events", "surface", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("an event id is listed twice")
        return v

    @model_validator(mode="before")
    @classmethod
    def _empty(cls, data):
        return _null_values(data, CITED_KEYS)

    def cited(self) -> list[Cited]:
        """Every cited block of the zone, for the source checks."""
        out: list[Cited] = [self.location] if self.location is not None else []
        for k in ("elevation_ft", "months", "wind", "snow_ice", "terrain", "access", "website"):
            if getattr(self, k) is not None:
                out.append(getattr(self, k))
        out.extend(self.hazards)
        return out

    def source_ids(self) -> set[str]:
        """The sources a zone cites: its cited blocks and the sources of its report."""
        ids = {c.source_id for c in self.cited()}
        if self.report is not None:
            ids.update(self.report.source_ids)
        return ids


class ZonesBatch(StrictModel):
    batch: str = Field(pattern=r"^snowkite-zones-[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    zones: list[ZoneIn] = Field(default_factory=list)

    @field_validator("zones", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v
