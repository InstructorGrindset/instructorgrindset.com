"""Jet stream layer transforms (pipeline/jetstream.py, docs/map/JETSTREAM.md). Synthetic winds and index values."""

from __future__ import annotations

import math

import pytest

from pipeline import jetstream as js
from pipeline.config import JetstreamDefs

CFG = JetstreamDefs.model_validate({
    "season_label": "November to April",
    "baseline_seasons": 2,
    "first_season": "1990/91",
    "enso": {"threshold": 0.5, "episode_min_seasons": 5, "season_value": "DJF",
             "strength": {"weak": 0.5, "moderate": 1.0, "strong": 1.5}},
    "axis": {"lat_min": 20, "lat_max": 70, "min_speed_ms": 18, "min_prominence_ms": 1.5, "max_step_deg": 3,
             "min_length_deg": 4, "smooth_deg": 3},
    "band": {"fraction": 0.9, "max_half_width_deg": 6},
    "overflies": {"sample_deg": 0.5},
    "split": {"min_seasons": 1, "min_mean_shift_deg": 2.0, "lon_west": -125, "lon_east": -67},
    "colors": {"last10": "#eeeeee", "el_nino": "#ff8800", "la_nina": "#0088ff", "neutral": "#88ff00"},
    "method": "Test method.",
})
CODES = js_codes = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")


def rows_from(values_by_year: dict[int, list[float]]) -> list[tuple[str, int, float]]:
    return [(code, year, v) for year, vals in sorted(values_by_year.items()) for code, v in zip(CODES, vals)]


# ----------------------------------------------------------------------------------------------- seasons


def test_classify_seasons_episodes_and_strength():
    rows = rows_from({
        1990: [0.0] * 6 + [0.6, 0.8, 1.0, 1.2, 1.4, 1.6],   # warm episode from JJA 1990 ...
        1991: [1.7, 1.2, 0.7, 0.3] + [0.0] * 4 + [-0.6, -0.7, -0.8, -0.9],   # ... through FMA 1991, peak 1.7
        1992: [-0.8, -0.4] + [0.0] * 10,   # cold run SON 1991 to DJF 1992 is only 5 values: an episode
        1993: [0.6, 0.6, 0.6, 0.6] + [0.0] * 8,   # 4 warm values: no episode
    })
    out = {c["season"]: c for c in js.classify_seasons(rows, ["1990/91", "1991/92", "1992/93", "1993/94"], CFG.enso)}
    assert out["1990/91"]["phase"] == "el_nino" and out["1990/91"]["strength"] == "strong"   # peak rule (1 value)
    three = CFG.enso.model_copy(update={"strength_min_seasons": 3})
    held = {c["season"]: c for c in js.classify_seasons(rows, ["1990/91"], three)}
    # Only 1.6 and 1.7 reach the strong floor; 1.0 to 1.7 hold the moderate floor for 6 values.
    assert held["1990/91"]["strength"] == "moderate" and held["1990/91"]["peak"] == 1.7
    assert out["1990/91"]["value"] == 1.7 and out["1990/91"]["peak"] == 1.7
    assert out["1991/92"]["phase"] == "la_nina" and out["1991/92"]["strength"] == "weak"
    assert out["1991/92"]["peak"] == -0.9
    assert out["1992/93"]["phase"] == "neutral" and out["1992/93"]["strength"] is None
    assert out["1993/94"]["phase"] is None   # no DJF 1994 row


def test_season_labels():
    assert js.season_label(1999) == "1999/00"
    assert js.season_start("2025/26") == 2025


# ----------------------------------------------------------------------------------------------- axis


LATS = list(range(15, 76))


def gauss(center: float, peak: float, width: float) -> list[float]:
    return [peak * math.exp(-((la - center) / width) ** 2) for la in LATS]


def test_find_peaks_prominence_and_refinement():
    col = [a + b for a, b in zip(gauss(30.4, 40, 6), gauss(52, 22, 4))]
    peaks = js.find_peaks(col, LATS, CFG.axis)
    assert len(peaks) == 2
    assert peaks[0][0] == pytest.approx(30.4, abs=0.1) and peaks[0][1] == pytest.approx(40, abs=0.2)
    assert peaks[1][0] == pytest.approx(52, abs=0.3)
    # A weak bump below min_speed drops; a shoulder without prominence drops.
    assert js.find_peaks(gauss(40, 15, 5), LATS, CFG.axis) == []
    shoulder = [a + b for a, b in zip(gauss(35, 40, 6), gauss(44, 3, 1.5))]
    assert len(js.find_peaks(shoulder, LATS, CFG.axis)) == 1


def test_link_chains_step_limit_and_length():
    lons = list(range(-130, -119))
    peaks = [[(40 + 0.5 * k, 30.0)] for k in range(len(lons))]
    peaks[5].append((60.0, 20.0))   # a lone peak: too short
    chains = js.link_chains(peaks, lons, CFG.axis)
    assert len(chains) == 1 and len(chains[0]) == len(lons)
    jump = [[(40.0, 30.0)] for _ in range(5)] + [[(48.0, 30.0)] for _ in range(6)]
    parts = js.link_chains(jump, lons, CFG.axis)
    assert [len(c) for c in parts] == [5, 6]   # an 8 degree jump splits the chain


def test_smooth_keeps_a_straight_chain():
    chain = [(lon, 40.0 + 0.1 * lon, 30.0) for lon in range(-130, -120)]
    out = js.smooth(chain, 3)
    assert out[3][1] == pytest.approx(chain[3][1])
    wiggle = [(lon, 40.0 + (1 if lon % 2 else -1), 30.0) for lon in range(-130, -120)]
    assert abs(js.smooth(wiggle, 3)[4][1] - 40.0) < 0.5


def test_band_edges_of_a_gaussian():
    lons = [-100]
    speed = [[v] for v in gauss(40, 40, 6)]
    grid = js.Grid([float(x) for x in LATS], lons, speed)
    (lon, south, north), = js.band_edges(grid, [(-100, 40.0, 40.0)], CFG.band)
    half = 6 * math.sqrt(-math.log(0.9))   # where exp(-(d/6)^2) = 0.9
    assert south == pytest.approx(40 - half, abs=0.15) and north == pytest.approx(40 + half, abs=0.15)
    capped = js.band_edges(js.Grid([float(x) for x in LATS], lons, [[v] for v in gauss(40, 40, 60)]),
                           [(-100, 40.0, 40.0)], CFG.band)
    assert capped[0][2] - capped[0][1] == pytest.approx(12, abs=0.06)


def test_label_branches_main_north_south():
    main = [(lon, 30.0, 40.0) for lon in range(-130, -100)]
    north = [(lon, 50.0, 20.0) for lon in range(-130, -115)]
    south = [(lon, 22.0, 25.0) for lon in range(-110, -100)]
    labels = dict((bid, c) for bid, c in js.label_branches([north, main, south]))
    assert labels["main"] is main and labels["north"] is north and labels["south"] is south


# ----------------------------------------------------------------------------------------------- overflies


SQUARE = {"type": "Polygon", "coordinates": [[[-110, 38], [-100, 38], [-100, 42], [-110, 42], [-110, 38]]]}
FAR = {"type": "Polygon", "coordinates": [[[-90, 25], [-85, 25], [-85, 28], [-90, 28], [-90, 25]]]}


def test_overflies_states_and_resorts():
    band = [[lon, 39.0, 41.0] for lon in range(-120, -94)]
    branches = [{"id": "main", "band": band}]
    states = {"AA": {"name": "Alpha", "geometry": SQUARE}, "BB": {"name": "Beta", "geometry": FAR}}
    resorts = [{"id": "in", "state": "AA", "lat": 40.0, "lon": -105.0},
               {"id": "out", "state": "AA", "lat": 41.8, "lon": -105.0},
               {"id": "east", "state": "BB", "lat": 26.0, "lon": -88.0}]
    out = js.overflies(branches, states, resorts, 0.5)
    assert out["states"] == [{"code": "AA", "name": "Alpha", "branches": ["main"]}]
    assert out["resorts"] == [{"id": "in", "state": "AA", "branch": "main"}]


def test_overflies_lists_the_state_of_every_resort_under_a_band():
    # A sliver of state CC lies under the band between sample points; its resort still lists the state.
    sliver = {"type": "Polygon", "coordinates": [[[-104.2, 39.6], [-104.1, 39.6], [-104.1, 39.9], [-104.2, 39.9], [-104.2, 39.6]]]}
    branches = [{"id": "main", "band": [[lon, 39.0, 41.0] for lon in range(-120, -94)]}]
    states = {"CC": {"name": "Gamma", "geometry": sliver}}
    out = js.overflies(branches, states, [{"id": "tiny", "state": "CC", "lat": 39.7, "lon": -104.15}], 0.5)
    assert out["states"] == [{"code": "CC", "name": "Gamma", "branches": ["main"]}]
    assert out["resorts"] == [{"id": "tiny", "state": "CC", "branch": "main"}]


def test_mean_shift_over_the_lower_48():
    a = [{"id": "main", "axis": [[lon, 40.0, 30] for lon in range(-130, -60)]}]
    b = [{"id": "main", "axis": [[lon, 43.0, 30] for lon in range(-130, -60)]}]
    assert js.mean_shift(a, b, -125, -67) == pytest.approx(3.0)
    assert js.mean_shift(a, [{"id": "north", "axis": []}], -125, -67) is None


# ----------------------------------------------------------------------------------------------- compile


def _season(lat_center: float) -> dict:
    lats = list(range(15, 76))
    lons = list(range(-130, -60))
    u = [[40 * math.exp(-((la - lat_center) / 6) ** 2) for _ in lons] for la in lats]
    v = [[0.0 for _ in lons] for _ in lats]
    return {"u": u, "v": v}


def test_compile_jetstream_layers_and_split():
    seasons = {"1990/91": 46.0, "1991/92": 34.0, "1992/93": 40.0, "1993/94": 40.0}
    extract = {"as_of": "2026-11-01", "grid": {"lat_min": 15, "lat_max": 75, "lon_min": -130, "lon_max": -61, "step_deg": 1},
               "seasons": [{"season": k, **_season(c)} for k, c in seasons.items()]}
    rows = rows_from({
        1990: [0.0] * 6 + [0.6] * 6, 1991: [0.6] * 3 + [0.0] * 5 + [-0.6] * 4,
        1992: [-0.6, -0.6] + [0.0] * 10, 1993: [0.0] * 12, 1994: [0.0] * 12,
    })
    inputs = js.JetInputs(extract, {"source": {"id": "era5-test"}}, rows,
                          {"source": {"id": "roni-test", "accessed_date": "2026-11-01"}})
    states = {"AA": {"name": "Alpha", "geometry": SQUARE}}
    out = js.compile_jetstream(inputs, CFG, states, [{"id": "r1", "state": "AA", "lat": 40.0, "lon": -105.0}], {"version": 1})
    ids = [layer["id"] for layer in out["layers"]]
    assert ids == ["last10", "el_nino", "la_nina", "neutral"]
    by_id = {layer["id"]: layer for layer in out["layers"]}
    assert by_id["el_nino"]["seasons"] == ["1990/91"] and by_id["la_nina"]["seasons"] == ["1991/92"]
    assert by_id["last10"]["seasons"] == ["1992/93", "1993/94"]
    main = by_id["el_nino"]["branches"][0]
    assert main["id"] == "main" and main["axis"][10][1] == pytest.approx(46.0, abs=0.1)
    assert by_id["neutral"]["overflies"]["resorts"] == [{"id": "r1", "state": "AA", "branch": "main"}]
    assert by_id["el_nino"]["confidence"] == "estimated" and out["seasons"][0]["confidence"] == "published"
    assert out["split"]["el_nino"]["split"] is False   # one strength class only: nothing to compare
    assert by_id["el_nino"]["color"] == "#ff8800"


def test_taper_narrows_the_band_at_both_ends():
    band = CFG.band.model_copy(update={"taper_deg": 4, "taper_min_fraction": 0.25})
    chain = [(lon, 40.0, 30.0) for lon in range(-130, -109)]
    edges = [(lon, 37.0, 43.0) for lon in range(-130, -109)]
    out = js.taper(chain, edges, band)
    assert out[0] == (-130, 40.0 - 0.75, 40.0 + 0.75)       # a quarter of the 3 degree half width at the end
    assert out[2][2] == pytest.approx(40 + 3 * (0.25 + 0.75 * 2 / 4))   # halfway into the taper
    assert out[10] == (-120, 37.0, 43.0)                     # untouched in the middle
    assert out[-1][1] == pytest.approx(39.25)
    assert js.taper(chain, edges, CFG.band) == edges         # taper_deg 0 keeps the band


def test_episode_strength_needs_consecutive_values():
    three = CFG.enso.model_copy(update={"strength_min_seasons": 3})
    assert js.episode_strength([0.6, 1.6, 1.7, 1.5, 0.9], three) == "strong"
    assert js.episode_strength([0.6, 1.6, 1.4, 1.7, 0.9], three) == "moderate"
    assert js.episode_strength([0.6, 0.8, 1.2, 0.7, 0.6], three) == "weak"


def _axis(lat: float) -> list[dict]:
    return [{"id": "main", "axis": [[lon, lat, 30] for lon in range(-125, -66)]}]


def test_strength_groups_merge_classes_that_look_alike():
    split = CFG.split.model_copy(update={"min_seasons": 2, "min_mean_shift_deg": 2.0})
    lat_of = {"w1": 40.0, "w2": 40.0, "m1": 36.5, "m2": 36.5, "s1": 36.0, "s2": 36.0}
    branches_for = lambda seasons: _axis(sum(lat_of[x] for x in seasons) / len(seasons))  # noqa: E731
    groups, pairs = js.strength_groups({"weak": ["w1", "w2"], "moderate": ["m1", "m2"], "strong": ["s1", "s2"]},
                                       branches_for, split)
    assert groups == [["weak"], ["moderate", "strong"]]   # weak differs by 3.5; moderate and strong by 0.5
    assert [(p["a"], p["b"], p["shift_deg"]) for p in pairs] == [("weak", "moderate", 3.5), ("moderate", "strong", 0.5)]
    alike = {"w1": 40.0, "w2": 40.0, "m1": 39.0, "m2": 39.0, "s1": 38.5, "s2": 38.5}
    groups, _ = js.strength_groups({"weak": ["w1", "w2"], "moderate": ["m1", "m2"], "strong": ["s1", "s2"]},
                                   lambda seasons: _axis(sum(alike[x] for x in seasons) / len(seasons)), split)
    assert groups == [["weak", "moderate", "strong"]]   # one toggle for the phase


def test_strength_groups_fold_small_classes_into_a_neighbor():
    split = CFG.split.model_copy(update={"min_seasons": 2, "min_mean_shift_deg": 2.0})
    lat_of = {"w1": 40.0, "m1": 40.0, "m2": 40.0, "s1": 34.0, "s2": 34.0}
    groups, _ = js.strength_groups({"weak": ["w1"], "moderate": ["m1", "m2"], "strong": ["s1", "s2"]},
                                   lambda seasons: _axis(sum(lat_of[x] for x in seasons) / len(seasons)), split)
    assert groups == [["weak", "moderate"], ["strong"]]   # one weak season joins moderate; every season stays in



def test_a_run_still_open_at_the_end_of_the_record_is_pending():
    rows = rows_from({1990: [0.0] * 9 + [0.7, 0.8, 0.9], 1991: [1.1, 1.2]})   # SON 1990 to JFM 1991: 5 values?
    rows = rows[:-1]   # ... the record stops at DJF 1991: 4 values, still short of an episode
    out = js.classify_seasons(rows, ["1990/91"], CFG.enso)[0]
    assert out["phase"] is None and out["value"] == 1.1


def test_each_winter_of_a_long_episode_gets_its_own_strength():
    three = CFG.enso.model_copy(update={"strength_min_seasons": 3})
    rows = rows_from({
        1998: [0.0] * 6 + [-0.8, -1.0, -1.2, -1.5, -1.6, -1.7],   # JJA 1998 on: strong in 1998/99
        1999: [-1.6, -1.2, -0.9, -0.8, -0.8, -0.9, -1.0, -1.1, -1.2, -1.3, -1.5, -1.6],
        2000: [-1.4, -1.0, -0.7, -0.5] + [0.0] * 8,
    })
    out = {c["season"]: c for c in js.classify_seasons(rows, ["1998/99", "1999/00"], three)}
    assert out["1998/99"]["phase"] == "la_nina" and out["1998/99"]["strength"] == "strong"
    assert out["1999/00"]["phase"] == "la_nina" and out["1999/00"]["strength"] == "moderate"   # 1.5 held for 2 values only
    assert out["1999/00"]["peak"] == -1.6


def test_link_chains_keep_crossing_jets_apart():
    lons = list(range(-130, -109))
    rising = [(30 + 1.0 * k, 40.0) for k in range(len(lons))]
    falling = [(50 - 1.0 * k, 30.0) for k in range(len(lons))]
    chains = js.link_chains([[a, b] for a, b in zip(rising, falling)], lons, CFG.axis)
    speeds = sorted({round(p[2]) for p in c} == {40} or {round(p[2]) for p in c} == {30} for c in chains)
    assert len(chains) == 2 and all(speeds)


def test_taper_leaves_ends_at_the_grid_edge_full_width():
    band = CFG.band.model_copy(update={"taper_deg": 4, "taper_min_fraction": 0.25})
    chain = [(lon, 40.0, 30.0) for lon in range(-170, -150)]
    edges = [(lon, 37.0, 43.0) for lon in range(-170, -150)]
    out = js.taper(chain, edges, band, (-170, -50))
    assert out[0] == (-170, 37.0, 43.0)          # the data stop here: no taper
    assert out[-1][1] == pytest.approx(39.25)    # the jet fades here


def test_strength_groups_compare_with_every_member():
    split = CFG.split.model_copy(update={"min_seasons": 1, "min_mean_shift_deg": 2.0})
    lat_of = {"w": 40.0, "m": 38.2, "s": 36.4}   # 1.8 apart step by step, 3.6 end to end
    groups, _ = js.strength_groups({"weak": ["w"], "moderate": ["m"], "strong": ["s"]},
                                   lambda seasons: _axis(sum(lat_of[x] for x in seasons) / len(seasons)), split)
    assert groups == [["weak", "moderate"], ["strong"]]


def test_check_extract_warns_on_short_or_gappy_extracts():
    full = {"seasons": [{"season": js.season_label(y)} for y in (1990, 1991, 1992)]}
    assert js.check_extract(full, CFG) == []
    short = {"seasons": [{"season": "1991/92"}]}
    assert any("fewer than baseline_seasons" in w for w in js.check_extract(short, CFG))
    gap = {"seasons": [{"season": js.season_label(y)} for y in (1990, 1992)]}
    assert js.check_extract(gap, CFG) == ["jet stream: the ERA5 extract misses seasons 1991/92"]


def test_band_warnings_flag_a_band_drawn_by_the_cap():
    capped = {"layers": [{"id": "x", "branches": [{"axis": [[-100, 40.0, 30]] * 4, "band": [[-100, 34.0, 46.0]] * 4}]}]}
    assert js.band_warnings(capped, CFG) == ["jet stream: the x band sits at max_half_width_deg (6.0) for most points"]
    free = {"layers": [{"id": "x", "branches": [{"axis": [[-100, 40.0, 30]] * 4, "band": [[-100, 37.0, 43.5]] * 4}]}]}
    assert js.band_warnings(free, CFG) == []



def test_name_branches_by_where_they_run():
    def br(bid, lat):
        return {"id": bid, "axis": [[lon, lat, 30] for lon in range(-130, -100)]}
    named = {b["id"]: b for b in js.name_branches([br("main", 30.0), br("north", 45.0), br("north_2", 55.0)])}
    assert named["main"]["label"] == "Southern branch" and named["main"]["fastest"] is True
    assert named["north"]["label"] == "Northern branch" and named["north_2"]["label"] == "Far northern branch"
    alone = js.name_branches([br("main", 40.0)])
    assert alone[0]["label"] == "Jet stream"
    both = {b["id"]: b["label"] for b in js.name_branches([br("main", 38.0), br("north", 48.0), br("south", 26.0)])}
    assert both == {"main": "Middle branch", "north": "Northern branch", "south": "Southern branch"}


def test_current_reading_names_its_months_and_layer():
    layers = [{"id": "el_nino_weak", "phase": "el_nino", "strengths": ["weak"]},
              {"id": "el_nino_moderate_strong", "phase": "el_nino", "strengths": ["moderate", "strong"]},
              {"id": "la_nina", "phase": "la_nina"}, {"id": "neutral", "phase": "neutral"}]
    now = js.current_reading([("JAS", 2026, 1.69)], CFG.enso, layers, {"source_id": "roni"})
    assert now["months"] == "July to September 2026" and now["phase"] == "el_nino" and now["strength"] == "strong"
    assert now["layer"] == "el_nino_moderate_strong" and now["preliminary"] is True and now["source_id"] == "roni"
    djf = js.current_reading([("DJF", 2026, -0.91)], CFG.enso, layers, {})
    assert djf["months"] == "December 2025 to February 2026" and djf["layer"] == "la_nina"
    assert js.current_reading([("OND", 2025, 0.2)], CFG.enso, layers, {})["layer"] == "neutral"
