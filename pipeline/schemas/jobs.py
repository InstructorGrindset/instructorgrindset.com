"""Jobs links and live postings (contract: ARCHITECTURE.md, "Webcams and jobs").

* data/raw/jobs/<batch>.yaml: employers (parent companies, resort operators, clubs, ski schools) with their careers
  page and job board vendor, and per resort links grouped by role (ski school, coaching, management, corporate, all).
* data/build/jobs/employers.json: every employer once, shared by all resorts.
* data/build/jobs/postings.json: open postings from public job board APIs (pipeline/jobs_feed.py), daily.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref
from .webcams import HttpsUrl

EmployerKind = Literal["parent_company", "resort_operator", "club", "ski_school", "other"]
AtsVendor = Literal["greenhouse", "lever", "smartrecruiters", "workable", "ashby", "workday", "icims", "ultipro",
                    "paylocity", "adp", "paycom", "jazzhr", "bamboohr", "other", "unknown"]
ATS_VENDORS: tuple[str, ...] = AtsVendor.__args__  # type: ignore[attr-defined]
# Vendors with a public, documented postings API that pipeline/jobs_feed.py reads. Every other vendor (Workday,
# iCIMS, UKG and the rest) gets links only: their pages are never scraped.
FEED_VENDORS: tuple[str, ...] = ("greenhouse", "lever", "smartrecruiters", "workable", "ashby")
RoleGroup = Literal["ski_school", "coaching", "management", "corporate", "all"]
ROLE_GROUPS: tuple[str, ...] = RoleGroup.__args__  # type: ignore[attr-defined]
Discipline = Literal["race", "freeride", "freestyle", "park", "nordic", "adaptive", "snowboard", "moguls"]
DISCIPLINES: tuple[str, ...] = Discipline.__args__  # type: ignore[attr-defined]
BOARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class _Cited(StrictModel):
    """source_id, as_of, confidence and note, declared last in each subclass so they print last."""

    @field_validator("source_id", check_fields=False)
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class Ats(StrictModel):
    """The employer's applicant tracking system. ``board`` is the public board token (Greenhouse board, Lever site,
    SmartRecruiters company, Workable account, Ashby job board name); set only when the vendor publishes one."""

    vendor: AtsVendor = "unknown"
    board: Optional[str] = None

    @field_validator("board")
    @classmethod
    def _board(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not BOARD_RE.match(v):
            raise ValueError("board must be a plain token (letters, digits, dot, dash, underscore)")
        return v

    @property
    def feed(self) -> bool:
        return self.vendor in FEED_VENDORS and self.board is not None


class Employer(_Cited):
    id: Slug
    name: str = Field(min_length=1, max_length=120)
    kind: EmployerKind
    careers_url: HttpsUrl
    ats: Ats = Field(default_factory=Ats)
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None


class JobLink(_Cited):
    """One link as it appears in the raw file and in Resort.jobs.links."""

    employer_id: Slug
    role_group: RoleGroup
    disciplines: list[Discipline] = Field(default_factory=list)
    label: str = Field(min_length=1, max_length=120)
    url: HttpsUrl
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("disciplines")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        dup = sorted({d for d in v if v.count(d) > 1})
        if dup:
            raise ValueError(f"discipline listed twice: {', '.join(dup)}")
        return v


class JobsRecord(StrictModel):
    resort_id: Slug
    links: list[JobLink] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "JobsRecord":
        # One board may serve several groups (coaching jobs on the general board), so a duplicate is the same url
        # twice within one role group.
        keys = [(x.role_group, x.url) for x in self.links]
        dup = sorted({f"{g} {u}" for g, u in keys if keys.count((g, u)) > 1})
        if dup:
            raise ValueError(f"url listed twice in one role group: {', '.join(dup)}")
        return self


class HiringWindow(_Cited):
    """When applications open, job fairs or when clinics start, in the source's own words (ROADMAP item 6)."""

    text: str = Field(min_length=1, max_length=400)
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        if "\u2014" in v or "\u2013" in v:
            raise ValueError("text: no em or en dashes (Alexandre's style)")
        return " ".join(v.split())


class HiringWindowRecord(HiringWindow):
    resort_id: Slug


YEAR_MONTH = r"^20\d\d-(0[1-9]|1[0-2])$"


class HiringWindowOut(HiringWindow):
    """Resort.jobs.hiring_window: the raw window plus the months pipeline/hiring.py reads from its text (both null
    when the text is not explicit about when hiring opens and when it closes)."""

    start_month: Optional[str] = Field(default=None, pattern=YEAR_MONTH)
    end_month: Optional[str] = Field(default=None, pattern=YEAR_MONTH)

    @model_validator(mode="after")
    def _months(self) -> "HiringWindowOut":
        if (self.start_month is None) != (self.end_month is None):
            raise ValueError("start_month and end_month are set together or not at all")
        if self.start_month and self.end_month < self.start_month:
            raise ValueError("end_month is before start_month")
        return self


class JobsBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    employers: list[Employer] = Field(default_factory=list)
    records: list[JobsRecord] = Field(default_factory=list)
    hiring_windows: list[HiringWindowRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "JobsBatch":
        for what, ids in (("employer ids", [e.id for e in self.employers]),
                          ("resort ids", [r.resort_id for r in self.records]),
                          ("hiring window resort ids", [h.resort_id for h in self.hiring_windows])):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                raise ValueError(f"{what} listed twice: {', '.join(dup)}")
        return self


class Jobs(StrictModel):
    """Resort.jobs in resorts.json. ``feed`` is true when one of the linked employers has a public board that
    pipeline/jobs_feed.py reads, so the panel loads jobs/postings.json only then. ``hiring_window`` is the source's
    own sentence on when hiring or training happens, with the months it names."""

    links: list[JobLink] = Field(default_factory=list)
    feed: bool = False
    hiring_window: Optional[HiringWindowOut] = None

    @model_validator(mode="after")
    def _something(self) -> "Jobs":
        if not self.links and self.hiring_window is None:
            raise ValueError("jobs needs at least one link or a hiring window")
        return self


# ---------------------------------------------------------------- build output: jobs/employers.json

class EmployerOut(Employer):
    feed: bool = False                 # vendor has a public postings API and a board is set
    board_url: Optional[str] = None    # the public board page (the postings source), when feed is true


class EmployersFile(StrictModel):
    version: int
    generated_at: str
    employers: list[EmployerOut] = Field(default_factory=list)


# ---------------------------------------------------------------- build output: jobs/postings.json

class Board(StrictModel):
    employer_id: Slug
    employer_name: str = Field(min_length=1)
    vendor: AtsVendor
    board: str
    url: HttpsUrl                       # public board page; the source of its postings
    source_id: Slug
    status: Literal["ok", "error"]
    fetched_at: Optional[str] = None    # last successful fetch (ISO datetime, UTC)
    as_of: Optional[dt.date] = None     # date of fetched_at
    total: int = Field(default=0, ge=0)  # open postings on the board at the last successful fetch
    matched: int = Field(default=0, ge=0)  # postings kept by the keywords
    error: Optional[str] = None


class Posting(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    employer_id: Slug
    title: str = Field(min_length=1, max_length=300)
    location: Optional[str] = Field(default=None, max_length=300)
    department: Optional[str] = Field(default=None, max_length=300)
    url: HttpsUrl
    role_group: RoleGroup                # a guess from the title (config jobs.feed.role_keywords)
    resort_ids: list[Slug] = Field(default_factory=list)   # empty: an employer wide role (no resort named)
    source_id: Slug
    as_of: dt.date
    confidence: Literal["published"] = "published"
    fetched_at: str


class PostingsFile(StrictModel):
    version: int
    generated_at: str
    fetched_at: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    boards: list[Board] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    postings: list[Posting] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs(self) -> "PostingsFile":
        src = {s.id for s in self.sources}
        boards = {b.employer_id for b in self.boards}
        for b in self.boards:
            if b.source_id not in src:
                raise ValueError(f"board {b.employer_id}: source_id '{b.source_id}' is not in sources")
        for p in self.postings:
            if p.employer_id not in boards:
                raise ValueError(f"posting {p.id}: employer '{p.employer_id}' has no board")
            if p.source_id not in src:
                raise ValueError(f"posting {p.id}: source_id '{p.source_id}' is not in sources")
        return self
