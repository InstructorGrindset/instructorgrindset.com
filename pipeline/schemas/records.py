"""Report (Phase 7), plus re-exports of the Section 6 entities defined with their phase.

HousingSite and Commute live in housing.py (Phase 5), PayRecord in career.py (Phase 4).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .career import PayRecord  # noqa: F401
from .common import Slug, StrictModel
from .housing import CommuteOut as Commute  # noqa: F401
from .housing import HousingSite  # noqa: F401


class Report(StrictModel):
    """Raw crowdsourced input. Never rendered directly; only aggregates of 3+."""

    id: str = Field(min_length=1)
    resort_id: Slug
    kind: Literal["pay", "rent", "housing"]
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    values: dict[str, Any]
    status: Literal["pending", "approved", "rejected"] = "pending"
