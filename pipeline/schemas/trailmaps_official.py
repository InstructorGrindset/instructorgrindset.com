"""Official resort trail map links: data/raw/trailmaps_official/<batch>.yaml (contract: ARCHITECTURE.md,
"Official trail maps"). Links only: the maps are the resorts' (or their artists') copyrighted art and are never
downloaded, rehosted or embedded."""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref

TrailmapFormat = Literal["pdf", "jpg", "png", "page"]
SEASON_RE = re.compile(r"^(\d{4})/(\d{2})$")
EXTENSIONS = {"pdf": (".pdf",), "jpg": (".jpg", ".jpeg"), "png": (".png",)}


class OfficialTrailmap(StrictModel):
    """The link as it reaches resorts.json (Resort.official_trailmap)."""

    url: str = Field(min_length=1)
    format: TrailmapFormat
    season: Optional[str] = None  # "2025/26"; null when the resort's page names no season
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        u = urlparse(v)
        if u.scheme != "https" or not u.hostname:
            raise ValueError("url must be an https link")
        return v

    @field_validator("season")
    @classmethod
    def _season(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        m = SEASON_RE.match(v)
        if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
            raise ValueError('season must look like "2025/26" (consecutive years)')
        return v

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _format_matches(self) -> "OfficialTrailmap":
        path = urlparse(self.url).path.lower()
        if self.format in EXTENSIONS and not path.endswith(EXTENSIONS[self.format]):
            raise ValueError(f"format {self.format} needs a url ending in {' or '.join(EXTENSIONS[self.format])}")
        if self.format == "page" and path.endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise ValueError("a url to a file needs format pdf, jpg or png, not page")
        return self


class OfficialTrailmapRecord(OfficialTrailmap):
    resort_id: Slug


class OfficialTrailmapBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    trailmaps: list[OfficialTrailmapRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "OfficialTrailmapBatch":
        ids = [r.resort_id for r in self.trailmaps]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"resort ids listed twice: {', '.join(dup)}")
        return self
