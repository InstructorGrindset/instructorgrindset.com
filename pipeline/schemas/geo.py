"""State and Cluster."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Slug, StateCode, StrictModel, check_source_ref


class State(StrictModel):
    code: StateCode
    name: str = Field(min_length=1)
    cluster_ids: list[Slug]
    resort_count: int = Field(ge=1)


class Geometry(StrictModel):
    """GeoJSON Polygon or MultiPolygon in lon/lat."""

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]

    @model_validator(mode="after")
    def _rings(self) -> "Geometry":
        polys = [self.coordinates] if self.type == "Polygon" else self.coordinates
        if not polys:
            raise ValueError("geometry has no polygons")
        for poly in polys:
            if not poly:
                raise ValueError("polygon has no rings")
            for ring in poly:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("each ring needs 4 or more positions and must be closed")
                for pos in ring:
                    lon, lat = pos[0], pos[1]
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        raise ValueError(f"position out of range: {pos}")
        return self


class Cluster(StrictModel):
    id: Slug
    name: str = Field(min_length=1)
    states: list[StateCode] = Field(min_length=1)
    polygon: Geometry
    resort_ids: list[Slug] = Field(min_length=1)
    tier_a_count: int = Field(ge=0)
    overview_md: Optional[str] = None
    sources: list[Slug] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def _sources_defined(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        return v
