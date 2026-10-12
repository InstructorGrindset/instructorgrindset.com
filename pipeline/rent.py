"""Rent baselines: HUD Fair Market Rents and Census ACS median gross rent (B25064).

The county comes from the resort base point (point in polygon against
data/raw/geo/us-counties.geojson, built by scripts/build-counties.mjs from
us-atlas). Responses are cached under data/raw/rent/ and the build reads only
the cache, so it never needs the network.

    python -m pipeline.rent fetch        # needs HUD_API_TOKEN for HUD; ACS needs no key

Cache layout (data/raw/rent/):
    hud_fmr/<county fips>.json      {fetched_date, url, response}   HUD FMR API, data/{fips}99999
    hud_fmr_<year>.csv              optional HUD county FMR file (columns fips, countyname, fmr_0, fmr_1, fmr_2)
    hud_fmr_<year>.csv.json         optional sidecar {fetched_date}; without it as_of is Oct 1 of <year> - 1
    acs/<year>_county_<fips>.json   {fetched_date, url, response}   Census API B25064_001E
    acs/<year>_zcta_<zip>.json      same, ZIP code tabulation area (parsed; ZIP lookup needs ZCTA outlines)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.geo import point_in_geometry  # noqa: E402
from pipeline.schemas.common import Source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COUNTIES_PATH = REPO_ROOT / "data" / "raw" / "geo" / "us-counties.geojson"
HUD_URL = "https://www.huduser.gov/hudapi/public/fmr/data/{entity}"
ACS_URL = "https://api.census.gov/data/{year}/{dataset}"
HUD_TOKEN_ENV = "HUD_API_TOKEN"
ACS_NULL_SENTINELS = {-666666666, -999999999, -888888888, -222222222, -333333333, -555555555}


# ------------------------------------------------------------------ counties
@dataclass
class County:
    fips: str
    name: str
    state: Optional[str]
    geometry: dict
    bbox: tuple[float, float, float, float]


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c: Any) -> None:
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geometry["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def load_counties(path: Path = COUNTIES_PATH) -> list[County]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [County(f["properties"]["fips"], f["properties"]["name"], f["properties"].get("state"),
                   f["geometry"], _bbox(f["geometry"])) for f in data["features"]]


def county_for(lat: float, lon: float, counties: Sequence[County]) -> Optional[County]:
    """The county whose outline contains the point, or None."""
    for c in counties:
        x0, y0, x1, y1 = c.bbox
        if x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_geometry(lon, lat, c.geometry):
            return c
    return None


# ------------------------------------------------------------------- parsing
def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if int(f) in ACS_NULL_SENTINELS or f < 0 else f


def parse_hud_fmr(response: dict) -> dict:
    """HUD FMR API response -> {county_name, year, studio, one_br, two_br, zips: {zip: {...}}}.

    ``data.basicdata`` is one object for a county, or a list with one row per ZIP
    (Small Area FMR areas) plus an "MSA level" row.
    """
    data = response.get("data") or {}
    basic = data.get("basicdata")
    rows = basic if isinstance(basic, list) else [basic] if isinstance(basic, dict) else []

    def unpack(row: dict) -> dict:
        return {"studio": _num(row.get("Efficiency")), "one_br": _num(row.get("One-Bedroom")),
                "two_br": _num(row.get("Two-Bedroom"))}
    area = next((r for r in rows if str(r.get("zip_code", "")).lower() in ("msa level", "")), rows[0] if rows else {})
    year = next((r.get("year") for r in rows if r.get("year")), None) or data.get("year")
    zips = {str(r["zip_code"]): unpack(r) for r in rows
            if str(r.get("zip_code", "")).isdigit() and len(str(r.get("zip_code"))) == 5}
    return {"county_name": data.get("county_name") or data.get("area_name"), "year": int(year) if year else None,
            **unpack(area or {}), "zips": zips}


def parse_hud_fmr_csv(path: Path) -> dict[str, dict]:
    """HUD county FMR CSV -> {county fips: {county_name, studio, one_br, two_br}}. The fips column may be
    5 digits or the 10 digit HUD entity id (state, county, 99999)."""
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            row = {k.strip().lower(): v for k, v in row.items() if k}
            fips = (row.get("fips") or row.get("fips2010") or "").strip()
            if len(fips) not in (5, 10) or not fips.isdigit():
                continue
            out[fips[:5]] = {"county_name": row.get("countyname") or row.get("county_name"),
                             "studio": _num(row.get("fmr_0")), "one_br": _num(row.get("fmr_1")),
                             "two_br": _num(row.get("fmr_2"))}
    return out


def parse_acs(response: list) -> dict[str, Optional[float]]:
    """Census API table -> {geography id: B25064_001E}. Geography id is the 5 digit county fips
    (state + county) or the 5 digit ZCTA. Census null sentinels become None."""
    if not response or not isinstance(response, list):
        return {}
    header = [h.lower() for h in response[0]]
    col = header.index("b25064_001e")
    out: dict[str, Optional[float]] = {}
    for row in response[1:]:
        rec = dict(zip(header, row))
        if "zip code tabulation area" in rec:
            gid = rec["zip code tabulation area"]
        else:
            gid = f"{rec.get('state', '')}{rec.get('county', '')}"
        out[gid] = _num(row[col])
    return out


# ------------------------------------------------------------------- sources
def hud_source(year: int, fetched: dt.date) -> Source:
    return Source(id=f"hud-fmr-fy{year}", url="https://www.huduser.gov/portal/datasets/fmr.html",
                  title=f"HUD Fair Market Rents, FY{year}", accessed_date=fetched)


def acs_source(year: int, fetched: dt.date, dataset: str = "acs/acs5") -> Source:
    return Source(id=f"census-{dataset.replace('/', '-').replace('acs-acs', 'acs')}-{year}-b25064",
                  url=ACS_URL.format(year=year, dataset=dataset),
                  title=f"US Census Bureau ACS 5 year estimates {year}, B25064 median gross rent",
                  accessed_date=fetched)


# ------------------------------------------------------------------ baseline
def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def hud_csv_date(csv_path: Path, year: int) -> dt.date:
    """as_of for a HUD FMR CSV: ``fetched_date`` from the sidecar ``<file>.json`` when present, else the date
    the FY<year> FMRs take effect (October 1 of the previous calendar year). Never the file mtime, which
    every checkout resets."""
    sidecar = csv_path.with_name(csv_path.name + ".json")
    meta = _read(sidecar)
    if meta and meta.get("fetched_date"):
        return dt.date.fromisoformat(meta["fetched_date"])
    return dt.date(year - 1, 10, 1)


def baseline_for(county: County, rent_dir: Path, acs_year: int, acs_dataset: str = "acs/acs5"
                 ) -> tuple[Optional[dict], list[Source]]:
    """RentBaseline dict for a county from the cache, plus the sources it cites; (None, []) when no data."""
    sources: list[Source] = []
    fmr: dict[str, Optional[dict]] = {"studio": None, "one_br": None, "two_br": None}
    name = county.name
    cached = _read(rent_dir / "hud_fmr" / f"{county.fips}.json")
    hud = None
    if cached and cached.get("response"):
        parsed = parse_hud_fmr(cached["response"])
        if parsed["year"]:
            hud = (parsed, int(parsed["year"]), dt.date.fromisoformat(cached["fetched_date"]))
    if hud is None:
        for csv_path in sorted(rent_dir.glob("hud_fmr_*.csv"), reverse=True):
            rows = parse_hud_fmr_csv(csv_path)
            if county.fips in rows:
                year = int(csv_path.stem.split("_")[-1])
                hud = (rows[county.fips], year, hud_csv_date(csv_path, year))
                break
    if hud is not None:
        parsed, year, fetched = hud
        src = hud_source(year, fetched)
        for key in fmr:
            if parsed.get(key) is not None:
                fmr[key] = {"value": parsed[key], "source_id": src.id, "as_of": fetched.isoformat(),
                            "confidence": "published", "note": f"HUD FMR FY{year}, {county.name} County"}
        if any(fmr.values()):
            sources.append(src)
        name = parsed.get("county_name") or name
    acs = None
    cached = _read(rent_dir / "acs" / f"{acs_year}_county_{county.fips}.json")
    if cached and cached.get("response"):
        value = parse_acs(cached["response"]).get(county.fips)
        if value is not None:
            src = acs_source(acs_year, dt.date.fromisoformat(cached["fetched_date"]), acs_dataset)
            sources.append(src)
            acs = {"value": value, "source_id": src.id, "as_of": cached["fetched_date"], "confidence": "published",
                   "note": f"ACS {acs_year} 5 year median gross rent, {county.name} County"}
    if not sources:
        return None, []
    return {"county_fips": county.fips, "county_name": name, "fmr": fmr, "acs_median_gross_rent": acs}, sources


BASELINE_FIELDS = {"fmr_studio": ("fmr", "studio"), "fmr_one_br": ("fmr", "one_br"),
                   "fmr_two_br": ("fmr", "two_br"), "acs_median": (None, "acs_median_gross_rent")}


def baseline_value(baseline: Optional[dict], preference: Iterable[str]) -> Optional[tuple[str, dict]]:
    """(key, Sourced dict) of the first available baseline value in ``preference``."""
    if not baseline:
        return None
    for key in preference:
        group, field = BASELINE_FIELDS[key]
        s = (baseline.get(group) or {}).get(field) if group else baseline.get(field)
        if s is not None:
            return key, s
    return None


# --------------------------------------------------------------------- fetch
def _get(url: str, headers: dict, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "instructorgrindset-career-map/1", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_counties(fips_list: Iterable[str], rent_dir: Path, acs_year: int, hud_year: Optional[int] = None,
                   acs_dataset: str = "acs/acs5", getter=_get) -> list[str]:
    """Fetch HUD FMR and ACS for each county into the cache. Returns messages; never raises on network errors."""
    msgs: list[str] = []
    token = os.environ.get(HUD_TOKEN_ENV)
    today = dt.date.today().isoformat()
    for fips in sorted(set(fips_list)):
        if token:
            url = HUD_URL.format(entity=f"{fips}99999") + (f"?year={hud_year}" if hud_year else "")
            try:
                resp = getter(url, {"Authorization": f"Bearer {token}"})
                _write(rent_dir / "hud_fmr" / f"{fips}.json", {"fetched_date": today, "url": url, "response": resp})
            except Exception as e:  # noqa: BLE001 - report and continue
                msgs.append(f"HUD FMR {fips}: {type(e).__name__}: {e}")
        q = urllib.parse.urlencode({"get": "NAME,B25064_001E", "for": f"county:{fips[2:]}", "in": f"state:{fips[:2]}"})
        url = ACS_URL.format(year=acs_year, dataset=acs_dataset) + "?" + q
        try:
            resp = getter(url, {})
            _write(rent_dir / "acs" / f"{acs_year}_county_{fips}.json", {"fetched_date": today, "url": url, "response": resp})
        except Exception as e:  # noqa: BLE001
            msgs.append(f"ACS {fips}: {type(e).__name__}: {e}")
    if not token:
        msgs.insert(0, f"HUD FMR skipped: set {HUD_TOKEN_ENV} (free token from https://www.huduser.gov/hudapi/public/register)")
    return msgs


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    from pipeline.config import load_config

    ap = argparse.ArgumentParser(description="Fetch HUD FMR and ACS rent baselines for Tier A resort counties.")
    ap.add_argument("command", choices=["fetch"])
    args = ap.parse_args(argv)
    del args
    defs = load_config().definitions.rent
    resorts = json.loads((REPO_ROOT / "data" / "build" / "resorts.json").read_text())["resorts"]
    counties = load_counties()
    fips = []
    for r in resorts:
        if r["tier"] == "A":
            c = county_for(r["lat"], r["lon"], counties)
            if c:
                fips.append(c.fips)
    msgs = fetch_counties(fips, REPO_ROOT / "data" / "raw" / "rent", defs.acs_year, defs.hud_fmr_year, defs.acs_dataset)
    for m in msgs:
        print(m)
    print(f"{len(set(fips))} counties processed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
