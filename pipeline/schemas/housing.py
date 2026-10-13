"""Phase 5 schemas: housing sites, rent baseline, transit extract, routing, detail file.

Contract: docs/map/ARCHITECTURE.md, "Phase 5 housing and commute".
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import (
    Confidence, Latitude, Longitude, Slug, Source, Sourced, SourcedNumber, SourcedString, StrictModel,
    check_source_ref,
)

HousingType = Literal["resort_owned", "master_lease", "market"]
EMPLOYEE_HOUSING = ("resort_owned", "master_lease")


# --------------------------------------------------------------- geometry
def _positions(coords: Any, depth: int) -> None:
    """Check nesting depth and lon/lat ranges of GeoJSON coordinates."""
    if depth == 0:
        if not (isinstance(coords, list) and len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2])):
            raise ValueError("expected a [lon, lat] position")
        if not (-180 <= coords[0] <= 180 and -90 <= coords[1] <= 90):
            raise ValueError(f"position out of range: {coords}")
        return
    if not isinstance(coords, list) or not coords:
        raise ValueError("empty coordinates")
    for c in coords:
        _positions(c, depth - 1)


class LineGeometry(StrictModel):
    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2)

    @model_validator(mode="after")
    def _check(self) -> "LineGeometry":
        _positions(self.coordinates, 1)
        return self


class MultiLineGeometry(StrictModel):
    type: Literal["MultiLineString"]
    coordinates: list[list[list[float]]] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "MultiLineGeometry":
        _positions(self.coordinates, 2)
        if any(len(line) < 2 for line in self.coordinates):
            raise ValueError("each line needs 2 or more positions")
        return self


class AreaGeometry(StrictModel):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "AreaGeometry":
        _positions(self.coordinates, 2 if self.type == "Polygon" else 3)
        return self


# ------------------------------------------------------------- raw: housing
class RentRange(StrictModel):
    low: float = Field(ge=0)
    high: float = Field(ge=0)

    @model_validator(mode="after")
    def _order(self) -> "RentRange":
        if self.low > self.high:
            raise ValueError("rent low must not exceed high")
        return self


class SourcedRent(Sourced):
    """Monthly rent range in USD."""

    value: RentRange  # type: ignore[assignment]


class HousingSite(StrictModel):
    id: Slug
    resort_id: Slug
    name: str = Field(min_length=1)
    type: HousingType
    lat: Latitude
    lon: Longitude
    location_source_id: Slug
    # building only when the address is publicly listed: `address` must then be given with its source
    precision: Literal["building", "neighborhood"]
    address: Optional[SourcedString] = None
    rent_monthly: Optional[SourcedRent] = None
    occupancy: Optional[Literal["shared", "private", "mixed"]] = None
    deposit: Optional[SourcedNumber] = None
    eligibility_note: Optional[str] = None
    confidence: Confidence
    verified_date: dt.date

    @field_validator("location_source_id")
    @classmethod
    def _loc(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _precision(self) -> "HousingSite":
        if self.precision == "building" and self.address is None:
            raise ValueError("building precision needs a publicly listed address: give `address` with its source")
        if self.deposit is not None and self.deposit.value < 0:
            raise ValueError("deposit must not be negative")
        return self


class HousingBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    sites: list[HousingSite] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "HousingBatch":
        ids = [s.id for s in self.sites]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate housing site id: {dupes}")
        reported = [s.id for s in self.sites if s.rent_monthly is not None and s.rent_monthly.confidence == "reported"]
        if reported:
            raise ValueError(f"sites {reported}: rent_monthly cannot be confidence reported in a housing file; reported "
                             "rents come only from aggregates of 3 or more approved reports (data/raw/reports)")
        return self


# ------------------------------------------------------------- rent baseline
class FmrSet(StrictModel):
    studio: Optional[SourcedNumber] = None
    one_br: Optional[SourcedNumber] = None
    two_br: Optional[SourcedNumber] = None


class RentBaseline(StrictModel):
    county_fips: str = Field(pattern=r"^\d{5}$")
    county_name: str = Field(min_length=1)
    fmr: FmrSet = Field(default_factory=FmrSet)
    acs_median_gross_rent: Optional[SourcedNumber] = None


# ------------------------------------------------------------------- transit
class TransitRoute(StrictModel):
    id: str = Field(min_length=1)          # "<agency>:<route_id>", unique across agencies
    agency: str = Field(min_length=1)
    short_name: Optional[str] = None
    long_name: Optional[str] = None
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    geometry: MultiLineGeometry


class TransitStop(StrictModel):
    id: str = Field(min_length=1)          # "<agency>:<stop_id>"
    name: str = Field(min_length=1)
    lat: Latitude
    lon: Longitude
    route_ids: list[str] = Field(min_length=1)


class TransitPattern(StrictModel):
    """One distinct stop sequence of a route, with median minutes from its first stop."""

    route_id: str
    stop_ids: list[str] = Field(min_length=2)
    offsets_min: list[float] = Field(min_length=2)
    trip_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _align(self) -> "TransitPattern":
        if len(self.stop_ids) != len(self.offsets_min):
            raise ValueError("stop_ids and offsets_min differ in length")
        if any(b < a for a, b in zip(self.offsets_min, self.offsets_min[1:])):
            raise ValueError("offsets_min must not decrease")
        return self


class TransitFeed(StrictModel):
    """The feed behind an extract: catalog fields from feeds.yaml plus the feed's own validity dates."""

    mdb_source_id: Optional[int] = None       # Mobility Database catalog id
    url: Optional[str] = None                 # the agency's own feed URL (also the source url)
    mirror_url: Optional[str] = None          # the catalog mirror copy that was downloaded
    license: Optional[str] = None             # as listed in the catalog; null when it lists none
    agency_ids: Optional[list[str]] = None    # GTFS agencies kept from a shared feed
    version: Optional[str] = None             # feed_info.txt feed_version
    valid_from: Optional[dt.date] = None
    valid_to: Optional[dt.date] = None
    validity_basis: Optional[Literal["feed_info", "calendar", "feed_info+calendar"]] = None
    as_of: Optional[dt.date] = None           # the start of the validity period

    @model_validator(mode="after")
    def _order(self) -> "TransitFeed":
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to is before valid_from")
        return self


class TransitExtract(StrictModel):
    """data/raw/transit/<agency>.json, written by pipeline/transit.py from a GTFS zip."""

    version: int = 1
    agency: Slug
    source: Source
    feed: Optional[TransitFeed] = None
    search_radius_km: float = Field(gt=0)
    simplify_tolerance_m: Optional[float] = Field(default=None, gt=0)
    routes: list[TransitRoute] = Field(default_factory=list)
    stops: list[TransitStop] = Field(default_factory=list)
    patterns: list[TransitPattern] = Field(default_factory=list)


# ---------------------------------------------------------------- build: out
class CommuteOut(StrictModel):
    mode: Literal["drive", "bus", "walk", "shuttle"]
    minutes: float = Field(ge=0)
    distance_mi: Optional[float] = Field(default=None, ge=0)
    transit_route_ids: list[str] = Field(default_factory=list)
    geometry: Optional[LineGeometry] = None
    source_id: Slug

    @field_validator("source_id")
    @classmethod
    def _src(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class HousingOut(HousingSite):
    commutes: list[CommuteOut] = Field(default_factory=list)


class TransitFeedRef(StrictModel):
    """One feed shown on a detail map, with its validity (``as_of`` is the start)."""

    source_id: Slug
    agency: Slug
    as_of: dt.date
    valid_to: Optional[dt.date] = None

    @field_validator("source_id")
    @classmethod
    def _src(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class TransitOut(StrictModel):
    routes: list[TransitRoute]
    stops: list[TransitStop]
    source_ids: list[Slug] = Field(min_length=1)
    feeds: list[TransitFeedRef] = Field(default_factory=list)


class Isochrone(StrictModel):
    minutes: int = Field(gt=0)
    mode: Literal["drive"] = "drive"
    geometry: AreaGeometry
    source_id: Slug

    @field_validator("source_id")
    @classmethod
    def _src(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class BaseArea(StrictModel):
    lat: Latitude
    lon: Longitude
    source_id: Slug


class LiftFeature(StrictModel):
    type: Literal["Feature"]
    geometry: LineGeometry
    properties: dict[str, Any]

    @model_validator(mode="after")
    def _props(self) -> "LiftFeature":
        if set(self.properties) != {"name", "lift_type", "source_id"}:
            raise ValueError("lift properties are exactly name, lift_type, source_id")
        return self


class LiftCollection(StrictModel):
    type: Literal["FeatureCollection"]
    features: list[LiftFeature]


class DetailFile(StrictModel):
    version: int
    generated_at: str
    resort_id: Slug
    base_area: BaseArea
    lifts: LiftCollection
    housing: list[HousingOut]
    rent_baseline: Optional[RentBaseline] = None
    rent_reported: Optional[SourcedRent] = None   # Phase 7: area rent from 3+ approved reports
    transit: Optional[TransitOut] = None
    isochrones: list[Isochrone] = Field(default_factory=list)


class HousingSummary(StrictModel):
    site_count: int = Field(ge=0)
    cheapest_rent_monthly_low: Optional[float] = Field(default=None, ge=0)
    cheapest_site_id: Optional[Slug] = None
    has_transit: bool
