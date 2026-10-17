"""Events and competitions: data/raw/events/<lane>.yaml (contract: docs/map/EVENTS.md; ROADMAP item 22).

One file per research lane lists its sources, the venues that are not resorts of the build, the events (one record per
event, or per stop of a series) and the calendars too large to list event by event. Every displayed value is a cited
block (source_id, as_of, confidence). Discipline, level and sign up method ids come from config/definitions.yaml
``events`` and are checked by pipeline/events.py, which also runs every check that needs another file or the build
(unique ids across files, resort ids, the season window).
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional, Union

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Latitude, Longitude, Slug, Source, StateCode, StrictModel, check_source_ref

VenueKind = Literal["resort", "nordic_center", "trail", "town", "lake", "park", "fairground", "arena", "jump_complex",
                    "other"]
VENUE_KINDS: tuple[str, ...] = VenueKind.__args__  # type: ignore[attr-defined]
Precision = Literal["venue", "town", "region"]
SignupMethod = Literal["online_registration", "lottery", "qualification", "invitation", "membership_then_online",
                       "at_venue", "none_spectator_only"]
SIGNUP_METHODS: tuple[str, ...] = SignupMethod.__args__  # type: ignore[attr-defined]
VocabId = Field(pattern=r"^[a-z][a-z0-9_]*$")


def _http_url(v: str) -> str:
    v = (v or "").strip()
    if not v.startswith(("https://", "http://")) or len(v) < 12 or " " in v:
        raise ValueError(f"expected an http(s) URL, got {v!r}")
    return v


def _clean(v: Optional[str]) -> Optional[str]:
    return " ".join(v.split()) if isinstance(v, str) and v.strip() else None


def _null_values(data, keys: tuple[str, ...]):
    """A cited block whose value is null holds no value: read it as the block left out (docs/map/EVENTS.md, a value
    that cannot be sourced is left out or null)."""
    if isinstance(data, dict):
        data = dict(data)
        for k in keys:
            v = data.get(k)
            if isinstance(v, dict) and "value" in v and v["value"] is None:
                data[k] = None
    return data


class Cited(StrictModel):
    """The provenance every displayed value carries."""

    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @field_validator("note")
    @classmethod
    def _note(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v)


class SourcedText(Cited):
    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def _text(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("value is empty")
        return v


class SourcedUrl(Cited):
    value: str

    @field_validator("value")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http_url(v)


class SourcedDate(Cited):
    value: dt.date


class SourcedTrue(Cited):
    """A sourced yes, such as season_long: the source says the program runs all season."""

    value: bool

    @field_validator("value")
    @classmethod
    def _true(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("give season_long only as true (leave it out otherwise)")
        return v


class SourcedFee(Cited):
    value: Union[int, float] = Field(ge=0)

    @field_validator("value", mode="before")
    @classmethod
    def _no_bool(cls, v):
        if isinstance(v, bool):
            raise ValueError("expected a number of US dollars")
        return v


class Named(Cited):
    """An organizer or sanctioning body: an organization, never a person."""

    name: str = Field(min_length=1)
    url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        return _http_url(v) if v else None


class DateSpan(Cited):
    """A published edition: its start, and its end when the source gives it (null: end date not seen)."""

    start: dt.date
    end: Optional[dt.date] = None

    @model_validator(mode="after")
    def _order(self) -> "DateSpan":
        if self.end is not None and self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")
        return self


class LastEdition(Cited):
    """The last edition held. Its season is always known; start and end may be null when the source names the season
    or the week but not the dates (an end needs a start)."""

    season: str
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None

    @model_validator(mode="after")
    def _in_season(self) -> "LastEdition":
        from .passes import check_season
        check_season(self.season)
        if self.end is not None and self.start is None:
            raise ValueError("end is set without a start")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")
        first = int(self.season[:4])
        if self.start is not None and not dt.date(first, 7, 1) <= self.start <= dt.date(first + 1, 6, 30):
            raise ValueError(f"start {self.start} is outside the {self.season} season (July 1 to June 30)")
        return self


class Location(Cited):
    lat: Latitude
    lon: Longitude
    precision: Precision


class VenueIn(StrictModel):
    """A venue that is not a resort of the build (a resort venue is cited by resort_id instead)."""

    id: Slug
    name: str = Field(min_length=1)
    kind: VenueKind
    state: Optional[StateCode] = None      # a US state; null for a venue outside the US
    country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")   # ISO code; set for a venue outside the US
    town: Optional[str] = None
    location: Location

    @field_validator("name", "town")
    @classmethod
    def _text(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v)

    @field_validator("country", mode="before")
    @classmethod
    def _norway(cls, v):
        # YAML 1.1 reads an unquoted NO as false; a country code is expected here, so false is Norway.
        return "NO" if v is False else v

    @model_validator(mode="after")
    def _place(self) -> "VenueIn":
        abroad = self.country not in (None, "US")
        if self.state is None and not abroad:
            raise ValueError("a venue needs a US state, or country set to a code other than US with state null")
        if self.state is not None and abroad:
            raise ValueError(f"state {self.state} is set but country is {self.country}; state is for US venues only")
        return self

    @property
    def abroad(self) -> bool:
        return self.country not in (None, "US")


class VenueRef(StrictModel):
    resort_id: Optional[Slug] = None
    venue_id: Optional[Slug] = None

    @model_validator(mode="after")
    def _one(self) -> "VenueRef":
        if (self.resort_id is None) == (self.venue_id is None):
            raise ValueError("give exactly one of resort_id and venue_id")
        return self


class Signup(StrictModel):
    """How to sign up. Any part may be null when no source gives it (docs/map/EVENTS.md: nothing is invented)."""

    method: Optional[SignupMethod] = None
    url: Optional[SourcedUrl] = None       # the registration page, or the event page that explains sign up
    platform: Optional[SourcedText] = None
    opens: Optional[SourcedDate] = None
    deadline: Optional[SourcedDate] = None
    fee_usd: Optional[SourcedFee] = None   # the lowest adult entry fee; the note says which
    requirements: Optional[SourcedText] = None

    @model_validator(mode="before")
    @classmethod
    def _empty(cls, data):
        return _null_values(data, ("url", "platform", "opens", "deadline", "fee_usd", "requirements"))

    @model_validator(mode="after")
    def _check(self) -> "Signup":
        if self.opens and self.deadline and self.deadline.value < self.opens.value:
            raise ValueError(f"deadline {self.deadline.value} is before opens {self.opens.value}")
        return self


class Series(StrictModel):
    id: Slug
    name: str = Field(min_length=1)


class EventIn(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    discipline: str = VocabId
    disciplines_also: list[str] = Field(default_factory=list)
    levels: list[str] = Field(min_length=1)
    venue: VenueRef
    series: Optional[Series] = None
    organizer: Optional[Named] = None      # null when no source names it
    sanctioning: Optional[Named] = None
    format: Optional[SourcedText] = None
    edition_2026_27: Optional[DateSpan] = None
    last_edition: Optional[LastEdition] = None
    usual_timing: Optional[SourcedText] = None
    # A season long program (a NASTAR program, a weekly race league, rail jam nights) has no single date: it gives
    # usual_timing ("Saturdays, January to March") or season_long, and both editions null.
    season_long: Optional[SourcedTrue] = None
    signup: Optional[Signup] = None        # null when no source says anything about signing up
    website: Optional[SourcedUrl] = None   # the event's page; null when no source gives one (never guessed)
    results_url: Optional[SourcedUrl] = None
    calendar_url: Optional[SourcedUrl] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v) or v

    @field_validator("disciplines_also", mode="before")
    @classmethod
    def _also(cls, v):
        return [] if v is None else v

    @model_validator(mode="before")
    @classmethod
    def _empty(cls, data):
        return _null_values(data, ("format", "usual_timing", "season_long", "website", "results_url", "calendar_url"))

    @model_validator(mode="after")
    def _when(self) -> "EventIn":
        if self.edition_2026_27 is None and self.last_edition is None and self.usual_timing is None \
                and self.season_long is None:
            raise ValueError("give edition_2026_27, or last_edition when the 2026/27 date is not published; a season "
                             "long program with neither gives usual_timing or season_long")
        return self

    @property
    def program(self) -> bool:
        """True for a season long program: no edition and no last edition, only usual_timing or season_long."""
        return self.edition_2026_27 is None and self.last_edition is None

    @property
    def dated(self) -> bool:
        """True when some date is known: the 2026/27 edition, or a last edition with its start."""
        return self.edition_2026_27 is not None or (self.last_edition is not None and self.last_edition.start is not None)


class CalendarIn(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    discipline: str = VocabId
    region: str = Field(pattern=r"^(national|[A-Z]{2})$")   # national or a two letter state code
    url: SourcedUrl


class EventsBatch(StrictModel):
    batch: str = Field(pattern=r"^events-[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    venues: list[VenueIn] = Field(default_factory=list)
    events: list[EventIn] = Field(default_factory=list)
    calendars: list[CalendarIn] = Field(default_factory=list)

    @field_validator("venues", "events", "calendars", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v


class ResortEventItem(StrictModel):
    """One event at a resort, as Resort.events lists it (the resort panel's "Events here"). start and end are null
    when the source does not give them (a last edition known by its season, an end date not seen). The cited fields
    are the dates' provenance, null for an event that gives no date."""

    source_id: Optional[Slug] = None
    as_of: Optional[dt.date] = None
    confidence: Optional[Confidence] = None
    note: Optional[str] = None
    id: Slug
    name: str
    discipline: str
    discipline_label: str
    group: str
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    season: Optional[str] = None   # the season of these dates: the map season, or the last edition's
    published: bool                # True: the dates are the map season's edition; False: the last edition's
    season_long: bool = False      # a season long program: timing holds its usual_timing, if any
    timing: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        return check_source_ref(v, info) if v else v


class ResortEvents(StrictModel):
    count: int = Field(ge=1)
    items: list[ResortEventItem] = Field(min_length=1)
