"""Drive routing and isochrones (Valhalla, OSRM or OpenRouteService) with a response cache.

    python -m pipeline.routing run      # fetch drive routes housing -> base and base isochrones into the cache

The build reads only the cache (data/raw/routing/<provider>/<sha1>.json, gitignored),
so it never calls a routing server. Provider settings live in config/definitions.yaml
``routing``. APIs:

* Valhalla  POST {base}/route      {"locations": [{lat, lon}, {lat, lon}], "costing": "auto", "units": "miles"}
            -> trip.summary.time (s), trip.summary.length (mi), trip.legs[].shape (polyline, 6 decimals)
            POST {base}/isochrone  {"locations": [{lat, lon}], "costing": "auto", "contours": [{"time": m}], "polygons": true}
            -> GeoJSON FeatureCollection, properties.contour (minutes)
* OSRM      GET  {base}/route/v1/driving/{lon},{lat};{lon},{lat}?overview=full&geometries=geojson
            -> routes[0].duration (s), routes[0].distance (m), routes[0].geometry (LineString). No isochrones.
* ORS       POST {base}/v2/directions/driving-car/geojson {"coordinates": [[lon, lat], [lon, lat]]}
            -> features[0].properties.summary.duration (s) / distance (m), geometry
            POST {base}/v2/isochrones/driving-car {"locations": [[lon, lat]], "range": [seconds...]}
            -> features[].properties.value (s), geometry. Needs an API key (routing.api_key_env).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.config import RoutingDefs  # noqa: E402
from pipeline.schemas.common import Source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "raw" / "routing"
M_PER_MI = 1609.344
TITLES = {"valhalla": "Valhalla routing on OpenStreetMap data", "osrm": "OSRM routing on OpenStreetMap data",
          "ors": "openrouteservice routing on OpenStreetMap data"}

Http = Callable[[str, str, Optional[dict], dict, float], Any]  # (method, url, json body, headers, timeout) -> json


def decode_polyline(encoded: str, precision: int = 6) -> list[list[float]]:
    """Google encoded polyline (Valhalla uses 6 decimals) to [[lon, lat], ...]."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append([round(lon / factor, 6), round(lat / factor, 6)])
    return coords


# --------------------------------------------------------------- parsers
def parse_valhalla_route(resp: dict) -> dict:
    trip = resp["trip"]
    line: list[list[float]] = []
    for leg in trip.get("legs") or []:
        pts = decode_polyline(leg["shape"], 6)
        line += pts if not line else pts[1:]
    return {"minutes": round(trip["summary"]["time"] / 60, 1), "distance_mi": round(trip["summary"]["length"], 2),
            "geometry": {"type": "LineString", "coordinates": line}}


def parse_osrm_route(resp: dict) -> dict:
    if resp.get("code") not in (None, "Ok") or not resp.get("routes"):
        raise ValueError(f"OSRM returned {resp.get('code')}")
    r = resp["routes"][0]
    return {"minutes": round(r["duration"] / 60, 1), "distance_mi": round(r["distance"] / M_PER_MI, 2),
            "geometry": r["geometry"]}


def parse_ors_route(resp: dict) -> dict:
    f = resp["features"][0]
    s = f["properties"]["summary"]
    return {"minutes": round(s["duration"] / 60, 1), "distance_mi": round(s["distance"] / M_PER_MI, 2),
            "geometry": f["geometry"]}


def parse_valhalla_isochrone(resp: dict) -> list[dict]:
    out = [{"minutes": int(round(f["properties"]["contour"])), "geometry": f["geometry"]}
           for f in resp.get("features", []) if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
    return sorted(out, key=lambda i: i["minutes"])


def parse_ors_isochrone(resp: dict) -> list[dict]:
    out = [{"minutes": int(round(f["properties"]["value"] / 60)), "geometry": f["geometry"]}
           for f in resp.get("features", []) if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
    return sorted(out, key=lambda i: i["minutes"])


# ---------------------------------------------------------------- client
def _http(method: str, url: str, body: Optional[dict], headers: dict, timeout: float) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": "instructorgrindset-career-map/1", "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class RoutingClient:
    """Cached client. ``offline=True`` (the build) answers only from the cache."""

    def __init__(self, defs: RoutingDefs, cache_dir: Path = CACHE_DIR, offline: bool = True, http: Http = _http):
        self.defs = defs
        self.cache = cache_dir / defs.provider
        self.offline = offline
        self.http = http
        self.fetched_dates: list[dt.date] = []

    @property
    def source_id(self) -> str:
        return f"routing-{self.defs.provider}"

    def source(self) -> Optional[Source]:
        if not self.fetched_dates:
            return None
        return Source(id=self.source_id, url=self.defs.base_url, title=TITLES[self.defs.provider],
                      accessed_date=max(self.fetched_dates))

    def _headers(self) -> dict:
        key = os.environ.get(self.defs.api_key_env) if self.defs.api_key_env else None
        return {"Authorization": key} if key else {}

    def _call(self, method: str, path: str, body: Optional[dict]) -> Optional[Any]:
        url = self.defs.base_url.rstrip("/") + path
        key = hashlib.sha1(json.dumps([method, url, body], sort_keys=True).encode()).hexdigest()
        cpath = self.cache / f"{key}.json"
        if cpath.exists():
            cached = json.loads(cpath.read_text(encoding="utf-8"))
            self.fetched_dates.append(dt.date.fromisoformat(cached["fetched_date"]))
            return cached["response"]
        if self.offline:
            return None
        resp = self.http(method, url, body, self._headers(), self.defs.timeout_s)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        today = dt.date.today()
        cpath.write_text(json.dumps({"fetched_date": today.isoformat(), "url": url, "request": body,
                                     "response": resp}) + "\n", encoding="utf-8")
        self.fetched_dates.append(today)
        return resp

    def route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Optional[dict]:
        """Drive route between (lat, lon) points: {minutes, distance_mi, geometry} or None."""
        p = self.defs.provider
        if p == "valhalla":
            resp = self._call("POST", "/route", {"locations": [{"lat": origin[0], "lon": origin[1]},
                                                               {"lat": dest[0], "lon": dest[1]}],
                                                 "costing": "auto", "units": "miles"})
            return parse_valhalla_route(resp) if resp else None
        if p == "osrm":
            path = f"/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}?overview=full&geometries=geojson"
            resp = self._call("GET", path, None)
            return parse_osrm_route(resp) if resp else None
        resp = self._call("POST", "/v2/directions/driving-car/geojson",
                          {"coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]]})
        return parse_ors_route(resp) if resp else None

    def isochrones(self, point: tuple[float, float]) -> list[dict]:
        """Drive isochrones at routing.isochrone_minutes around (lat, lon). OSRM has none."""
        minutes = list(self.defs.isochrone_minutes)
        if not minutes or self.defs.provider == "osrm":
            return []
        if self.defs.provider == "valhalla":
            resp = self._call("POST", "/isochrone", {"locations": [{"lat": point[0], "lon": point[1]}], "costing": "auto",
                                                     "contours": [{"time": m} for m in minutes], "polygons": True})
            return parse_valhalla_isochrone(resp) if resp else []
        resp = self._call("POST", "/v2/isochrones/driving-car",
                          {"locations": [[point[1], point[0]]], "range": [m * 60 for m in minutes]})
        return parse_ors_isochrone(resp) if resp else []


def main() -> int:
    """Fetch routes from every housing site to its base area and base area isochrones for Tier A resorts."""
    import yaml

    from pipeline.config import load_config

    defs = load_config().definitions.routing
    client = RoutingClient(defs, offline=False)
    resorts = {r["id"]: r for r in json.loads((REPO_ROOT / "data" / "build" / "resorts.json").read_text())["resorts"]
               if r["tier"] == "A" and r.get("detail_available")}
    sites = []
    for p in sorted((REPO_ROOT / "data" / "raw" / "housing").glob("*.yaml")):
        sites += (yaml.safe_load(p.read_text()) or {}).get("sites") or []
    failures = 0
    jobs = [("iso", rid, None) for rid in sorted(resorts)] + [("route", s["resort_id"], s) for s in sites
                                                               if s["resort_id"] in resorts]
    for kind, rid, site in jobs:
        base = (resorts[rid]["lat"], resorts[rid]["lon"])
        try:
            if kind == "iso":
                client.isochrones(base)
            else:
                client.route((float(site["lat"]), float(site["lon"])), base)
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"{kind} {rid}: {type(e).__name__}: {e}")
    print(f"{len(jobs) - failures} of {len(jobs)} routing requests cached in {client.cache}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
