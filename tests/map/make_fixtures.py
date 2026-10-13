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
import sys
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
    {"id": "fixture-noaa-normals", "url": "https://example.com/fixture/noaa-normals",
     "title": "Synthetic fixture: station snowfall normals", "accessed_date": AS_OF},
    # Trail map files always cite this id (contract); the fixture points it at example.com.
    {"id": "openskimap-runs", "url": "https://example.com/fixture/runs",
     "title": "Synthetic fixture: runs", "accessed_date": AS_OF},
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
ASSUMPTIONS = [  # owner decision 4 keys and labels per docs/map/ARCHITECTURE.md; values synthetic
    ("wage_top_usd", "Top of the posted wage scale, per hour", 32.5),
    ("wage_bottom_usd", "Bottom of the posted wage scale, per hour", 21),
    ("hours_per_week", "Hours per week, standard full time", 40),
    ("season_months", "Season months, from the sourced season length", 4.93),
    ("season_months_basis", "Season months basis", "season_length"),
    ("weeks_per_month", "Weeks per month", 4.33),
    ("tips_season_usd", "Tips are not included yet", None),
    ("rent_monthly_usd", "Average rent per month", 1150),
    ("tax", "Savings are before tax", None),
]
ACCESS = {
    "alpha-peak": ("transit_friendly", "A free bus links the staff village and the town to the base all day."),
    "kilo-notch": ("car_centric", "Most staff drive about 20 minutes from the valley towns."),
}


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


def lucr(low, mid, high, status="computed", rent_source=None):
    vals = [(k, l, None if status == "assumptions_unset" and k == "hours_per_week" else v) for k, l, v in ASSUMPTIONS]
    return {"low": low if status == "computed" else None, "mid": mid if status == "computed" else None,
            "high": high if status == "computed" else None, "status": status,
            "assumptions": [{"key": k, "label": l, "value": v} for k, l, v in vals]
            + ([{"key": "rent_basis", "label": "Rent basis", "value": rent_source[0]},
                {"key": "rent_source_id", "label": "Rent source", "value": rent_source[1]}] if rent_source else [])}


def career_data():
    """Synthetic career records covering every status, built lazily so sourced() is defined."""
    return {
        "alpha-peak": {"pay": pay_rows(21, LEVELS, stale_level="level_1", reported_level="level_3"),
                       "private_rate": sourced({"low": 150, "high": 220, "unit": "hour"}),
                       "private_split": sourced(45, "fixture-survey", "reported"),
                       "lucrativeness": lucr(3200, 8900, 8900, rent_source=("employee_housing_mean:alpha-staff-village,alpha-town-lease", "fixture-resort-site, fixture-survey")),
                       "stale_fields": ["pay.level_1", "private_split"]},
        "bravo-bowl": {"pay": pay_rows(19, ["none", "level_1"]), "private_rate": None, "private_split": None,
                       "lucrativeness": lucr(None, None, None, "assumptions_unset"),
                       "stale_fields": []},
        "charlie-hill": {"pay": pay_rows(17, ["level_2"], confidence="estimated"), "private_rate": None, "private_split": None,
                         "lucrativeness": lucr(None, None, None, "insufficient_data"), "stale_fields": []},
        "foxtrot-basin": {"pay": pay_rows(22, ["level_1", "level_2", "level_3"]),
                          "private_rate": sourced({"low": 700, "high": 950, "unit": "full_day"}),
                          "private_split": sourced(40, "fixture-model", "estimated"),
                          "lucrativeness": lucr(4800, 10600, 10600, rent_source=("acs_median", "fixture-model")), "stale_fields": []},
        "hotel-lake": {"pay": pay_rows(23, ["level_1", "level_2"]), "private_rate": None, "private_split": None,
                       "lucrativeness": lucr(None, None, None, "insufficient_data"),
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
        "career": career_data().get(rid),
        "editorial": ({"overview_md": "Synthetic overview with **bold** text and a [linked source](https://example.com/fixture/editorial).",
                       "culture_md": "Synthetic culture paragraph one.\n\nSynthetic culture paragraph two.",
                       "alexandre_take_md": "Synthetic take from a reviewer.", "status": "reviewed",
                       "source_ids": ["fixture-resort-site"], "reviewed_date": "2026-10-10"} if rid == "alpha-peak" else None),
        "climate_available": climate,
    }
    if rid != "lima-hollow":
        record["facility_type"] = facility
    record["detail_available"] = rid in DETAIL_IDS
    record["trailmap_available"] = rid in TRAILMAP_IDS
    if rid == "alpha-peak":
        record["official_trailmap"] = {"url": "https://example.com/fixture/alpha-peak-trail-map-2025-26.pdf", "format": "pdf",
                                       "season": "2025/26", "source_id": "fixture-resort-site", "as_of": AS_OF,
                                       "confidence": "estimated", "note": None}
    if rid in ACCESS:
        record["access"] = {"rating": ACCESS[rid][0], "summary_md": ACCESS[rid][1], "source_id": "fixture-location",
                            "as_of": AS_OF, "confidence": "estimated"}
    record["housing_summary"] = ({"site_count": 3, "cheapest_rent_monthly_low": 550, "cheapest_site_id": "alpha-staff-village",
                                  "has_transit": True} if rid == "alpha-peak" else None)
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
        "lucrativeness_mid": (c.get("lucrativeness") or {}).get("mid"),
    }


DETAIL_IDS = {"alpha-peak", "foxtrot-basin"}
TRAILMAP_IDS = {"alpha-peak"}


def circle(lon, lat, km, n=24):
    dlat = km / 111.0
    dlon = km / (111.0 * math.cos(math.radians(lat)))
    ring = [[round(lon + dlon * math.cos(2 * math.pi * i / n), 4), round(lat + dlat * math.sin(2 * math.pi * i / n), 4)] for i in range(n)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def lifts_for(lon, lat, names):
    feats = []
    for i, name in enumerate(names):
        a = math.radians(40 + i * 35)
        end = [round(lon + 0.02 * math.cos(a), 5), round(lat + 0.015 * math.sin(a), 5)]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat], end]},
                      "properties": {"name": name, "lift_type": "chair_lift", "source_id": "fixture-location"}})
    return {"type": "FeatureCollection", "features": feats}


def detail_record(r):
    lon, lat = r["lon"], r["lat"]
    base = {"lat": lat, "lon": lon, "source_id": "fixture-location"}
    if r["id"] == "foxtrot-basin":  # lifts only, the shape real Tier A detail has today
        return {"version": 1, "generated_at": GENERATED_AT, "resort_id": r["id"], "base_area": base,
                "lifts": lifts_for(lon, lat, ["Fixture Lift One", "Fixture Lift Two", None]),
                "housing": [], "rent_baseline": None, "transit": None, "isochrones": []}
    town = [round(lon + 0.09, 4), round(lat - 0.03, 4)]
    def line(a, b):
        return {"type": "LineString", "coordinates": [a, [round((a[0] + b[0]) / 2, 4), round((a[1] + b[1]) / 2 + 0.01, 4)], b]}
    housing = [
        {"id": "alpha-staff-village", "resort_id": r["id"], "name": "Fixture Staff Village", "type": "resort_owned",
         "lat": round(lat - 0.01, 4), "lon": round(lon + 0.025, 4), "location_source_id": "fixture-location", "precision": "building",
         "address": sourced("1 Fixture Way, Fixture Town"),
         "rent_monthly": sourced({"low": 550, "high": 700}), "occupancy": "shared", "deposit": sourced(300),
         "eligibility_note": "Synthetic note: full time staff only.", "confidence": "published", "verified_date": AS_OF,
         "commutes": [
             {"mode": "drive", "minutes": 6, "distance_mi": 2.1, "transit_route_ids": [],
              "geometry": line([round(lon + 0.025, 4), round(lat - 0.01, 4)], [lon, lat]), "source_id": "fixture-model"},
             {"mode": "walk", "minutes": 25, "distance_mi": 1.2, "transit_route_ids": [],
              "geometry": line([round(lon + 0.025, 4), round(lat - 0.01, 4)], [lon, lat]), "source_id": "fixture-model"}]},
        {"id": "alpha-town-lease", "resort_id": r["id"], "name": "Fixture Town Lease", "type": "master_lease",
         "lat": town[1], "lon": town[0], "location_source_id": "fixture-location", "precision": "neighborhood",
         "rent_monthly": sourced({"low": 800, "high": 950}, "fixture-survey", "reported"), "occupancy": "mixed", "deposit": None,
         "eligibility_note": None, "confidence": "reported", "verified_date": AS_OF,
         "commutes": [
             {"mode": "bus", "minutes": 28, "distance_mi": 7.4, "transit_route_ids": ["fixture-blue"],
              "geometry": line(town, [lon, lat]), "source_id": "fixture-model"},
             {"mode": "drive", "minutes": None, "distance_mi": None, "transit_route_ids": [], "geometry": None, "source_id": None}]},
        {"id": "alpha-market-west", "resort_id": r["id"], "name": "Fixture West Rentals", "type": "market",
         "lat": round(lat + 0.04, 4), "lon": round(lon - 0.08, 4), "location_source_id": "fixture-location", "precision": "neighborhood",
         "rent_monthly": sourced({"low": 1200, "high": 1600}, "fixture-model", "estimated"), "occupancy": "private", "deposit": None,
         "eligibility_note": None, "confidence": "estimated", "verified_date": AS_OF, "commutes": []},
    ]
    transit = {"routes": [{"id": "fixture-blue", "agency": "Fixture Transit", "short_name": "Blue", "long_name": "Fixture Blue Line",
                           "color": "3987E5", "geometry": {"type": "MultiLineString", "coordinates": [[town, [lon, lat]]]}},
                          {"id": "fixture-red", "agency": "Fixture Transit", "short_name": "Red", "long_name": None, "color": "bad",
                           "geometry": {"type": "LineString", "coordinates": [[round(lon - 0.08, 4), round(lat + 0.04, 4)], [lon, lat]]}}],
               "stops": [{"id": "s1", "name": "Fixture Town Center", "lat": town[1], "lon": town[0], "route_ids": ["fixture-blue"]},
                         {"id": "s2", "name": "Fixture Base Stop", "lat": lat, "lon": lon, "route_ids": ["fixture-blue", "fixture-red"]}],
               "source_ids": ["fixture-model"]}
    return {"version": 1, "generated_at": GENERATED_AT, "resort_id": r["id"], "base_area": base,
            "lifts": lifts_for(lon, lat, ["Fixture Express", "Fixture Summit Chair", "Fixture Bowl Lift", None]),
            "housing": housing,
            "rent_reported": sourced({"low": 700, "high": 1000}, "fixture-survey", "reported", "Range from 4 approved reports"),
            "rent_baseline": {"county_fips": "00000", "county_name": "Fixture County",
                              "fmr": {"studio": sourced(1100, "fixture-model", "estimated"), "one_br": sourced(1300, "fixture-model", "estimated"),
                                      "two_br": sourced(1650, "fixture-model", "estimated")},
                              "acs_median_gross_rent": sourced(1450, "fixture-model", "estimated")},
            "transit": transit,
            "isochrones": [{"minutes": m, "mode": "drive", "geometry": circle(lon, lat, km), "source_id": "fixture-model"}
                           for m, km in ((45, 40), (30, 26), (15, 12))]}


def trailmap_record(r):
    """Synthetic trail map: one run per North American sign, an unnamed run, an unrated run, a park area."""
    lon, lat = r["lon"], r["lat"]
    top = (lon + 0.012, lat + 0.022)

    def run(name, sign, difficulty, dx, bend):
        pts = [top, (top[0] + dx * 0.4 + bend, top[1] - 0.008), (top[0] + dx * 0.8, top[1] - 0.015), (lon + dx, lat + 0.002)]
        return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in pts]},
                "properties": {"name": name, "difficulty": difficulty, "difficulty_us": sign, "grooming": None, "lit": None}}

    runs = [
        run("Fixture Meadow", "green", "easy", -0.012, -0.002),
        run("Fixture Cruiser", "blue", "intermediate", -0.004, 0.002),
        run(None, "blue", "intermediate", 0.000, -0.001),
        run("Fixture Chute", "black", "advanced", 0.006, 0.003),
        run("Fixture Couloir", "double_black", "expert", 0.012, 0.004),
        run("Fixture Traverse", None, "freeride", 0.018, -0.003),
    ]
    px, py = lon - 0.006, lat + 0.006
    ring = [[px, py], [px + 0.004, py], [px + 0.004, py + 0.003], [px, py + 0.003], [px, py]]
    runs.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(x, 5), round(y, 5)] for x, y in ring]]},
                 "properties": {"name": "Fixture Park", "difficulty": None, "difficulty_us": "terrain_park", "grooming": None, "lit": None}})
    lifts = lifts_for(lon, lat, ["Fixture Express", "Fixture Gondola"])
    pts = [c for f in runs + lifts["features"] for c in (f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"])]
    xs = [p[0] for p in pts] + [lon]
    ys = [p[1] for p in pts] + [lat]
    return {"version": 1, "generated_at": GENERATED_AT, "resort_id": r["id"], "source_id": "openskimap-runs",
            "runs": {"type": "FeatureCollection", "features": runs}, "lifts": lifts,
            "bounds": [round(min(xs), 5), round(min(ys), 5), round(max(xs), 5), round(max(ys), 5)]}


def synthetic_stations():
    """Invented station snowfall on a half degree lattice over the fixture states: a smooth ridge over the
    fixture mountains, low elsewhere. Test data only; never real normals."""
    out = []
    for i in range(0, 47):
        for j in range(0, 25):
            lon, lat = -124.5 + i * 0.5, 34.0 + j * 0.5
            ridge = math.exp(-((lon + 111.6) ** 2) / 6 - ((lat - 40.6) ** 2) / 3)
            sierra = math.exp(-((lon + 120.2) ** 2) / 2 - ((lat - 39.2) ** 2) / 4)
            rockies = math.exp(-((lon + 106.2) ** 2) / 3 - ((lat - 39.5) ** 2) / 5)
            out.append((lon, lat, round(15 + 180 * max(ridge, sierra, rockies), 1)))
    for i in range(0, 13):
        for j in range(0, 11):
            lon, lat = -74.0 + i * 0.5, 42.5 + j * 0.5
            out.append((lon, lat, round(60 + 70 * math.exp(-((lon + 72.8) ** 2) - ((lat - 44.4) ** 2)), 1)))
    return out


def write_heat_fixture(cfg, resorts, outlines):
    """heat/snowfall.json and its PNG, built by pipeline/heat.py from synthetic stations and the fixture
    resorts' synthetic snowfall, at a lower resolution to keep the fixture small."""
    from pipeline import heat  # noqa: PLC0415
    h = cfg.definitions.heat
    h = h.model_copy(update={"snowfall": h.snowfall.model_copy(update={"px_per_degree": 10})})
    pockets = []
    for r in resorts:
        v = recolor_props(r)["snowfall_annual_in"]
        if v is not None:
            pockets.append(heat.Point(r["lon"], r["lat"], v))
    stations = [heat.Point(lon, lat, v) for lon, lat, v in synthetic_stations()]
    out_dir = OUT
    (out_dir / "heat").mkdir(parents=True, exist_ok=True)
    for old in (out_dir / "heat").glob("*"):
        old.unlink()
    heat.write_snowfall(out_dir, stations=stations, pockets=pockets, outlines=outlines, heat=h,
                        source={"id": "fixture-noaa-normals"}, generated_at=GENERATED_AT, as_of=AS_OF)


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
                                      "overview_md": "Synthetic cluster overview." if cid == "fixture-front-range" else None,
                                      "alexandre_take_md": "Synthetic cluster take." if cid == "fixture-front-range" else None,
                                      "sources": ["fixture-location"]}})
    write("clusters.geojson", {"type": "FeatureCollection", "version": 1, "generated_at": GENERATED_AT, "features": cfeats})

    states = []
    for code, name in STATE_NAMES.items():
        states.append({"code": code, "name": name,
                       "cluster_ids": [cid for cid, (_, s) in CLUSTERS.items() if code in s],
                       "resort_count": sum(r["state"] == code for r in resorts)})
    write("states.json", {"version": 1, "generated_at": GENERATED_AT, "states": states})
    write("sources.json", {"version": 1, "generated_at": GENERATED_AT, "sources": SOURCES})
    sys.path.insert(0, str(REPO))
    from pipeline.config import load_config  # noqa: PLC0415
    from pipeline.build import display_definitions  # noqa: PLC0415
    cfg = load_config(REPO / "config")
    write("definitions.json", {"version": 1, "generated_at": GENERATED_AT,
                               # Heat map display settings are config, not resort facts: copied from the real config.
                               "heat": display_definitions(cfg)["heat"],
                               "snow": {"powder_day_in": 6, "banner_day_in": 12},
                               "density": {"dry_max_pct": 7, "moist_min_pct": 12},
                               "display": {"priority": ["published", "reported", "estimated"]},
                               "reports": {"aggregation_min": 3}, "staleness": {"max_age_months": 18},
                               # Synthetic endpoint; the browser test intercepts it. Production keeps null until it exists.
                               "submissions": {"endpoint": "https://reports.example.test/submit"}})
    for r in resorts:
        if r["detail_available"]:
            write(f"detail/{r['id']}.json", detail_record(r))
    for r in resorts:
        if r["trailmap_available"]:
            write(f"trailmap/{r['id']}.json", trailmap_record(r))
    for row in RESORTS:
        if row[11]:
            write(f"climate/{row[0]}.json", climate_record(row))
    write_heat_fixture(cfg, resorts, {"type": "FeatureCollection", "features": feats})
    # The slim national index the map loads first, built the same way as the real one.
    from pipeline.routes import write_map_index  # noqa: PLC0415
    write_map_index(OUT)
    print(f"wrote fixtures to {OUT}")


if __name__ == "__main__":
    main()
