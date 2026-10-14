"""Trail map file schema: data/build/trailmap/<resort_id>.json (contract: ARCHITECTURE.md, "Trail map")."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Slug, StrictModel, check_source_ref
from .housing import LiftCollection, _positions

Difficulty = Literal["novice", "easy", "intermediate", "advanced", "expert", "freeride", "extreme"]
DifficultyUS = Literal["green", "blue", "black", "double_black", "terrain_park"]
DEPTH = {"LineString": 1, "MultiLineString": 2, "Polygon": 2, "MultiPolygon": 3}


class RunGeometry(StrictModel):
    type: Literal["LineString", "MultiLineString", "Polygon", "MultiPolygon"]
    coordinates: list[Any] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "RunGeometry":
        _positions(self.coordinates, DEPTH[self.type])
        return self


class RunProperties(StrictModel):
    name: Optional[str] = None
    difficulty: Optional[Difficulty] = None
    difficulty_us: Optional[DifficultyUS] = None
    grooming: Optional[str] = None
    lit: Optional[bool] = None


class RunFeature(StrictModel):
    type: Literal["Feature"]
    geometry: RunGeometry
    properties: RunProperties


class RunCollection(StrictModel):
    type: Literal["FeatureCollection"]
    features: list[RunFeature] = Field(min_length=1)


class TrailmapFile(StrictModel):
    version: int
    generated_at: str
    resort_id: Slug
    source_id: Literal["openskimap-runs"]
    runs: RunCollection
    lifts: LiftCollection
    bounds: list[float] = Field(min_length=4, max_length=4)

    @field_validator("source_id")
    @classmethod
    def _src(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _bounds(self) -> "TrailmapFile":
        w, s, e, n = self.bounds
        if not (-180 <= w <= e <= 180 and -90 <= s <= n <= 90):
            raise ValueError("bounds must be [west, south, east, north]")
        return self
