"""Phase 4 career schemas: raw pay records and career inputs, and the Resort.career output.

Contract: docs/map/ARCHITECTURE.md, "Phase 4 career". Tag vocabularies come from
config/definitions.yaml ``career.vocab`` and are enforced through the validation
context key ``career_vocab`` ({"client_profiles": [...], "enthusiast_types": [...],
"professional_types": [...]}), alongside ``source_ids``.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional, Union

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import Confidence, Slug, Source, Sourced, SourcedNumber, StrictModel, check_source_ref

CertLevel = Literal["none", "level_1", "level_2", "level_3", "trainer", "examiner", "unspecified"]
CERT_LEVELS: tuple[str, ...] = ("none", "level_1", "level_2", "level_3", "trainer", "examiner", "unspecified")
PROFESSIONALISM_COMPONENTS: tuple[str, ...] = (
    "cert_pay_differential", "paid_training_hours", "examiner_trainer_presence",
    "private_lesson_share", "year_round_staff", "clinic_access",
)
SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")


def check_season(v: str) -> str:
    """A ski season written "2025-26": the second year follows the first."""
    m = SEASON_RE.match(v)
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError(f"season must look like 2025-26, got {v!r}")
    return v


def season_start_year(season: str) -> int:
    return int(season[:4])


# ------------------------------------------------------------------ raw: pay
class PayRecord(StrictModel):
    resort_id: Slug
    season: str
    cert_level: CertLevel
    hourly_low: float = Field(ge=0)
    hourly_high: float = Field(ge=0)
    confidence: Confidence
    source_id: Slug
    verified_date: dt.date
    note: Optional[str] = None
    # Reported rows only: how many approved reports this aggregate combines (Phase 7).
    aggregate_count: Optional[int] = Field(default=None, ge=1)

    @field_validator("season")
    @classmethod
    def _season(cls, v: str) -> str:
        return check_season(v)

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)

    @model_validator(mode="after")
    def _checks(self) -> "PayRecord":
        if self.hourly_low > self.hourly_high:
            raise ValueError("hourly_low must not exceed hourly_high")
        if self.confidence == "reported" and self.aggregate_count is None:
            raise ValueError("a reported pay row must give aggregate_count (individual reports are never stored here)")
        if self.confidence != "reported" and self.aggregate_count is not None:
            raise ValueError("aggregate_count applies only to reported rows")
        return self


class PayBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    records: list[PayRecord] = Field(default_factory=list)


# --------------------------------------------------------------- raw: career
class PrivateRateValue(StrictModel):
    low: float = Field(ge=0)
    high: float = Field(ge=0)
    unit: Literal["hour", "half_day", "full_day"]

    @model_validator(mode="after")
    def _order(self) -> "PrivateRateValue":
        if self.low > self.high:
            raise ValueError("private_rate low must not exceed high")
        return self


class SourcedPrivateRate(Sourced):
    """Guest price of a private lesson."""

    value: PrivateRateValue  # type: ignore[assignment]


class SourcedPercent(SourcedNumber):
    @field_validator("value")
    @classmethod
    def _pct(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError("percent must be between 0 and 100")
        return v


class SourcedUnit(SourcedNumber):
    """A component normalised to 0..1."""

    @field_validator("value")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError("normalised component must be between 0 and 1")
        return v


class ProfessionalismInputs(StrictModel):
    cert_pay_differential: Optional[SourcedUnit] = None
    paid_training_hours: Optional[SourcedUnit] = None
    examiner_trainer_presence: Optional[SourcedUnit] = None
    private_lesson_share: Optional[SourcedUnit] = None
    year_round_staff: Optional[SourcedUnit] = None
    clinic_access: Optional[SourcedUnit] = None


def _check_tags(tags: list[str], vocab_key: str, info: ValidationInfo) -> list[str]:
    vocab = (info.context or {}).get("career_vocab")
    if vocab is not None:
        allowed = set(vocab.get(vocab_key) or [])
        unknown = [t for t in tags if t not in allowed]
        if unknown:
            raise ValueError(
                f"unknown {vocab_key} tags {unknown}; allowed: {sorted(allowed)} (config/definitions.yaml career.vocab)"
            )
    if len(set(tags)) != len(tags):
        raise ValueError("duplicate tags")
    return tags


class CareerInputs(StrictModel):
    resort_id: Slug
    private_rate: Optional[SourcedPrivateRate] = None
    private_split: Optional[SourcedPercent] = None
    professionalism_inputs: ProfessionalismInputs = Field(default_factory=ProfessionalismInputs)
    client_profile_tags: list[str] = Field(default_factory=list)
    enthusiast_types: list[str] = Field(default_factory=list)
    professional_types: list[str] = Field(default_factory=list)

    @field_validator("client_profile_tags")
    @classmethod
    def _clients(cls, v: list[str], info: ValidationInfo) -> list[str]:
        return _check_tags(v, "client_profiles", info)

    @field_validator("enthusiast_types")
    @classmethod
    def _enthusiasts(cls, v: list[str], info: ValidationInfo) -> list[str]:
        return _check_tags(v, "enthusiast_types", info)

    @field_validator("professional_types")
    @classmethod
    def _professionals(cls, v: list[str], info: ValidationInfo) -> list[str]:
        return _check_tags(v, "professional_types", info)


class CareerBatch(StrictModel):
    batch: Slug
    researched_date: dt.date
    sources: list[Source] = Field(default_factory=list)
    resorts: list[CareerInputs] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "CareerBatch":
        ids = [r.resort_id for r in self.resorts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate resort_id: {dupes}")
        return self


# ------------------------------------------------------------ build: output
class PayRow(StrictModel):
    cert_level: CertLevel
    hourly_low: float = Field(ge=0)
    hourly_high: float = Field(ge=0)
    confidence: Confidence
    source_id: Slug
    as_of: dt.date
    season: str
    stale: bool

    @field_validator("source_id")
    @classmethod
    def _source_defined(cls, v: str, info: ValidationInfo) -> str:
        return check_source_ref(v, info)


class BreakdownItem(StrictModel):
    component: Literal[PROFESSIONALISM_COMPONENTS]  # type: ignore[valid-type]
    weight: Optional[float] = None
    value: Optional[float] = None
    points: Optional[float] = None
    source_id: Optional[Slug] = None


class Professionalism(StrictModel):
    score: Optional[float] = Field(default=None, ge=0, le=100)
    status: Literal["computed", "weights_unset", "insufficient_data"]
    breakdown: list[BreakdownItem]

    @model_validator(mode="after")
    def _status(self) -> "Professionalism":
        if (self.status == "computed") != (self.score is not None):
            raise ValueError("professionalism score is set exactly when status is computed")
        return self


class Assumption(StrictModel):
    key: str = Field(min_length=1)
    value: Union[float, int, str, None] = None
    label: str = Field(min_length=1)


class LucrativenessOut(StrictModel):
    low: Optional[float] = None
    mid: Optional[float] = None
    high: Optional[float] = None
    status: Literal["computed", "assumptions_unset", "insufficient_data"]
    assumptions: list[Assumption]

    @model_validator(mode="after")
    def _status(self) -> "LucrativenessOut":
        vals = (self.low, self.mid, self.high)
        if self.status == "computed":
            if any(v is None for v in vals) or not (self.low <= self.mid <= self.high):
                raise ValueError("computed lucrativeness needs low <= mid <= high")
        elif any(v is not None for v in vals):
            raise ValueError("lucrativeness values are null unless status is computed")
        return self


class Career(StrictModel):
    pay: list[PayRow] = Field(default_factory=list)
    private_rate: Optional[SourcedPrivateRate] = None
    private_split: Optional[SourcedPercent] = None
    professionalism: Professionalism
    lucrativeness: LucrativenessOut
    stale_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_row_per_level(self) -> "Career":
        levels = [p.cert_level for p in self.pay]
        if len(set(levels)) != len(levels):
            raise ValueError("career.pay has one row per cert_level")
        return self
