"""Generate SYNTHETIC Career Map fixtures for frontend development and browser tests.

Everything this script writes is invented test data. It lives only in
tests/map/fixtures/ and must never be copied into data/build. Resort names
are fictional and every source points at example.com.

State outlines are the one real input: a subset of data/build/us-states.geojson
(us-atlas, US Census Bureau) so the map looks like the production map.

    python tests/map/make_fixtures.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "map" / "fixtures"
GENERATED_AT = "2026-10-11T12:00:00Z"
AS_OF = "2026-10-01"
STATE_CODES = ["CA", "NV", "UT", "CO", "WY", "ID", "VT", "NH"]
STATE_NAMES = {"CA": "California", "NV": "Nevada", "UT": "Utah", "CO": "Colorado", "VT": "Vermont"}
SEASON = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]

SOURCES = [
    {"id": "fixture-resort-site", "url": "https://example.com/fixture/resort-site",
     "title": "Synthetic fixture: resort facts", "accessed_date": AS_OF},
    {"id": "fixture-survey", "url": "https://example.com/fixture/survey",
     "title": "Synthetic fixture: aggregated reports", "accessed_date": AS_OF},
    {"id": "fixture-model", "url": "https://example.com/fixture/model",
     "title": "Synthetic fixture: estimate", "accessed_date": AS_OF, "note": "synthetic source note"},
    {"id": "fixture-location", "url": "https://example.com/fixture/location",
     "title": "Synthetic fixture: base area location", "accessed_date": AS_OF},
    {"id": "fixture-climate", "url": "https://example.com/fixture/climate",
     "title": "Synthetic fixture: climatology", "accessed_date": AS_OF},
    {"id": "fixture-snotel", "url": "https://example.com/fixture/snotel",
     "title": "Synthetic fixture: station density", "accessed_date": AS_OF},
    {"id": "fixture-ownership", "url": "https://example.com/fixture/ownership",
     "title": "Synthetic fixture: ownership", "accessed_date": AS_OF},
]

# (id, name, cluster, state, tier, tier_a_status, lat, lon, acres, vertical, lifts, climate, density_source)
RESORTS = [
    ("alpha-peak", "Alpha Peak", "fixture-front-range", "CO", "A", "approved", 39.64, -106.37, 5300, 3450, 31, True, "snotel"),
    ("bravo-bowl", "Bravo Bowl", "fixture-front-range", "CO", "A", "draft", 39.48, -106.07, 2900, 2600, 20, True, "estimated"),
    ("charlie-hill", "Charlie Hill", "fixture-front-range", "CO", "B", None, 39.68, -105.90, 700, 1500, 7, True, "estimated"),
    ("delta-ridge", "Delta Ridge", "fixture-front-range", "CO", "B", None, 39.58, -105.80, None, None, None, False, None),
    ("echo-mesa", "Echo Mesa", "fixture-high-desert", "CO", "B", None, 37.47, -107.81, 1200, 2000, 10, True, "estimated"),
    ("foxtrot-basin", "Foxtrot Basin", "fixture-wasatch-like", "UT", "A", "approved", 40.59, -111.64, 2600, 2900, 12, True, "snotel"),
    ("golf-canyon", "Golf Canyon", "fixture-wasatch-like", "UT", "B", None, 40.62, -111.59, 1000, 1700, 6, False, None),
    ("hotel-lake", "Hotel Lake", "fixture-lake-crest", "CA", "A", "draft", 39.20, -120.24, 6000, 2850, 34, True, "snotel"),
    ("india-crest", "India Crest", "fixture-lake-crest", "CA", "B", None, 39.10, -120.15, 1500, 1800, 11, True, "estimated"),
    ("juliet-summit", "Juliet Summit", "fixture-lake-crest", "NV", "B", None, 39.32, -119.88, 1650, 1800, 7, True, "estimated"),
    ("kilo-notch", "Kilo Notch", "fixture-green-ridge", "VT", "A", "approved", 44.53, -72.78, 485, 2360, 13, True, "estimated"),
    ("lima-hollow", "Lima Hollow", "fixture-green-ridge", "VT", "B", None, 44.40, -72.95, 300, 1200, 5, False, None),
    ("mike-dome", "Mike Snow Dome", "fixture-front-range", "CO", "A", "approved", 39.74, -105.62, None, 300, 3, False, None),
    ("november-ramps", "November Ramps", "fixture-wasatch-like", "UT", "A", "draft", 40.66, -111.50, None, None, None, False, None),
]

# facility_type per resort; lima-hollow deliberately omits the key to test tolerance.
FACILITY = {"mike-dome": "indoor", "november-ramps": "training_center"}
CRITERIA = {
    "alpha-peak": ["operator_compensation", "luxury"],
    "foxtrot-basin": ["diehard", "heritage"],
    "kilo-notch": ["diehard", "heritage"],
    "mike-dome": ["year_round_training"],
    "november-ramps": ["year_round_training", "athlete_development"],
}
APPROVALS = {
    "alpha-peak": {"by": "Fixture Reviewer", "date": "2026-10-05", "note": "Synthetic approval"},
    "mike-dome": {"by": "Fixture Reviewer", "date": "2026-10-06", "note": None},
}

CLUSTERS = {
    "fixture-front-range": ("Fixture Front Range", ["CO"]),
    "fixture-high-desert": ("Fixture High Desert", ["CO"]),
    "fixture-wasatch-like": ("Fixture Canyon Range", ["UT"]),
    "fixture-lake-crest": ("Fixture Lake Crest", ["CA", "NV"]),
    "fixture-green-ridge": ("Fixture Green Ridge", ["VT"]),
}


SEASON_LENGTH = {"alpha-peak": 150, "bravo-bowl": 138, "charlie-hill": 120, "echo-mesa": 112, "foxtrot-basin": 168,
                 "golf-canyon": 131, "hotel-lake": 160, "india-crest": 142, "juliet-summit": 134, "kilo-notch": 151,
                 "lima-hollow": 118}

LEVELS = ["none", "level_1", "level_2", "level_3", "trainer", "examiner"]
COMPONENTS = ["cert_pay_differential", "paid_training_hours", "examiner_trainer_presence",
              "private_lesson_share", "year_round_staff", "clinic_access"]
ASSUMPTIONS = [  # keys and labels per pipeline/career.py; values synthetic
    ("cert_level", "Cert level used", "level_2"), ("hours_per_week", "Hours per week", 32),
    ("season_months", "Season months", 5), ("housing_choice", "Housing used", "cheapest_employee_housing"),
    ("food_monthly_usd", "Food per month (USD)", 450), ("transport_monthly_usd", "Transport per month (USD)", 120),
    ("pass_offset_usd", "Pass offset (USD)", 0), ("gear_offset_usd", "Gear offset (USD)", 400),
    ("range_spread_low", "Range below mid, share of earnings", 0.15),
    ("range_spread_high", "Range above mid, share of earnings", 0.12),
    ("pay_hourly_mid_usd", "Hourly pay used, midpoint (USD)", 27.5),
    ("season_length_days", "Season length used (days)", 150), ("rent_monthly_usd", "Rent used per month (USD)", 650),
]


def pay_rows(base, levels, stale_level=None, confidence="published", reported_level=None):
    rows = []
    for i, lvl in enumerate(levels):
        lo = round(base + 2.5 * LEVELS.index(lvl), 2)
        conf = "reported" if lvl == reported_level else confidence
        rows.append({"cert_level": lvl, "hourly_low": lo, "hourly_high": round(lo + 3, 2), "confidence": conf,
                     "aggregate_count": 4 if conf == "reported" else None,
                     "source_id": "fixture-survey" if conf == "reported" else "fixture-resort-site",
                     "as_of": "2024-11-01" if lvl == stale_level else AS_OF, "season": "2024-25" if lvl == stale_level else "2025-26",
                     "stale": lvl == stale_level})
    return rows


def prof(score, status="computed"):
    """All six components, always (contract). Non computed statuses carry null weights or values."""
    weights = [0.25, 0.2, 0.15, 0.15, 0.15, 0.1]
    base = (score if score is not None else 50) / 100
    vals = [round(min(1, max(0, base + d)), 2) for d in (0.1, -0.05, 0.05, -0.1, 0.0, 0.0)]
    if status == "insufficient_data":
        vals = [vals[0], vals[1]] + [None] * 4
    rows = []
    for c, w, v in zip(COMPONENTS, weights, vals):
        w = None if status == "weights_unset" else w
        pts = round(100 * w * v, 1) if status == "computed" and v is not None else None
        rows.append({"component": c, "weight": w, "value": v, "points": pts, "source_id": "fixture-model"})
    return {"score": score if status == "computed" else None, "status": status, "breakdown": rows}


def lucr(low, mid, high, status="computed"):
    vals = [(k, l, None if status == "assumptions_unset" and k == "food_monthly_usd" else v) for k, l, v in ASSUMPTIONS]
    return {"low": low if status == "computed" else None, "mid": mid if status == "computed" else None,
            "high": high if status == "computed" else None, "status": status,
            "assumptions": [{"key": k, "label": l, "value": v} for k, l, v in vals]}


def career_data():
    """Synthetic career records covering every status, built lazily so sourced() is defined."""
    return {
        "alpha-peak": {"pay": pay_rows(21, LEVELS, stale_level="level_1", reported_level="level_3"),
                       "private_rate": sourced({"low": 150, "high": 220, "unit": "hour"}),
                       "private_split": sourced(45, "fixture-survey", "reported"),
                       "professionalism": prof(72), "lucrativeness": lucr(3200, 6100, 8900),
                       "stale_fields": ["pay.level_1", "private_split", "professionalism.clinic_access"]},
        "bravo-bowl": {"pay": pay_rows(19, ["none", "level_1"]), "private_rate": None, "private_split": None,
                       "professionalism": prof(None, "weights_unset"), "lucrativeness": lucr(None, None, None, "assumptions_unset"),
                       "stale_fields": []},
        "charlie-hill": {"pay": pay_rows(17, ["level_2"], confidence="estimated"), "private_rate": None, "private_split": None,
                         "professionalism": prof(None, "insufficient_data"),
                         "lucrativeness": lucr(None, None, None, "insufficient_data"), "stale_fields": []},
        "foxtrot-basin": {"pay": pay_rows(22, ["level_1", "level_2", "level_3"]),
                          "private_rate": sourced({"low": 700, "high": 950, "unit": "full_day"}),
                          "private_split": sourced(40, "fixture-model", "estimated"),
                          "professionalism": prof(85), "lucrativeness": lucr(4800, 7900, 10600), "stale_fields": []},
        "hotel-lake": {"pay": pay_rows(23, ["level_1", "level_2"]), "private_rate": None, "private_split": None,
                       "professionalism": prof(61), "lucrativeness": lucr(None, None, None, "insufficient_data"),
                       "stale_fields": []},
    }


def sourced(value, source_id="fixture-resort-site", confidence="published", note=None):
    if value is None:
        return None
    out = {"value": value, "source_id": source_id, "as_of": AS_OF, "confidence": confidence}
    if note:
        out["note"] = note
    return out


def size_rank(acres):
    if acres is None:
        return 1
    for rank, limit in ((1, 500), (2, 1000), (3, 2000), (4, 4000)):
        if acres < limit:
            return rank
    return 5


def resort_record(row):
    rid, name, cluster, state, tier, status, lat, lon, acres, vert, lifts, climate, _ = row
    sparse = rid in ("delta-ridge",)
    facility = FACILITY.get(rid, "ski_area")
    staff = sourced(round((acres or 0) / 4) or None, "fixture-model", "estimated", "synthetic estimate") if acres else None
    record = {
        "id": rid, "name": name, "tier": tier,
        "tier_a_criteria": [
            {"criterion": c, "rationale": f"Synthetic rationale for {c.replace('_', ' ')}.", "source_id": "fixture-resort-site"}
            for c in CRITERIA.get(rid, ["luxury"])
        ] if tier == "A" else [],
        "tier_a_status": status,
        "approval": APPROVALS.get(rid) if status == "approved" else None,
        "owner": sourced("Fixture Mountain Co", "fixture-ownership") if tier == "A" else None,
        "lat": lat, "lon": lon, "location_source_id": "fixture-location",
        "cluster_id": cluster, "state": state,
        "size": {
            "acres": sourced(acres), "vertical_ft": sourced(vert),
            "lifts": sourced(lifts, "fixture-survey", "reported") if rid == "bravo-bowl" else sourced(lifts),
            "ski_school_staff_est": staff,
        },
        "elevation": {
            "base_ft": None if sparse else sourced(8100 if vert else None),
            "summit_ft": None if sparse else sourced(8100 + vert if vert else None),
        },
        "season": {
            "open_typical": None if sparse else sourced("11-20"),
            "close_typical": None if sparse else sourced("04-12"),
            "length_days": sourced(SEASON_LENGTH[rid], "fixture-model", "estimated") if rid in SEASON_LENGTH and not sparse else None,
            "training_start": sourced("11-08") if tier == "A" else None,
        },
        "terrain": {
            "beginner_pct": None if sparse else sourced(18),
            "intermediate_pct": None if sparse else sourced(29),
            "advanced_pct": None if sparse else sourced(36),
            "expert_pct": None if sparse else sourced(17),
            "character_tags": [] if sparse else ["bowls", "groomers", "tree_skiing"],
        },
        "fit": {"enthusiast_types": ["powder_hound"] if tier == "A" else [],
                "professional_types": ["career_instructor"] if tier == "A" else []},
        "clients": {"profile_tags": ["destination_families", "private_lessons"] if tier == "A" else [],
                    "notes_md": None},
        "career": career_data().get(rid), "editorial": None,
        "climate_available": climate,
    }
    if rid != "lima-hollow":
        record["facility_type"] = facility
    ids = {"fixture-location"}
    for group in ("size", "elevation", "season", "terrain"):
        for v in record[group].values():
            if isinstance(v, dict):
                ids.add(v["source_id"])
    if record["owner"]:
        ids.add(record["owner"]["source_id"])
    for c in record["tier_a_criteria"]:
        ids.add(c["source_id"])
    career = record["career"] or {}
    for row in career.get("pay", []):
        ids.add(row["source_id"])
    for k in ("private_rate", "private_split"):
        if career.get(k):
            ids.add(career[k]["source_id"])
    for b in (career.get("professionalism") or {}).get("breakdown", []):
        ids.add(b["source_id"])
    record["sources"] = sorted(ids)
    return record


def hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def buffered_hull(coords, km=15):
    ring = []
    for lon, lat in coords:
        dlat = km / 111.0
        dlon = km / (111.0 * math.cos(math.radians(lat)))
        for i in range(16):
            a = 2 * math.pi * i / 16
            ring.append((round(lon + dlon * math.cos(a), 4), round(lat + dlat * math.sin(a), 4)))
    h = hull(ring)
    return [[list(p) for p in h + [h[0]]]]


def climate_record(row):
    rid, *_rest = row
    lat = row[6]
    density_source = row[12]
    base = {"alpha-peak": 1.0, "foxtrot-basin": 1.35, "hotel-lake": 1.25}.get(rid, 0.75)
    peak = {10: .25, 11: .7, 12: .95, 1: 1.0, 2: .95, 3: .9, 4: .55, 5: .15, 6: 0, 7: 0, 8: 0, 9: .05}
    months = []
    for m in SEASON:
        f = peak[m]
        snow = round(62 * base * f, 1)
        warm = {10: 52, 11: 38, 12: 29, 1: 27, 2: 30, 3: 37, 4: 45, 5: 55, 6: 66, 7: 74, 8: 72, 9: 63}[m]
        warm -= round((lat - 39) * 2)
        mix = None
        if f >= .5 and not (rid == "bravo-bowl" and m == 4):
            dry = round(.3 + .2 * f * (1.2 if base > 1 else .8), 2)
            moist = round(.12 + .2 * (1 - f), 2)
            mix = {"dry": dry, "average": round(1 - dry - moist, 2), "moist": moist}
        months.append({
            "resort_id": rid, "month": m,
            "snowfall_in": snow,
            "powder_days": round(snow / 14, 1) if snow else 0.0,
            "banner_days": round(snow / 45, 1) if snow else 0.0,
            "avg_storm_depth_in": round(4 + 5 * f * base, 1) if f else None,
            "density_pct_median": round(9 - 2 * f, 1) if mix else None,
            "density_mix": mix,
            "density_source": density_source if mix else None,
            "temp_high_f": warm, "temp_low_f": warm - 22,
            "wind_mph": round(8 + 6 * f, 1), "sun_pct": round(55 - 15 * f),
        })
    srcs = ["fixture-climate"] + (["fixture-snotel"] if density_source == "snotel" else [])
    return {"version": 1, "generated_at": GENERATED_AT, "resort_id": rid, "source_ids": srcs,
            "period": {"start_year": 1996, "end_year": 2025}, "months": months}


def recolor_props(r):
    climate = next((row for row in RESORTS if row[0] == r["id"] and row[11]), None)
    snow = round(sum(m["snowfall_in"] or 0 for m in climate_record(climate)["months"]), 1) if climate else None
    c = r["career"] or {}
    return {
        "season_length_days": r["season"]["length_days"]["value"] if r["season"]["length_days"] else None,
        "snowfall_annual_in": snow,
        "professionalism_score": (c.get("professionalism") or {}).get("score"),
        "lucrativeness_mid": (c.get("lucrativeness") or {}).get("mid"),
    }


def write(name, data):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")


def main():
    states_src = json.loads((REPO / "data" / "build" / "us-states.geojson").read_text())

    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 3), round(c[1], 3)]

    feats = [{"type": "Feature", "properties": f["properties"],
              "geometry": {"type": f["geometry"]["type"], "coordinates": rnd(f["geometry"]["coordinates"])}}
             for f in states_src["features"] if f["properties"]["code"] in STATE_CODES]
    write("us-states.geojson", {"type": "FeatureCollection", "version": 1, "generated_at": GENERATED_AT,
                                "source": "Subset of data/build/us-states.geojson for tests", "features": feats})

    resorts = [resort_record(r) for r in RESORTS]
    write("resorts.json", {"version": 1, "generated_at": GENERATED_AT, "resorts": resorts})
    write("resorts.geojson", {"type": "FeatureCollection", "version": 1, "generated_at": GENERATED_AT, "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
         "properties": {"id": r["id"], "name": r["name"], "tier": r["tier"], "cluster_id": r["cluster_id"],
                        "state": r["state"], "facility_type": r.get("facility_type"),
                        "acres": r["size"]["acres"]["value"] if r["size"]["acres"] else None,
                        "vertical_ft": r["size"]["vertical_ft"]["value"] if r["size"]["vertical_ft"] else None,
                        "lifts": r["size"]["lifts"]["value"] if r["size"]["lifts"] else None,
                        "size_rank": size_rank(r["size"]["acres"]["value"] if r["size"]["acres"] else None),
                        **recolor_props(r)}}
        for r in resorts]})

    cfeats = []
    for cid, (name, states) in CLUSTERS.items():
        members = [r for r in resorts if r["cluster_id"] == cid]
        cfeats.append({"type": "Feature",
                       "geometry": {"type": "Polygon", "coordinates": buffered_hull([(r["lon"], r["lat"]) for r in members])},
                       "properties": {"id": cid, "name": name, "states": states,
                                      "resort_ids": [r["id"] for r in members],
                                      "tier_a_count": sum(r["tier"] == "A" for r in members),
                                      "overview_md": None, "sources": ["fixture-location"]}})
    write("clusters.geojson", {"type": "FeatureCollection", "version": 1, "generated_at": GENERATED_AT, "features": cfeats})

    states = []
    for code, name in STATE_NAMES.items():
        states.append({"code": code, "name": name,
                       "cluster_ids": [cid for cid, (_, s) in CLUSTERS.items() if code in s],
                       "resort_count": sum(r["state"] == code for r in resorts)})
    write("states.json", {"version": 1, "generated_at": GENERATED_AT, "states": states})
    write("sources.json", {"version": 1, "generated_at": GENERATED_AT, "sources": SOURCES})
    write("definitions.json", {"version": 1, "generated_at": GENERATED_AT,
                               "snow": {"powder_day_in": 6, "banner_day_in": 12},
                               "density": {"dry_max_pct": 7, "moist_min_pct": 12},
                               "display": {"priority": ["published", "reported", "estimated"]},
                               "reports": {"aggregation_min": 3}, "staleness": {"max_age_months": 18}})
    for row in RESORTS:
        if row[11]:
            write(f"climate/{row[0]}.json", climate_record(row))
    print(f"wrote fixtures to {OUT}")


if __name__ == "__main__":
    main()
