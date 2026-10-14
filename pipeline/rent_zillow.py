"""Zillow Observed Rent Index (ZORI), Zillow Research: fetch, parse, extract and match.

Series: "ZORI (Smoothed): All Homes Plus Multifamily Time Series ($)" on https://www.zillow.com/research/data/
(section Rentals). One CSV per geography, one row per region, one column per month (the month end date):

    County_zori_uc_sfrcondomfr_sm_month.csv   RegionID, SizeRank, RegionName, RegionType, StateName, State, Metro,
                                              StateCodeFIPS, MunicipalCodeFIPS, <YYYY-MM-DD> ...
    Zip_zori_uc_sfrcondomfr_sm_month.csv      RegionID, SizeRank, RegionName (the ZIP), RegionType, StateName, State,
                                              City, Metro, CountyName, <YYYY-MM-DD> ...
    Metro_zori_uc_sfrcondomfr_sm_month.csv    RegionID, SizeRank, RegionName, RegionType, StateName, <YYYY-MM-DD> ...

The full files are large and stay out of git. ``python -m pipeline.rent_zillow fetch`` downloads them into
data/raw/rent/zillow/download/; files dropped by hand into data/raw/rent/zillow/ work the same. The build (or
``python -m pipeline.rent_zillow extract``) keeps only the rows it needs in the committed
data/raw/rent/zillow/zori_extract.csv: counties with a resort, the metros of those counties, and ZIPs whose
centroid lies within ``rent.zori.zip_radius_km`` of a Tier A base (ZIP centroids come from the Census Gazetteer
ZCTA file placed in the same folder; without it ZIP rows are skipped). The build reads only the extract.

Per region: the latest month with a value is the displayed rent (``as_of`` = that month end), and the trailing
mean over ``rent.zori.trailing_months`` months ending there is kept for reference (null unless every month in the
window has a value). Values are never interpolated. A resort matches its nearest ZIP within the radius, else its
county (FIPS), else its county's metro, else nothing.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.schemas.common import Source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
ZILLOW_DIR = REPO_ROOT / "data" / "raw" / "rent" / "zillow"
DOWNLOAD_SUBDIR = "download"
EXTRACT_NAME = "zori_extract.csv"

HOST = "files.zillowstatic.com"
BASE_URL = f"https://{HOST}/research/public_csvs/zori/"
RESEARCH_PAGE = "https://www.zillow.com/research/data/"
FILES = {
    "county": "County_zori_uc_sfrcondomfr_sm_month.csv",
    "zip": "Zip_zori_uc_sfrcondomfr_sm_month.csv",
    "metro": "Metro_zori_uc_sfrcondomfr_sm_month.csv",
}
LEVELS = ("zip", "county", "metro")
# A generic User-Agent; never an email address or any personal detail.
USER_AGENT = "Mozilla/5.0 (compatible; rent-data-fetch/1.0)"

SOURCE_ID = "zillow-zori"
SOURCE_TITLE = "Zillow Observed Rent Index (ZORI), Zillow Research"
ZORI_NOTE = "ZORI is a typical market rent for all homes and multifamily, smoothed"
GAZETTEER_GLOB = "*Gaz_zcta_national*.txt"

DATE_COL = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
EXTRACT_COLUMNS = ("level", "region_id", "region_name", "state", "county_fips", "county_name", "city", "metro", "lat",
                   "lon", "latest_month", "latest_value", "trailing_mean", "trailing_months", "source_file",
                   "fetched_date")


class ZoriFormatError(ValueError):
    """A ZORI CSV whose header lacks the columns this module reads."""


# -------------------------------------------------------------------- parsing
@dataclass
class ZoriRow:
    level: str                       # zip | county | metro
    region_id: str
    region_name: str                 # "80424", "Summit County", "Breckenridge, CO"
    state: Optional[str] = None
    county_fips: Optional[str] = None   # county rows: 5 digits; metro rows in the extract: the linked resort counties
    county_name: Optional[str] = None   # ZIP rows: CountyName
    city: Optional[str] = None          # ZIP rows: City
    metro: Optional[str] = None
    lat: Optional[float] = None         # ZIP rows in the extract: Census Gazetteer ZCTA centroid
    lon: Optional[float] = None
    latest_month: Optional[dt.date] = None
    latest_value: Optional[float] = None
    trailing_mean: Optional[float] = None
    trailing_months: int = 0            # months with a value inside the trailing window
    source_file: Optional[str] = None
    fetched_date: Optional[dt.date] = None

    @property
    def county_fips_list(self) -> list[str]:
        return (self.county_fips or "").split()


def month_end(y: int, m: int) -> dt.date:
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def parse_month(col: str) -> Optional[dt.date]:
    """A ZORI date column ("2026-08-31", or "2026-08") as its month end date; None for other columns."""
    m = DATE_COL.match(col.strip())
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return month_end(y, mo) if 1 <= mo <= 12 else None


def month_label(d: dt.date) -> str:
    """'Aug 2026'."""
    return f"{MONTHS[d.month - 1]} {d.year}"


def _value(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) and f > 0 else None


def series_summary(months: Sequence[tuple[dt.date, Optional[float]]], trailing: int = 12
                   ) -> tuple[Optional[dt.date], Optional[float], Optional[float], int]:
    """(latest month with a value, its value, trailing mean, months with a value in the window).

    The window is the ``trailing`` calendar months ending at the latest month with a value. The mean is null unless
    every month in it has a value, so it is always a true trailing ``trailing`` month mean."""
    ordered = sorted(months, key=lambda mv: mv[0])
    latest = next(((d, v) for d, v in reversed(ordered) if v is not None), None)
    if latest is None:
        return None, None, None, 0
    d0, v0 = latest
    first = d0.year * 12 + d0.month - trailing + 1
    window = [v for d, v in ordered if first <= d.year * 12 + d.month <= d0.year * 12 + d0.month and v is not None]
    mean = sum(window) / len(window) if len(window) == trailing else None
    return d0, v0, mean, len(window)


def _norm_header(cols: Sequence[str]) -> list[str]:
    return [c.replace("﻿", "").strip() for c in cols]


def _get(rec: dict, *names: str) -> Optional[str]:
    for n in names:
        v = rec.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _fips(state: Optional[str], county: Optional[str]) -> Optional[str]:
    if not state or not county:
        return None
    s, c = state.split(".")[0], county.split(".")[0]   # "8" / "117", or "8.0" when a sheet program touched it
    if not (s.isdigit() and c.isdigit()) or len(s) > 2 or len(c) > 3:
        return None
    return s.zfill(2) + c.zfill(3)


def parse_zori_csv(path: Path, level: str, trailing: int = 12) -> Iterator[ZoriRow]:
    """Rows of one ZORI file. Raises ZoriFormatError when the header has no RegionID, RegionName or month columns."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = _norm_header(next(reader))
        except StopIteration:
            raise ZoriFormatError(f"{path.name}: empty file") from None
        months = [(i, parse_month(c)) for i, c in enumerate(header)]
        months = [(i, d) for i, d in months if d is not None]
        missing = [c for c in ("RegionID", "RegionName") if c not in header]
        if missing or not months:
            raise ZoriFormatError(
                f"{path.name}: expected RegionID, RegionName and monthly YYYY-MM-DD columns; "
                f"missing {missing or 'month columns'}; header starts {header[:10]}")
        idx = {c: i for i, c in enumerate(header) if parse_month(c) is None}
        for raw in reader:
            if not raw or not any(x.strip() for x in raw):
                continue
            rec = {c: (raw[i] if i < len(raw) else "") for c, i in idx.items()}
            if (_get(rec, "RegionType") or "").lower() == "country":
                continue
            name = _get(rec, "RegionName")
            if name is None:
                continue
            if level == "zip" and name.split(".")[0].isdigit():
                name = name.split(".")[0].zfill(5)
            latest, value, mean, n = series_summary(
                [(d, _value(raw[i] if i < len(raw) else None)) for i, d in months], trailing)
            state = _get(rec, "State", "StateName")
            yield ZoriRow(
                level=level, region_id=_get(rec, "RegionID") or name, region_name=name,
                state=state.upper() if state and len(state) == 2 else state,
                county_fips=_fips(_get(rec, "StateCodeFIPS"), _get(rec, "MunicipalCodeFIPS")) if level == "county" else None,
                county_name=_get(rec, "CountyName") if level == "zip" else (name if level == "county" else None),
                city=_get(rec, "City") if level == "zip" else None,
                metro=_get(rec, "Metro") if level != "metro" else None,
                latest_month=latest, latest_value=value, trailing_mean=mean, trailing_months=n,
                source_file=path.name)


# ----------------------------------------------------------------- geography
COUNTY_SUFFIXES = (" city and borough", " census area", " municipality", " borough", " parish", " county", " city")


def norm_county(name: Optional[str]) -> str:
    """'Summit County' and 'Summit' both give 'summit'."""
    s = re.sub(r"\s+", " ", (name or "").strip().lower().replace(".", ""))
    for suf in COUNTY_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)].strip()
    return s


def metro_key(name: Optional[str]) -> tuple[str, str]:
    """(principal city, first state) of a metro name: 'Denver-Aurora-Lakewood, CO' and 'Denver, CO' match."""
    s = (name or "").strip().lower()
    city, _, states = s.partition(",")
    return city.split("-")[0].strip(), states.strip().split("-")[0].strip()[:2]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def load_gazetteer(path: Path) -> dict[str, tuple[float, float]]:
    """Census Gazetteer ZCTA file (tab separated GEOID ... INTPTLAT INTPTLONG) -> {zip: (lat, lon)}."""
    out: dict[str, tuple[float, float]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = [h.strip().upper() for h in next(reader, [])]
        try:
            gi, la, lo = header.index("GEOID"), header.index("INTPTLAT"), header.index("INTPTLONG")
        except ValueError:
            raise ZoriFormatError(f"{path.name}: expected GEOID, INTPTLAT and INTPTLONG columns") from None
        for row in reader:
            try:
                out[row[gi].strip().zfill(5)] = (float(row[la]), float(row[lo]))
            except (IndexError, ValueError):
                continue
    return out


# ------------------------------------------------------------------ inputs
@dataclass
class ZoriInputs:
    files: dict[str, Path] = field(default_factory=dict)        # level -> full CSV
    fetched: dict[str, dt.date] = field(default_factory=dict)   # level -> fetched date
    gazetteer: Optional[Path] = None


def fetched_date(path: Path) -> dt.date:
    """The download date: ``fetched_date`` from the sidecar ``<file>.json`` the fetch command writes, else the
    file's modification date when the extract is made (a browser download sets it to the download day). The
    extract records it, so a fresh checkout never changes it."""
    try:
        meta = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))
        return dt.date.fromisoformat(meta["fetched_date"])
    except (OSError, ValueError, KeyError, TypeError):
        return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).date()


def find_inputs(zillow_dir: Path = ZILLOW_DIR) -> ZoriInputs:
    """Full ZORI CSVs: a file placed by hand in ``zillow_dir`` wins over ``zillow_dir/download``. A browser may add
    " (1)" to the name; any ``<expected stem>*.csv`` counts (the newest when several)."""
    found = ZoriInputs()
    for level, name in FILES.items():
        stem = name[: -len(".csv")]
        for d in (zillow_dir, zillow_dir / DOWNLOAD_SUBDIR):
            cands = sorted((p for p in d.glob(f"{stem}*.csv") if p.is_file()), key=lambda p: p.stat().st_mtime)
            if cands:
                found.files[level] = cands[-1]
                found.fetched[level] = fetched_date(cands[-1])
                break
    for d in (zillow_dir, zillow_dir / DOWNLOAD_SUBDIR):
        gaz = sorted(p for p in d.glob(GAZETTEER_GLOB) if p.is_file())
        if gaz:
            found.gazetteer = gaz[-1]
            break
    return found


# ------------------------------------------------------------------ extract
@dataclass
class Targets:
    """What the build needs: every resort county (fips -> (name, state)) and the Tier A base points."""
    counties: dict[str, tuple[str, Optional[str]]]
    bases: list[tuple[float, float]]
    zip_radius_km: float


def select_rows(rows: dict[str, list[ZoriRow]], targets: Targets,
                centroids: Optional[dict[str, tuple[float, float]]]) -> tuple[list[ZoriRow], list[str]]:
    """Rows to keep in the extract, plus notes. ZIP rows only with centroids (lat, lon set from them)."""
    notes: list[str] = []
    keep: list[ZoriRow] = []
    by_name = {(norm_county(n), (s or "").upper()): f for f, (n, s) in targets.counties.items()}
    # counties: by FIPS, else by name and state when the file has no FIPS columns
    county_metro: dict[str, str] = {}
    for r in rows.get("county", []):
        fips = r.county_fips if r.county_fips in targets.counties else by_name.get((norm_county(r.region_name), (r.state or "").upper()))
        if fips:
            keep.append(replace(r, county_fips=fips))
            if r.metro:
                county_metro.setdefault(fips, r.metro)
    # the county -> metro link also comes from ZIP rows (CountyName, State, Metro) for counties without a county row
    for r in rows.get("zip", []):
        fips = by_name.get((norm_county(r.county_name), (r.state or "").upper()))
        if fips and r.metro:
            county_metro.setdefault(fips, r.metro)
    if rows.get("zip"):
        if centroids is None:
            notes.append("no Census Gazetteer ZCTA file in data/raw/rent/zillow/; ZIP matching skipped")
        else:
            for r in rows["zip"]:
                c = centroids.get(r.region_name)
                if c and any(haversine_km(c[0], c[1], la, lo) <= targets.zip_radius_km for la, lo in targets.bases):
                    keep.append(replace(r, lat=c[0], lon=c[1]))
    # metros of the resort counties, each listing the counties it serves
    metros = rows.get("metro", [])
    exact = {m.region_name.strip().lower(): m for m in metros}
    keyed: dict[tuple[str, str], ZoriRow] = {}
    for m in metros:
        keyed.setdefault(metro_key(m.region_name), m)
    linked: dict[str, list[str]] = {}
    for fips, metro in county_metro.items():
        m = exact.get(metro.strip().lower()) or keyed.get(metro_key(metro))
        if m:
            linked.setdefault(m.region_id, []).append(fips)
    for m in metros:
        if m.region_id in linked:
            keep.append(replace(m, county_fips=" ".join(sorted(linked[m.region_id]))))
    order = {lv: i for i, lv in enumerate(LEVELS)}
    keep.sort(key=lambda r: (order[r.level], r.region_id))
    return keep, notes


def _fmt(v: Optional[float], digits: int = 2) -> str:
    return "" if v is None else f"{round(v, digits):.{digits}f}".rstrip("0").rstrip(".")


def write_extract(rows: Iterable[ZoriRow], path: Path) -> bool:
    """Write the extract CSV (one region per line); returns True when the file changed."""
    import io

    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(EXTRACT_COLUMNS)
    for r in rows:
        w.writerow([r.level, r.region_id, r.region_name, r.state or "", r.county_fips or "", r.county_name or "",
                    r.city or "", r.metro or "", _fmt(r.lat, 5), _fmt(r.lon, 5),
                    r.latest_month.isoformat() if r.latest_month else "", _fmt(r.latest_value),
                    _fmt(r.trailing_mean), str(r.trailing_months), r.source_file or "",
                    r.fetched_date.isoformat() if r.fetched_date else ""])
    text = out.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def refresh_extract(zillow_dir: Path, targets: Targets, trailing: int = 12) -> tuple[Optional[int], list[str]]:
    """Rebuild ``zori_extract.csv`` from the full files when any is present. Returns (rows kept or None, notes).

    A level whose full file is absent keeps its rows from the committed extract, so refreshing only the county
    file never drops the ZIP or metro rows."""
    inputs = find_inputs(zillow_dir)
    if not inputs.files:
        return None, []
    notes: list[str] = []
    rows: dict[str, list[ZoriRow]] = {}
    for level, path in inputs.files.items():
        rows[level] = [replace(r, fetched_date=inputs.fetched[level]) for r in parse_zori_csv(path, level, trailing)]
    old = load_extract(zillow_dir / EXTRACT_NAME)
    kept_old = [r for r in (old.rows if old else []) if r.level not in inputs.files]
    for level in LEVELS:
        if level not in inputs.files:
            notes.append(f"no Zillow {level} ZORI file ({FILES[level]}); its rows come from the committed extract")
    centroids = load_gazetteer(inputs.gazetteer) if inputs.gazetteer else None
    keep, sel_notes = select_rows(rows, targets, centroids)
    order = {lv: i for i, lv in enumerate(LEVELS)}
    keep = sorted(keep + kept_old, key=lambda r: (order[r.level], r.region_id))
    write_extract(keep, zillow_dir / EXTRACT_NAME)
    return len(keep), notes + sel_notes


@dataclass
class ZoriExtract:
    rows: list[ZoriRow]

    def level(self, level: str) -> list[ZoriRow]:
        return [r for r in self.rows if r.level == level]


def load_extract(path: Path) -> Optional[ZoriExtract]:
    """The committed extract, or None when it does not exist."""
    if not path.is_file():
        return None
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            def num(k: str) -> Optional[float]:
                return float(rec[k]) if rec.get(k) not in (None, "") else None
            rows.append(ZoriRow(
                level=rec["level"], region_id=rec["region_id"], region_name=rec["region_name"],
                state=rec.get("state") or None, county_fips=rec.get("county_fips") or None,
                county_name=rec.get("county_name") or None, city=rec.get("city") or None,
                metro=rec.get("metro") or None,
                lat=num("lat"), lon=num("lon"),
                latest_month=dt.date.fromisoformat(rec["latest_month"]) if rec.get("latest_month") else None,
                latest_value=num("latest_value"), trailing_mean=num("trailing_mean"),
                trailing_months=int(rec.get("trailing_months") or 0), source_file=rec.get("source_file") or None,
                fetched_date=dt.date.fromisoformat(rec["fetched_date"]) if rec.get("fetched_date") else None))
    return ZoriExtract(rows)


# ------------------------------------------------------------------- match
@dataclass
class ZoriMatch:
    row: ZoriRow
    level: str
    distance_km: Optional[float] = None   # ZIP matches: centroid to base


def match_resort(lat: float, lon: float, county_fips: Optional[str], extract: Optional[ZoriExtract],
                 radius_km: float, levels: Sequence[str] = LEVELS) -> Optional[ZoriMatch]:
    """ZIP (nearest centroid within ``radius_km`` of the base), else the county (FIPS), else the county's metro,
    else None. Only regions with a value count; nothing is interpolated."""
    if extract is None:
        return None
    for level in levels:
        cands = [r for r in extract.level(level) if r.latest_value is not None]
        if level == "zip":
            near = [(haversine_km(lat, lon, r.lat, r.lon), r) for r in cands if r.lat is not None and r.lon is not None]
            near = [(d, r) for d, r in near if d <= radius_km]
            if near:
                d, r = min(near, key=lambda dr: (dr[0], dr[1].region_id))
                return ZoriMatch(r, "zip", round(d, 2))
        elif county_fips:
            hit = [r for r in cands if county_fips in r.county_fips_list]
            if hit:
                return ZoriMatch(min(hit, key=lambda r: r.region_id), level)
    return None


def geography_name(m: ZoriMatch) -> str:
    """'ZIP 80424 (Breckenridge, CO)', 'Summit County, CO' or 'Breckenridge, CO metro'."""
    r = m.row
    if m.level == "zip":
        place = ", ".join(x for x in (r.city, r.state) if x)
        return f"ZIP {r.region_name}" + (f" ({place})" if place else "")
    if m.level == "county":
        return f"{r.region_name}, {r.state}" if r.state and not r.region_name.endswith(f", {r.state}") else r.region_name
    return f"{r.region_name} metro"


def source(extract: ZoriExtract) -> Optional[Source]:
    """One source record for the build: the latest download date among the rows."""
    dates = [r.fetched_date for r in extract.rows if r.fetched_date]
    if not dates:
        return None
    through = max((r.latest_month for r in extract.rows if r.latest_month), default=None)
    note = ZORI_NOTE + (f". Data through {month_label(through)}" if through else "")
    return Source(id=SOURCE_ID, url=RESEARCH_PAGE, title=SOURCE_TITLE, accessed_date=max(dates), note=note)


def zori_rent(m: ZoriMatch, value_kind: str = "latest") -> Optional[dict]:
    """The rent used block for a ZORI match: the latest month (``as_of`` = its month end), confidence published."""
    r = m.row
    value = r.latest_value if value_kind == "latest" else r.trailing_mean
    if value is None or r.latest_month is None:
        return None
    where = geography_name(m)
    if m.level == "zip" and m.distance_km is not None:
        where += f", centroid {m.distance_km:g} km from the base"
    return {"value": round(value, 2), "source_ids": [SOURCE_ID], "as_of": r.latest_month.isoformat(),
            "confidence": "published",
            "note": f"{ZORI_NOTE}; {where}; {month_label(r.latest_month)}"
                    + ("" if value_kind == "latest" else f", mean of the {r.trailing_months} months to then"),
            "geography": {"level": m.level, "id": r.region_id if m.level != "county" else (r.county_fips_list or [r.region_id])[0],
                          "name": geography_name(m)},
            "trailing_mean": round(r.trailing_mean, 2) if r.trailing_mean is not None else None}


# ------------------------------------------------------------------- fetch
class FetchBlocked(RuntimeError):
    pass


def _download(url: str, dest: Path, timeout: float = 120) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    tmp.replace(dest)


def fetch(dest_dir: Path, downloader: Callable[[str, Path], None] = _download,
          today: Optional[dt.date] = None) -> list[Path]:
    """Download the three ZORI CSVs into ``dest_dir`` with a ``<file>.json`` sidecar ``{fetched_date, url}``.
    Raises FetchBlocked naming the host when it cannot be reached."""
    today = today or dt.date.today()
    out = []
    for name in FILES.values():
        url = BASE_URL + name
        dest = dest_dir / name
        try:
            downloader(url, dest)
        except (urllib.error.URLError, OSError, TimeoutError) as e:  # URLError covers HTTPError and proxy refusals
            raise FetchBlocked(
                f"{HOST} could not be reached ({type(e).__name__}: {e}). Allow {HOST} in the network policy, or "
                f"download the files by hand: open {RESEARCH_PAGE}, section Rentals, series \"ZORI (Smoothed): All "
                f"Homes Plus Multifamily Time Series ($)\", geographies County, ZIP Code and Metro & U.S., and put "
                f"{', '.join(FILES.values())} in data/raw/rent/zillow/") from e
        dest.with_name(dest.name + ".json").write_text(
            json.dumps({"fetched_date": today.isoformat(), "url": url}) + "\n", encoding="utf-8")
        out.append(dest)
    return out


def build_targets() -> Targets:  # pragma: no cover - wiring for the command line
    """Targets from data/raw (resort counties) and config (radius), for ``extract`` outside the build."""
    from pipeline.config import load_config
    from pipeline.rent import county_for, load_counties
    from pipeline.sources import SourceRegistry
    from pipeline.tier_a import load_raw

    cfg = load_config().definitions.rent.zori
    recs = load_raw(REPO_ROOT / "data" / "raw", SourceRegistry(), REPO_ROOT).records
    counties = load_counties()
    found: dict[str, tuple[str, Optional[str]]] = {}
    bases = []
    for _, rec in recs:
        c = county_for(rec.lat, rec.lon, counties)
        if c:
            found[c.fips] = (c.name, c.state)
        if rec.tier == "A":
            bases.append((rec.lat, rec.lon))
    return Targets(found, bases, cfg.zip_radius_km)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Zillow ZORI rent data: fetch the CSVs or rebuild the committed extract.")
    ap.add_argument("command", choices=["fetch", "extract"])
    args = ap.parse_args(argv)
    if args.command == "fetch":
        try:
            paths = fetch(ZILLOW_DIR / DOWNLOAD_SUBDIR)
        except FetchBlocked as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        for p in paths:
            print(f"downloaded {p.relative_to(REPO_ROOT)} ({p.stat().st_size:,} bytes)")
        print("now run: python pipeline/build.py (it rebuilds data/raw/rent/zillow/zori_extract.csv)")
    from pipeline.config import load_config
    n, notes = refresh_extract(ZILLOW_DIR, build_targets(), load_config().definitions.rent.zori.trailing_months)
    for note in notes:
        print(f"NOTE: {note}")
    if n is None:
        print(f"no ZORI files in {ZILLOW_DIR.relative_to(REPO_ROOT)}/ or its download/ folder", file=sys.stderr)
        return 1
    print(f"wrote {(ZILLOW_DIR / EXTRACT_NAME).relative_to(REPO_ROOT)}: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
