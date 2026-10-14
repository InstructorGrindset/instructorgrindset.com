"""Shared building blocks: strict base model, identifiers, Source, Sourced.

Source reference checking
-------------------------
A ``Sourced`` value (and every other model field that names a ``source_id``)
must point at a defined :class:`Source`. Pass the set of known source ids as
validation context to enforce this::

    Resort.model_validate(data, context={"source_ids": {"wikipedia-vail"}})

Without that context only the shape of the id is checked; the build always
passes the context (see ``pipeline/sources.py``).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
SLUG_RE = re.compile(SLUG_PATTERN)
STATE_PATTERN = r"^[A-Z]{2}$"

Slug = Annotated[str, Field(pattern=SLUG_PATTERN)]
StateCode = Annotated[str, Field(pattern=STATE_PATTERN)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]

Confidence = Literal["published", "reported", "estimated"]


class StrictModel(BaseModel):
    """Base for every schema: unknown keys are an error."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


def check_source_ref(source_id: str, info: ValidationInfo) -> str:
    """Raise when ``source_id`` is not in the ``source_ids`` validation context."""
    ctx = info.context or {}
    known = ctx.get("source_ids")
    if known is not None and source_id not in known:
        raise ValueError(f"source_id '{source_id}' is not defined in any sources list")
    return source_id


class Source(StrictModel):
    id: Slug
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    accessed_date: dt.date
    note: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v


SourcedValue = Union[bool, int, float, str]


class Sourced(StrictModel):
    """A displayed number or fact with its provenance."""

    value: SourcedValue
    source_id: Slug
    as_of: dt.date
    confidence: Confidence
    note: Optional[str] = None

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class SourcedNumber(Sourced):
    value: Union[int, float]

    @field_validator("value", mode="before")
    @classmethod
    def _no_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("expected a number")
        return v


class SourcedString(Sourced):
    value: str = Field(min_length=1)


MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")


class SourcedMonthDay(Sourced):
    """A typical calendar date without a year, as ``MM-DD``."""

    value: str

    @field_validator("value", mode="before")
    @classmethod
    def _mmdd(cls, v: Any) -> Any:
        if isinstance(v, (dt.date,)):
            raise ValueError("expected MM-DD without a year")
        if not isinstance(v, str) or not MMDD_RE.match(v):
            raise ValueError("expected a date as MM-DD")
        return v
