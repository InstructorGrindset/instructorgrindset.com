import json

import pytest

from pipeline.config import RoutingDefs
from pipeline.routing import (
    RoutingClient, decode_polyline, parse_ors_isochrone, parse_ors_route, parse_osrm_route, parse_valhalla_isochrone,
    parse_valhalla_route,
)

LINE = [[-106.2, 39.7], [-106.25, 39.65], [-106.300001, 39.6]]
POLY = {"type": "Polygon", "coordinates": [[[-106.4, 39.5], [-106.2, 39.5], [-106.2, 39.7], [-106.4, 39.5]]]}


def valhalla_route(encoder):
    return {"trip": {"summary": {"time": 1260, "length": 12.345},
                     "legs": [{"shape": encoder(LINE[:2])}, {"shape": encoder(LINE[1:])}]}}


def test_decode_polyline(polyline_encoder):
    assert decode_polyline(polyline_encoder(LINE)) == LINE
    assert decode_polyline(polyline_encoder([[-120.2, 38.5]], 5), 5) == [[-120.2, 38.5]]
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5) == [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]


def test_parse_routes(polyline_encoder):
    r = parse_valhalla_route(valhalla_route(polyline_encoder))
    assert r == {"minutes": 21.0, "distance_mi": 12.35, "geometry": {"type": "LineString", "coordinates": LINE}}
    osrm = {"code": "Ok", "routes": [{"duration": 600, "distance": 16093.44, "geometry": {"type": "LineString", "coordinates": LINE}}]}
    assert parse_osrm_route(osrm) == {"minutes": 10.0, "distance_mi": 10.0, "geometry": {"type": "LineString", "coordinates": LINE}}
    with pytest.raises(ValueError):
        parse_osrm_route({"code": "NoRoute", "routes": []})
    ors = {"features": [{"properties": {"summary": {"duration": 90, "distance": 1609.344}}, "geometry": {"type": "LineString", "coordinates": LINE}}]}
    assert parse_ors_route(ors)["minutes"] == 1.5 and parse_ors_route(ors)["distance_mi"] == 1.0


def test_parse_isochrones():
    v = {"type": "FeatureCollection", "features": [
        {"properties": {"contour": 30}, "geometry": POLY}, {"properties": {"contour": 15}, "geometry": POLY},
        {"properties": {"contour": 45}, "geometry": {"type": "LineString", "coordinates": LINE}}]}
    assert [i["minutes"] for i in parse_valhalla_isochrone(v)] == [15, 30]
    o = {"features": [{"properties": {"value": 2700}, "geometry": POLY}, {"properties": {"value": 900}, "geometry": POLY}]}
    assert [i["minutes"] for i in parse_ors_isochrone(o)] == [15, 45]


def defs(provider="valhalla", **kw):
    base = {"valhalla": "https://valhalla.example.org", "osrm": "https://osrm.example.org", "ors": "https://ors.example.org"}
    return RoutingDefs(provider=provider, base_url=base[provider], isochrone_minutes=[15, 30, 45], **kw)


def test_client_cache_offline_online(tmp_path, polyline_encoder):
    calls = []

    def http(method, url, body, headers, timeout):
        calls.append((method, url, body, headers))
        if url.endswith("/route"):
            return valhalla_route(polyline_encoder)
        return {"features": [{"properties": {"contour": m}, "geometry": POLY} for m in (15, 30, 45)]}
    offline = RoutingClient(defs(), cache_dir=tmp_path, offline=True, http=http)
    assert offline.route((39.7, -106.2), (39.6, -106.3)) is None and offline.isochrones((39.6, -106.3)) == []
    assert calls == [] and offline.source() is None
    online = RoutingClient(defs(), cache_dir=tmp_path, offline=False, http=http)
    r = online.route((39.7, -106.2), (39.6, -106.3))
    assert r["minutes"] == 21.0 and calls[0][2]["costing"] == "auto"
    assert [i["minutes"] for i in online.isochrones((39.6, -106.3))] == [15, 30, 45]
    assert calls[1][2]["contours"] == [{"time": 15}, {"time": 30}, {"time": 45}] and calls[1][2]["polygons"] is True
    cached = RoutingClient(defs(), cache_dir=tmp_path, offline=True, http=http)
    assert cached.route((39.7, -106.2), (39.6, -106.3)) == r and len(calls) == 2
    src = cached.source()
    assert src.id == "routing-valhalla" and src.url == "https://valhalla.example.org"
    files = list((tmp_path / "valhalla").glob("*.json"))
    assert len(files) == 2 and "fetched_date" in json.loads(files[0].read_text())


def test_client_osrm_and_ors(tmp_path, monkeypatch):
    seen = []

    def http(method, url, body, headers, timeout):
        seen.append((method, url, body, headers))
        if "osrm" in url:
            return {"code": "Ok", "routes": [{"duration": 60, "distance": 1609.344, "geometry": {"type": "LineString", "coordinates": LINE}}]}
        if "isochrones" in url:
            return {"features": [{"properties": {"value": 900}, "geometry": POLY}]}
        return {"features": [{"properties": {"summary": {"duration": 60, "distance": 1609.344}}, "geometry": {"type": "LineString", "coordinates": LINE}}]}
    osrm = RoutingClient(defs("osrm"), cache_dir=tmp_path, offline=False, http=http)
    assert osrm.route((39.7, -106.2), (39.6, -106.3))["distance_mi"] == 1.0
    assert seen[0][1] == "https://osrm.example.org/route/v1/driving/-106.2,39.7;-106.3,39.6?overview=full&geometries=geojson"
    assert osrm.isochrones((39.6, -106.3)) == []  # OSRM has no isochrones
    monkeypatch.setenv("ORS_KEY", "k")
    ors = RoutingClient(defs("ors", api_key_env="ORS_KEY"), cache_dir=tmp_path, offline=False, http=http)
    ors.route((39.7, -106.2), (39.6, -106.3))
    assert seen[-1][2] == {"coordinates": [[-106.2, 39.7], [-106.3, 39.6]]} and seen[-1][3] == {"Authorization": "k"}
    assert [i["minutes"] for i in ors.isochrones((39.6, -106.3))] == [15]
    assert seen[-1][2]["range"] == [900, 1800, 2700]
