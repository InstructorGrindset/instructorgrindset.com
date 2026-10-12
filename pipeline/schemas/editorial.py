"""Phase 6 editorial schemas and Alexandre's style lint.

Contract: docs/map/ARCHITECTURE.md, "Phase 6 editorial".
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Slug, Source, StrictModel, check_source_ref

TEXT_FIELDS = ("overview_md", "culture_md", "alexandre_take_md")

_URL = re.compile(r"https?://\S+|\]\([^)]*\)")
_DASH = re.compile(r"[—–]")                        # em dash, en dash
_HYPHEN_WORD = re.compile(r"\b(?=\w*[A-Za-z])\w+(?:-\w+)+\b")   # full-time, I-70; 2025-26 is a number range
_NEGATIVE = re.compile(r"\b(?:is|are|was|were) not\b|\b(?:isn't|aren't|wasn't|weren't)\b", re.IGNORECASE)


def lint_text(text: Optional[str]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one prose field. Dashes are errors; hyphenated words and
    negative definitions are warnings. URLs and Markdown link targets are skipped."""
    if not text:
        return [], []
    prose = _URL.sub(" ", text)
    errors = [f"em or en dash at '{_snippet(prose, m.start())}'" for m in _DASH.finditer(prose)]
    warnings = [f"hyphenated word '{m.group(0)}' (write it without a hyphen)" for m in _HYPHEN_WORD.finditer(prose)]
    warnings += [f"negative definition '{m.group(0)}' at '{_snippet(prose, m.start())}' (say what it is)"
                 for m in _NEGATIVE.finditer(prose)]
    return errors, warnings


def _snippet(text: str, i: int, width: int = 24) -> str:
    return text[max(0, i - width): i + width].replace("\n", " ").strip()


class EditorialEntry(StrictModel):
    target: Literal["cluster", "resort"]
    id: Slug
    overview_md: str = Field(min_length=1)
    culture_md: Optional[str] = None
    alexandre_take_md: Optional[str] = None
    status: Literal["draft", "reviewed"] = "draft"
    source_ids: list[Slug] = Field(min_length=1)
    reviewed_by: Optional[str] = Field(default=None, min_length=1)
    reviewed_date: Optional[dt.date] = None

    @field_validator("source_ids")
    @classmethod
    def _sources(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for sid in v:
            check_source_ref(sid, info)
        if len(set(v)) != len(v):
            raise ValueError("duplicate source_ids")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "EditorialEntry":
        if self.target == "cluster" and self.culture_md:
            raise ValueError("culture_md is for resort entries only")
        if self.status == "draft" and (self.reviewed_by or self.reviewed_date):
            raise ValueError("reviewed_by and reviewed_date belong to reviewed entries only")
        errors = [f"{f}: {e}" for f in TEXT_FIELDS for e in lint_text(getattr(self, f))[0]]
        if errors:
            raise ValueError("style: " + "; ".join(errors))
        return self

    def lint_warnings(self) -> list[str]:
        return [f"{f}: {w}" for f in TEXT_FIELDS for w in lint_text(getattr(self, f))[1]]


class EditorialBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    entries: list[EditorialEntry] = Field(default_factory=list)
