"""Live webcam links: data/raw/webcams/<batch>.yaml (contract: ARCHITECTURE.md, "Webcams and jobs").

Links only: the cams are the resorts' (or their camera vendors') pages, images and streams. The site links to them
in a new tab and never embeds, proxies or rehosts them."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Optional
from urllib.parse import urlparse

from pydantic import AfterValidator, Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref

WebcamKind = Literal["page", "image", "stream"]


def https_url(v: str) -> str:
    """Raise unless ``v`` is an https link with a host name."""
    u = urlparse(v)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("url must be an https link")
    return v


HttpsUrl = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(https_url)]


class CamsPage(StrictModel):
    """The resort's own page that lists its cams."""

    url: HttpsUrl
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class WebcamIn(StrictModel):
    """One named cam in a raw file. as_of and confidence default to the record's cams_page."""

    label: str = Field(min_length=1, max_length=80)
    url: HttpsUrl
    kind: WebcamKind
    source_id: Slug
    as_of: Optional[dt.date] = None
    confidence: Optional[Confidence] = None
    note: Optional[str] = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class Webcam(StrictModel):
    """One named cam as it reaches resorts.json (as_of and confidence always set)."""

    label: str = Field(min_length=1)
    url: HttpsUrl
    kind: WebcamKind
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class Webcams(StrictModel):
    """Resort.webcams in resorts.json: the cams page and up to webcams.max_named_cams named cams."""

    cams_page: Optional[CamsPage] = None
    cams: list[Webcam] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something(self) -> "Webcams":
        if self.cams_page is None and not self.cams:
            raise ValueError("webcams needs a cams_page or at least one cam")
        return self


class WebcamRecord(StrictModel):
    resort_id: Slug
    cams_page: Optional[CamsPage] = None
    cams: list[WebcamIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> "WebcamRecord":
        if self.cams_page is None and not self.cams:
            raise ValueError("a webcam record needs a cams_page or at least one cam")
        urls = [c.url for c in self.cams] + ([self.cams_page.url] if self.cams_page else [])
        dup = sorted({u for u in urls if urls.count(u) > 1})
        if dup:
            raise ValueError(f"url listed twice: {', '.join(dup)}")
        labels = [c.label.lower() for c in self.cams]
        dup = sorted({x for x in labels if labels.count(x) > 1})
        if dup:
            raise ValueError(f"cam label listed twice: {', '.join(dup)}")
        if self.cams_page is None:
            bare = [c.label for c in self.cams if c.as_of is None or c.confidence is None]
            if bare:
                raise ValueError(f"cams need as_of and confidence when the record has no cams_page: {', '.join(bare)}")
        return self

    def output(self, max_cams: int) -> Webcams:
        """The record as Resort.webcams: cams inherit the cams page date and confidence, first max_cams kept."""
        page = self.cams_page
        cams = [Webcam(label=c.label, url=c.url, kind=c.kind, source_id=c.source_id,
                       as_of=c.as_of or page.as_of, confidence=c.confidence or page.confidence, note=c.note)
                for c in self.cams[:max_cams]]
        return Webcams(cams_page=page, cams=cams)


class WebcamBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    records: list[WebcamRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "WebcamBatch":
        ids = [r.resort_id for r in self.records]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"resort ids listed twice: {', '.join(dup)}")
        return self
