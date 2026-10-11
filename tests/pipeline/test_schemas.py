import copy

import pytest
from pydantic import ValidationError

from pipeline.schemas import (
    Cluster, Commute, HousingSite, OwnershipFile, PayRecord, Report, Resort, Source, Sourced,
    SourcedMonthDay, State, TierABatch,
)

CTX = {"source_ids": {"src-a", "src-b"}}
SV = {"value": 100, "source_id": "src-a", "as_of": "2026-10-01", "confidence": "published"}

RESORT = {
    "id": "fixture-peak", "name": "Fixture Peak", "tier": "A",
    "tier_a_criteria": [{"criterion": "luxury", "rationale": "r", "source_id": "src-a"}],
    "tier_a_status": "draft", "owner": None, "lat": 39.6, "lon": -106.3,
    "location_source_id": "src-b", "cluster_id": "i70-corridor", "state": "CO",
    "size": {"acres": SV}, "sources": ["src-a", "src-b"],
}


def resort(**over):
    d = copy.deepcopy(RESORT)
    d.update(over)
    return d


def test_valid_resort():
    r = Resort.model_validate(resort(), context=CTX)
    assert r.size.acres.value == 100 and r.career is None and r.climate_available is False


def test_sourced_missing_source_id_fails():
    with pytest.raises(ValidationError, match="source_id"):
        Sourced.model_validate({"value": 1, "as_of": "2026-10-01", "confidence": "published"})


def test_sourced_undefined_source_fails():
    bad = dict(SV, source_id="nowhere")
    with pytest.raises(ValidationError, match="not defined"):
        Resort.model_validate(resort(size={"acres": bad}, sources=["src-a", "src-b", "nowhere"]), context=CTX)


def test_criterion_undefined_source_fails():
    crit = [{"criterion": "luxury", "rationale": "r", "source_id": "nowhere"}]
    with pytest.raises(ValidationError, match="not defined"):
        Resort.model_validate(resort(tier_a_criteria=crit), context=CTX)


def test_criterion_missing_source_fails():
    crit = [{"criterion": "luxury", "rationale": "r"}]
    with pytest.raises(ValidationError, match="source_id"):
        Resort.model_validate(resort(tier_a_criteria=crit), context=CTX)


def test_tier_a_without_criteria_fails():
    with pytest.raises(ValidationError, match="at least one"):
        Resort.model_validate(resort(tier_a_criteria=[]), context=CTX)


def test_tier_a_needs_status_and_tier_b_must_not_have_one():
    with pytest.raises(ValidationError, match="tier_a_status"):
        Resort.model_validate(resort(tier_a_status=None), context=CTX)
    with pytest.raises(ValidationError, match="null for Tier B"):
        Resort.model_validate(resort(tier="B", tier_a_criteria=[]), context=CTX)
    Resort.model_validate(resort(tier="B", tier_a_criteria=[], tier_a_status=None), context=CTX)


@pytest.mark.parametrize("missing", ["lat", "lon"])
def test_missing_coordinates_fail(missing):
    d = resort()
    del d[missing]
    with pytest.raises(ValidationError, match=missing):
        Resort.model_validate(d, context=CTX)


def test_null_coordinates_fail():
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(lat=None), context=CTX)


def test_out_of_range_coordinates_fail():
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(lat=95), context=CTX)


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError, match="Extra inputs"):
        Resort.model_validate(resort(surprise=1), context=CTX)
    with pytest.raises(ValidationError, match="Extra inputs"):
        Sourced.model_validate(dict(SV, extra=1))


def test_sources_must_list_every_reference():
    with pytest.raises(ValidationError, match="missing referenced"):
        Resort.model_validate(resort(sources=["src-a"]), context=CTX)


def test_bad_confidence_and_slug():
    with pytest.raises(ValidationError):
        Sourced.model_validate(dict(SV, confidence="guess"))
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(id="Fixture Peak"), context=CTX)


def test_month_day():
    SourcedMonthDay.model_validate(dict(SV, value="11-20"))
    for bad in ("2026-11-20", "13-01", "11/20", 1120):
        with pytest.raises(ValidationError):
            SourcedMonthDay.model_validate(dict(SV, value=bad))


def test_numbers_reject_strings():
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(size={"acres": dict(SV, value="lots")}), context=CTX)


def test_draft_editorial_cannot_be_emitted():
    with pytest.raises(ValidationError, match="reviewed"):
        Resort.model_validate(resort(editorial={"overview_md": "x", "status": "draft"}), context=CTX)


def test_source_url_required():
    Source.model_validate({"id": "a", "url": "https://x.org", "title": "t", "accessed_date": "2026-10-01"})
    with pytest.raises(ValidationError):
        Source.model_validate({"id": "a", "url": "x.org", "title": "t", "accessed_date": "2026-10-01"})


def test_without_context_only_shape_is_checked():
    Resort.model_validate(resort(size={"acres": dict(SV, source_id="nowhere")}, sources=["src-a", "src-b", "nowhere"]))


def test_tier_a_batch_rules():
    rec = {k: v for k, v in RESORT.items() if k not in ("tier", "tier_a_status", "cluster_id", "sources")}
    rec["cluster_hint"] = "i70-corridor"
    batch = {"batch": "b", "researched_date": "2026-10-01", "sources": [], "resorts": [rec]}
    TierABatch.model_validate(batch, context=CTX)
    with pytest.raises(ValidationError, match="at least 1"):
        TierABatch.model_validate({**batch, "resorts": [{**rec, "tier_a_criteria": []}]}, context=CTX)
    no_lat = {k: v for k, v in rec.items() if k != "lat"}
    with pytest.raises(ValidationError, match="lat"):
        TierABatch.model_validate({**batch, "resorts": [no_lat]}, context=CTX)
    with pytest.raises(ValidationError, match="duplicate resort id"):
        TierABatch.model_validate({**batch, "resorts": [rec, rec]}, context=CTX)
    with pytest.raises(ValidationError):
        TierABatch.model_validate({**batch, "status": "reviewed"}, context=CTX)


def test_ownership_entry_history_fields_optional():
    f = {"operator": "Op", "verified_date": "2026-10-01",
         "sources": [{"id": "src-a", "url": "https://x.org", "title": "t", "accessed_date": "2026-10-01"}],
         "resorts": [{"name": "Hill", "state": "PA", "source_id": "src-a"},
                     {"name": "Peak", "state": "CO", "source_id": "src-a", "resort_id": "peak", "tier_a_candidate": False}]}
    OwnershipFile.model_validate(f, context=CTX)
    f["resorts"][0]["source_id"] = "nowhere"
    with pytest.raises(ValidationError, match="not defined"):
        OwnershipFile.model_validate(f, context=CTX)


def test_other_entities():
    State.model_validate({"code": "CO", "name": "Colorado", "cluster_ids": ["i70-corridor"], "resort_count": 2})
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    Cluster.model_validate({"id": "c", "name": "C", "states": ["CO"], "polygon": {"type": "Polygon", "coordinates": [ring]},
                            "resort_ids": ["a"], "tier_a_count": 0}, context=CTX)
    with pytest.raises(ValidationError, match="closed"):
        Cluster.model_validate({"id": "c", "name": "C", "states": ["CO"], "polygon": {"type": "Polygon", "coordinates": [ring[:-1] + [[2, 2]]]},
                                "resort_ids": ["a"], "tier_a_count": 0})
    with pytest.raises(ValidationError, match="not defined"):
        HousingSite.model_validate({"id": "h", "resort_id": "r", "name": "H", "type": "market", "lat": 1, "lon": 1,
                                    "precision": "neighborhood", "confidence": "estimated", "verified_date": "2026-10-01",
                                    "sources": ["nowhere"]}, context=CTX)
    Commute.model_validate({"housing_id": "h", "resort_id": "r", "mode": "bus", "minutes": 20, "distance_mi": 8})
    with pytest.raises(ValidationError, match="hourly_low"):
        PayRecord.model_validate({"resort_id": "r", "season": "2026-27", "cert_level": "L2", "hourly_low": 30,
                                  "hourly_high": 20, "confidence": "published", "source": "src-a", "verified_date": "2026-10-01"},
                                 context=CTX)
    with pytest.raises(ValidationError):
        Report.model_validate({"id": "1", "resort_id": "r", "kind": "tips", "season": "2026-27", "values": {}})


def test_elevation_and_source_note():
    r = Resort.model_validate(resort(elevation={"base_ft": dict(SV, value=8000), "summit_ft": dict(SV, value=11000)}), context=CTX)
    assert r.elevation.base_ft.value == 8000
    assert Resort.model_validate(resort(), context=CTX).elevation.summit_ft is None
    with pytest.raises(ValidationError, match="base_ft must not exceed"):
        Resort.model_validate(resort(elevation={"base_ft": dict(SV, value=9000), "summit_ft": dict(SV, value=8000)}), context=CTX)
    with pytest.raises(ValidationError, match="missing referenced"):
        Resort.model_validate(resort(elevation={"base_ft": dict(SV, source_id="src-c")}), context={"source_ids": {"src-a", "src-b", "src-c"}})
    s = Source.model_validate({"id": "a", "url": "https://x.org", "title": "t", "accessed_date": "2026-10-01", "note": "caveat"})
    assert s.note == "caveat"


def test_new_criteria_and_facility_type():
    crit = [{"criterion": "operator_compensation", "rationale": "r", "source_id": "src-a"},
            {"criterion": "year_round_training", "rationale": "r", "source_id": "src-a"}]
    r = Resort.model_validate(resort(tier_a_criteria=crit, facility_type="indoor"), context=CTX)
    assert r.facility_type == "indoor"
    assert Resort.model_validate(resort(), context=CTX).facility_type == "ski_area"
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(facility_type="water_ramp"), context=CTX)
    with pytest.raises(ValidationError, match="ski_area"):
        Resort.model_validate(resort(tier="B", tier_a_criteria=[], tier_a_status=None, facility_type="indoor"), context=CTX)
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(tier_a_criteria=[{"criterion": "vibes", "rationale": "r", "source_id": "src-a"}]),
                              context=CTX)


def test_approval_rules():
    ok = {"by": "Alexandre", "date": "2026-10-11", "note": None}
    r = Resort.model_validate(resort(tier_a_status="approved", approval=ok), context=CTX)
    assert r.approval.by == "Alexandre" and str(r.approval.date) == "2026-10-11"
    with pytest.raises(ValidationError, match="needs an approval block"):
        Resort.model_validate(resort(tier_a_status="approved"), context=CTX)
    with pytest.raises(ValidationError, match="approval is set"):
        Resort.model_validate(resort(approval=ok), context=CTX)
    with pytest.raises(ValidationError):
        Resort.model_validate(resort(tier_a_status="approved", approval={"by": "", "date": "2026-10-11"}), context=CTX)
    with pytest.raises(ValidationError, match="Tier B"):
        Resort.model_validate(resort(tier="B", tier_a_criteria=[], tier_a_status=None, approval=ok), context=CTX)


def test_batch_record_status_override():
    rec = {k: v for k, v in RESORT.items() if k not in ("tier", "tier_a_status", "cluster_id", "sources")}
    rec["cluster_hint"] = "i70-corridor"
    ok = {"by": "Alexandre", "date": "2026-10-11"}
    batch = {"batch": "b", "researched_date": "2026-10-01", "status": "draft", "resorts": [rec]}
    b = TierABatch.model_validate({**batch, "resorts": [{**rec, "tier_a_status": "approved", "approval": ok}]}, context=CTX)
    assert b.status_of(b.resorts[0]) == "approved"
    with pytest.raises(ValidationError, match="resort 'fixture-peak': an approved Tier A record needs an approval"):
        TierABatch.model_validate({**batch, "resorts": [{**rec, "tier_a_status": "approved"}]}, context=CTX)
    with pytest.raises(ValidationError, match="needs an approval"):
        TierABatch.model_validate({**batch, "status": "approved"}, context=CTX)
    b = TierABatch.model_validate({**batch, "status": "approved", "resorts": [{**rec, "tier_a_status": "draft"}]}, context=CTX)
    assert b.status_of(b.resorts[0]) == "draft"
