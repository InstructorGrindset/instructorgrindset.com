"""Pydantic models for the Phase 3 climate output.

Contract: docs/map/ARCHITECTURE.md, section ``climate/{resort_id}.json``.

    { version, generated_at, resort_id,
      source_ids: string[], period: { start_year, end_year },
      months: ClimateMonth[12] }   // Oct, Nov, Dec, Jan, ..., Sep

    ClimateMonth { resort_id, month (1..12), snowfall_in, powder_days, banner_days,
                   avg_storm_depth_in, density_pct_median,
                   density_mix: { dry, average, moist } | null,
                   density_source: "snotel" | "estimated" | null,
                   temp_high_f, temp_low_f, wind_mph, sun_pct }

``period`` holds calendar years: data runs from October 1 of ``start_year``
through September 30 of ``end_year``, so the file averages
``end_year - start_year`` ski seasons.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Ski season order used by every climate file.
SEASON_MONTH_ORDER: tuple[int, ...] = (10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9)

DENSITY_MIX_TOLERANCE = 1e-3

_SLUG = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DensityMix(_Strict):
    """Share of storms in each density class. Fractions sum to 1."""

    dry: float = Field(ge=0, le=1)
    average: float = Field(ge=0, le=1)
    moist: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sums_to_one(self) -> "DensityMix":
        total = self.dry + self.average + self.moist
        if abs(total - 1.0) > DENSITY_MIX_TOLERANCE:
            raise ValueError(f"density_mix fractions must sum to 1, got {total:.4f}")
        return self


class ClimateMonth(_Strict):
    resort_id: str = Field(pattern=_SLUG)
    month: int = Field(ge=1, le=12)
    snowfall_in: Optional[float] = Field(default=None, ge=0)
    powder_days: Optional[float] = Field(default=None, ge=0, le=31)
    banner_days: Optional[float] = Field(default=None, ge=0, le=31)
    avg_storm_depth_in: Optional[float] = Field(default=None, ge=0)
    density_pct_median: Optional[float] = Field(default=None, gt=0, le=100)
    density_mix: Optional[DensityMix] = None
    density_source: Optional[Literal["snotel", "estimated"]] = None
    temp_high_f: Optional[float] = Field(default=None, ge=-80, le=130)
    temp_low_f: Optional[float] = Field(default=None, ge=-80, le=130)
    wind_mph: Optional[float] = Field(default=None, ge=0, le=200)
    sun_pct: Optional[float] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _consistency(self) -> "ClimateMonth":
        has_density = self.density_pct_median is not None or self.density_mix is not None
        if has_density and self.density_source is None:
            raise ValueError("density values need a density_source")
        if not has_density and self.density_source is not None:
            raise ValueError("density_source is set but there are no density values")
        if (
            self.powder_days is not None
            and self.banner_days is not None
            and self.banner_days > self.powder_days + 1e-9
        ):
            raise ValueError("banner_days cannot exceed powder_days")
        if (
            self.temp_high_f is not None
            and self.temp_low_f is not None
            and self.temp_low_f > self.temp_high_f + 1e-9
        ):
            raise ValueError("temp_low_f cannot exceed temp_high_f")
        return self


class ClimatePeriod(_Strict):
    start_year: int = Field(ge=1940, le=2100)
    end_year: int = Field(ge=1941, le=2100)

    @model_validator(mode="after")
    def _ordered(self) -> "ClimatePeriod":
        if self.end_year <= self.start_year:
            raise ValueError("period.end_year must be after period.start_year")
        return self


class ClimateFile(_Strict):
    version: int = Field(ge=1)
    generated_at: datetime
    resort_id: str = Field(pattern=_SLUG)
    source_ids: list[str] = Field(min_length=1)
    period: ClimatePeriod
    months: list[ClimateMonth] = Field(min_length=12, max_length=12)

    @field_validator("source_ids")
    @classmethod
    def _source_ids(cls, value: list[str]) -> list[str]:
        import re

        for sid in value:
            if not re.match(_SLUG, sid):
                raise ValueError(f"source_id {sid!r} is not a kebab case slug")
        if len(set(value)) != len(value):
            raise ValueError("source_ids must be unique")
        return value

    @model_validator(mode="after")
    def _months(self) -> "ClimateFile":
        order = tuple(m.month for m in self.months)
        if order != SEASON_MONTH_ORDER:
            raise ValueError(f"months must be in season order {SEASON_MONTH_ORDER}, got {order}")
        for m in self.months:
            if m.resort_id != self.resort_id:
                raise ValueError(
                    f"month {m.month} has resort_id {m.resort_id!r}, expected {self.resort_id!r}"
                )
        return self
