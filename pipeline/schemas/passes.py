"""Multi resort pass membership: data/raw/passes/<season>.yaml (contract: ARCHITECTURE.md, "Pass membership").

One record says that a resort is on a pass family (Epic, Ikon, Mountain Collective, Indy) for a season, with the
access level of that family's top pass where the pass company states it. Membership comes from the pass companies'
own sites and is never inferred from who owns a resort."""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_serializer, model_validator

from .common import Confidence, Slug, Source, StrictModel, check_source_ref

PassId = Literal["epic", "ikon", "mountain_collective", "indy"]
PassAccess = Literal["unlimited", "limited", "unspecified"]
PASS_ORDER: tuple[str, ...] = ("epic", "ikon", "mountain_collective", "indy")
SEASON_RE = re.compile(r"^(20\d\d)/(\d\d)$")


def check_season(v: str) -> str:
    """Raise unless ``v`` is a ski season such as "2026/27" (two consecutive years)."""
    m = SEASON_RE.match(v or "")
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError(f"season must look like 2026/27 (consecutive years), got {v!r}")
    return v


class PassMembership(StrictModel):
    """One resort on one pass for one season, as it reaches resorts.json (Resort.passes[])."""

    model_config = ConfigDict(extra="forbid", validate_default=True, populate_by_name=True)

    pass_id: PassId = Field(alias="pass")
    access: PassAccess
    days: Optional[int] = Field(default=None, ge=1, le=366)   # only with access limited, when the page states it
    blackout: Optional[bool] = None                          # set only when the page says whether blackout dates apply
    season: str
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("season")
    @classmethod
    def _season(cls, v: str) -> str:
        return check_season(v)

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @field_validator("note")
    @classmethod
    def _note(cls, v: Optional[str]) -> Optional[str]:
        return " ".join(v.split()) if v and v.strip() else None

    @model_validator(mode="after")
    def _days(self) -> "PassMembership":
        if self.days is not None and self.access != "limited":
            raise ValueError(f"days is set but access is {self.access}; days goes only with access limited")
        return self

    @model_serializer(mode="wrap")
    def _pass_key(self, handler):
        """Dump the pass id under its contract key "pass" (a Python keyword, hence the field name pass_id)."""
        d = handler(self)
        if isinstance(d, dict) and "pass_id" in d:
            d = {"pass": d.pop("pass_id"), **d}
        return d

    def output(self) -> dict:
        """The record in resorts.json, with the key "pass"."""
        return self.model_dump(mode="json")


class PassMembershipIn(PassMembership):
    """One raw record: the output fields plus the resort it describes."""

    resort_id: Slug

    def membership(self) -> PassMembership:
        return PassMembership.model_validate(self.model_dump(exclude={"resort_id"}))


class PassBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    memberships: list[PassMembershipIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "PassBatch":
        keys = [(m.resort_id, m.pass_id, m.season) for m in self.memberships]
        dup = sorted({f"{r} on {p} for {s}" for r, p, s in keys if keys.count((r, p, s)) > 1})
        if dup:
            raise ValueError(f"membership listed twice: {', '.join(dup)}")
        return self
