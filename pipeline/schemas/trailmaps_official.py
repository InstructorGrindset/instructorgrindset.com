"""Official resort trail map links: data/raw/trailmaps_official/<batch>.yaml (contract: ARCHITECTURE.md,
"Official trail maps"). The maps are the resorts' (or their artists') copyrighted art: the site links to the page and,
when `image_url` is set, shows that file in the trail map dialog straight from the resort's servers (hotlinked). It
never downloads, copies, rehosts or modifies the art."""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional
from urllib.parse import parse_qsl, urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref

TrailmapFormat = Literal["pdf", "jpg", "png", "page"]
ImageFormat = Literal["jpg", "png", "webp", "pdf"]
SEASON_RE = re.compile(r"^(\d{4})/(\d{2})$")
EXTENSIONS = {"pdf": (".pdf",), "jpg": (".jpg", ".jpeg"), "png": (".png",), "webp": (".webp",)}
IMAGE_FIELDS = ("image_url", "image_format", "image_source_id", "image_as_of", "image_confidence")


def _matches_format(url: str, fmt: str) -> bool:
    """The url path ends in the format's extension, or an image CDN names it in a fmt query parameter
    (scene7 style: .../is/image/<name>?fmt=jpg)."""
    u = urlparse(url)
    if u.path.lower().endswith(EXTENSIONS[fmt]):
        return True
    q = {k.lower(): v.lower() for k, v in parse_qsl(u.query)}
    return q.get("fmt") in ({fmt, "jpeg"} if fmt == "jpg" else {fmt})


class OfficialTrailmap(StrictModel):
    """The link as it reaches resorts.json (Resort.official_trailmap)."""

    url: str = Field(min_length=1)
    format: TrailmapFormat
    season: Optional[str] = None  # "2025/26"; null when the resort's page names no season
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None
    # The map file itself (image or PDF), shown in the trail map dialog straight from the resort's servers.
    # All five image_* fields come together or not at all; image_note is optional.
    image_url: Optional[str] = None
    image_format: Optional[ImageFormat] = None
    image_source_id: Optional[Slug] = None
    image_as_of: Optional[dt.date] = None
    image_confidence: Optional[Confidence] = None
    image_note: Optional[str] = None

    @field_validator("url", "image_url")
    @classmethod
    def _https(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        if v is None:
            return v
        u = urlparse(v)
        if u.scheme != "https" or not u.hostname:
            raise ValueError(f"{info.field_name} must be an https link")
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

    @field_validator("source_id", "image_source_id")
    @classmethod
    def _source_defined(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        return v if v is None else check_source_ref(v, info)

    @model_validator(mode="after")
    def _format_matches(self) -> "OfficialTrailmap":
        path = urlparse(self.url).path.lower()
        if self.format in EXTENSIONS and not path.endswith(EXTENSIONS[self.format]):
            raise ValueError(f"format {self.format} needs a url ending in {' or '.join(EXTENSIONS[self.format])}")
        if self.format == "page" and path.endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise ValueError("a url to a file needs format pdf, jpg or png, not page")
        given = [f for f in IMAGE_FIELDS if getattr(self, f) is not None]
        if given and len(given) != len(IMAGE_FIELDS):
            missing = [f for f in IMAGE_FIELDS if getattr(self, f) is None]
            raise ValueError(f"image fields come together: {', '.join(missing)} missing")
        if self.image_note is not None and not given:
            raise ValueError("image_note needs an image_url")
        if self.image_url is not None and not _matches_format(self.image_url, self.image_format):
            raise ValueError(f"image_format {self.image_format} needs an image_url ending in "
                             f"{' or '.join(EXTENSIONS[self.image_format])} (or a fmt={self.image_format} parameter)")
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
