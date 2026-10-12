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
from .career import (
    CERT_LEVELS, PROFESSIONALISM_COMPONENTS, Assumption, BreakdownItem, CareerBatch, CareerInputs,
    LucrativenessOut, PayBatch, PayRecord, PayRow, Professionalism, ProfessionalismInputs,
)
from .geo import Cluster, Geometry, State
from .housing import (
    DetailFile, HousingBatch, HousingOut, HousingSummary, Isochrone, RentBaseline, SourcedRent, TransitExtract,
)
from .raw import OwnershipEntry, OwnershipFile, SourcesFile, TierABatch, TierARecord
from .records import Commute, HousingSite, Report
from .resort import (
    Approval,
    Career,
    Clients,
    Editorial,
    Elevation,
    Fit,
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
    "Approval", "Career", "Clients", "Elevation", "SourcesFile", "ClimateMonth", "Cluster", "Commute", "Confidence", "Editorial",
    "Fit", "Geometry", "HousingSite", "LucrativenessOut", "CareerInputs", "CareerBatch", "PayBatch",
    "PayRow", "Professionalism", "ProfessionalismInputs", "BreakdownItem", "Assumption", "CERT_LEVELS",
    "PROFESSIONALISM_COMPONENTS", "DetailFile", "HousingBatch", "HousingOut", "HousingSummary", "Isochrone",
    "RentBaseline", "SourcedRent", "TransitExtract", "OwnershipEntry", "OwnershipFile",
    "PayRecord", "Report", "Resort", "Season", "Size", "Source", "Sourced", "SourcedMonthDay",
    "SourcedNumber", "SourcedString", "State", "StrictModel", "Terrain", "TierABatch",
    "TierACriterion", "TierARecord", "check_source_ref", "referenced_source_ids",
]
