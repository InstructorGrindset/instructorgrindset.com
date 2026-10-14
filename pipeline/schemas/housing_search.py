"""Housing search links: data/raw/housing_search/<batch>.yaml (contract: ARCHITECTURE.md, "Housing search").

Links only. Furnished Finder listings are linked by town and never copied, scraped or embedded. Housing search tools
(employer portals, housing authorities, listing sites, Facebook groups and more) are the ones the cited sources name
for an area; the build ranks them by how many independent sources name each one."""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref
from .webcams import HttpsUrl

ToolKind = Literal["employer_portal", "housing_authority", "listing_site", "facebook_group", "craigslist",
                   "property_manager", "app", "other"]
ToolScope = Literal["cluster", "resort"]
TOOL_KINDS = ToolKind.__args__  # type: ignore[attr-defined]


def _plain_text(v: str, what: str) -> str:
    """One line of public text: spaces collapsed, no em or en dashes (Alexandre's style)."""
    v = " ".join(v.split())
    if "—" in v or "–" in v:
        raise ValueError(f"{what}: no em or en dashes (Alexandre's style)")
    return v


class FurnishedFinderIn(StrictModel):
    """The Furnished Finder search page for the town nearest a resort."""

    resort_id: Slug
    town: str = Field(min_length=1, max_length=80)
    url: HttpsUrl
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("town")
    @classmethod
    def _town(cls, v: str) -> str:
        return _plain_text(v, "town")

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class HousingToolIn(StrictModel):
    """One housing search tool named by the sources for a cluster or a single resort."""

    scope: ToolScope
    scope_id: Slug
    name: str = Field(min_length=1, max_length=100)
    kind: ToolKind
    url: HttpsUrl
    why: str = Field(min_length=1, max_length=400)   # the sources' reason, one public sentence
    source_ids: list[Slug] = Field(min_length=1)
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _plain_text(v, "name")

    @field_validator("why")
    @classmethod
    def _why(cls, v: str) -> str:
        return _plain_text(v, "why")

    @field_validator("source_ids")
    @classmethod
    def _sources(cls, v: list[str], info: ValidationInfo) -> list[str]:
        dup = sorted({s for s in v if v.count(s) > 1})
        if dup:
            raise ValueError(f"source id listed twice: {', '.join(dup)}")
        for sid in v:
            check_source_ref(sid, info)
        return v


class HousingSearchBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    furnished_finder: list[FurnishedFinderIn] = Field(default_factory=list)
    tools: list[HousingToolIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "HousingSearchBatch":
        ids = [f.resort_id for f in self.furnished_finder]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"furnished_finder lists a resort twice: {', '.join(dup)}")
        keys = [(t.scope, t.scope_id, t.url) for t in self.tools]
        dup = sorted({f"{k[0]} {k[1]}: {k[2]}" for k in keys if keys.count(k) > 1})
        if dup:
            raise ValueError(f"tool url listed twice for one scope: {'; '.join(dup)}")
        return self


# ---------------------------------------------------------------- build output (Resort.housing_search)
class FurnishedFinderOut(StrictModel):
    town: str = Field(min_length=1)
    url: HttpsUrl
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class HousingToolOut(StrictModel):
    name: str = Field(min_length=1)
    kind: ToolKind
    url: HttpsUrl
    why: str = Field(min_length=1)
    scope: ToolScope
    named_by: int = Field(ge=1)   # independent sources: distinct registered domains among source_ids
    source_ids: list[Slug] = Field(min_length=1)
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_ids")
    @classmethod
    def _sources(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        return v


class HousingSearch(StrictModel):
    """Resort.housing_search in resorts.json: the Furnished Finder link and the ranked tools."""

    furnished_finder: Optional[FurnishedFinderOut] = None
    tools: list[HousingToolOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something(self) -> "HousingSearch":
        if self.furnished_finder is None and not self.tools:
            raise ValueError("housing_search needs a furnished_finder link or at least one tool")
        return self
