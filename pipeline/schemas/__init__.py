"""Pydantic models for every Career Map entity (docs/map/ARCHITECTURE.md)."""
from .common import (
    Confidence,
    Source,
    Sourced,
    SourcedMonthDay,
    SourcedNumber,
    SourcedString,
    StrictModel,
    check_source_ref,
)
from .geo import Cluster, Geometry, State
from .raw import OwnershipEntry, OwnershipFile, TierABatch, TierARecord
from .records import Commute, HousingSite, PayRecord, Report
from .resort import (
    Career,
    Clients,
    Editorial,
    Elevation,
    Fit,
    Lucrativeness,
    Resort,
    Season,
    Size,
    Terrain,
    TierACriterion,
    referenced_source_ids,
)

try:  # Owned by the climate agent (Phase 3); optional here.
    from .climate import ClimateMonth  # noqa: F401
except ImportError:  # pragma: no cover - depends on the climate module existing
    ClimateMonth = None  # type: ignore[assignment]

__all__ = [
    "Career", "Clients", "Elevation", "ClimateMonth", "Cluster", "Commute", "Confidence", "Editorial",
    "Fit", "Geometry", "HousingSite", "Lucrativeness", "OwnershipEntry", "OwnershipFile",
    "PayRecord", "Report", "Resort", "Season", "Size", "Source", "Sourced", "SourcedMonthDay",
    "SourcedNumber", "SourcedString", "State", "StrictModel", "Terrain", "TierABatch",
    "TierACriterion", "TierARecord", "check_source_ref", "referenced_source_ids",
]
