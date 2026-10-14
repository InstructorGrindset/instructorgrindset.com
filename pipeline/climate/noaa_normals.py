"""NOAA NCEI U.S. Climate Normals 1991 to 2020: annual snowfall normals by station.

The snowfall heat map background (docs/map/ARCHITECTURE.md, "Heat maps") uses the field
``ANN-SNOW-NORMAL`` (inches) of the annual and seasonal normals by station. NOAA publishes the bulk
archive on ncei.noaa.gov and on the NOAA Open Data Dissemination mirror on AWS, which holds the same
files and is the one this fetcher reads:

    https://noaa-normals-pds.s3.amazonaws.com/?list-type=2&prefix=normals-annualseasonal/1991-2020/archive/

The archive (about 54 MB) holds one CSV per station: ``STATION, NAME, LATITUDE, LONGITUDE, ELEVATION``
(metres) and, per variable present at that station, four columns: the value, ``meas_flag_<var>``,
``comp_flag_<var>`` and ``years_<var>``. Flags, from NOAA's readme
(``doc/Readme_By-Variable_By-Station_Normals_Files.txt``):

* measurement: M missing, V too cold to compute, W unused, X nonzero value rounded to zero,
  Y insufficient values, Z logically inconsistent with other values;
* completeness: S standard (24 or more years), R representative (10 or more years, gaps filled from
  nearby stations), P provisional (10 or more years, gaps could not be filled), E estimated (2 or more
  years, estimated statistically from nearby stations).

Commands::

    python -m pipeline.climate.noaa_normals fetch     # download the archive, write the compact extract
    python -m pipeline.climate.noaa_normals summary   # counts in the committed extract

The compact extract (committed) is ``data/raw/climate/noaa_normals/ann_snow_normals.csv`` plus its source
record ``source.json``. The archive download goes to ``data/raw/climate/noaa_normals/download/``
(gitignored). The build reads only the extract and keeps a station when its completeness flag is in
``heat.snowfall.accept_completeness`` and its measurement flag in ``heat.snowfall.accept_measurement_flags``
(config/definitions.yaml). Zero snowfall stations (the South) are real data and stay.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from pipeline.climate import http

BUCKET_URL = "https://noaa-normals-pds.s3.amazonaws.com/"
ARCHIVE_PREFIX = "normals-annualseasonal/1991-2020/archive/"
FIELD = "ANN-SNOW-NORMAL"
SOURCE_ID = "noaa-ncei-normals-1991-2020"
EXTRACT_NAME = "ann_snow_normals.csv"
SOURCE_NAME = "source.json"
COLUMNS = ["station_id", "name", "lat", "lon", "elevation_m", "ann_snow_in", "meas_flag", "comp_flag", "years"]
# GHCN Daily ids of the 50 states start with US (USC cooperative, USW first order, US1 CoCoRaHS, USS SNOTEL).
# Territory stations (RQ, VQ, GQ, CQ, AQ and others) are left out: the map hides the territories.
US_STATION = re.compile(r"^US[A-Z0-9]{9}$")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "data" / "raw" / "climate" / "noaa_normals"


@dataclass(frozen=True)
class StationNormal:
    station_id: str
    name: str
    lat: float
    lon: float
    elevation_m: Optional[float]
    ann_snow_in: Optional[float]
    meas_flag: str
    comp_flag: str
    years: Optional[int]


def _num(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    # NOAA marks missing numbers with large negative sentinels (-9999, -7777 and similar).
    return None if v <= -999 else v


def parse_station_csv(text: str) -> Optional[StationNormal]:
    """The annual snowfall normal of one by station CSV, or None when the station has no such column."""
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    row = next(rows, None)
    if not header or not row:
        return None
    col = {name.strip(): i for i, name in enumerate(header)}
    if FIELD not in col:
        return None

    def get(name: str) -> str:
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    lat, lon = _num(get("LATITUDE")), _num(get("LONGITUDE"))
    sid = get("STATION")
    if lat is None or lon is None or not sid:
        return None
    years = _num(get(f"years_{FIELD}"))
    return StationNormal(
        station_id=sid,
        name=" ".join(get("NAME").split()),
        lat=lat,
        lon=lon,
        elevation_m=_num(get("ELEVATION")),
        ann_snow_in=_num(get(FIELD)),
        meas_flag=get(f"meas_flag_{FIELD}"),
        comp_flag=get(f"comp_flag_{FIELD}"),
        years=int(years) if years is not None else None,
    )


def usable(st: StationNormal, accept_completeness: Iterable[str], accept_measurement: Iterable[str]) -> bool:
    """True when the station has a snow normal that its own flags mark as complete and valid."""
    if st.ann_snow_in is None or st.ann_snow_in < 0:
        return False
    return st.comp_flag in set(accept_completeness) and st.meas_flag in set(accept_measurement)


def filter_usable(stations: Iterable[StationNormal], accept_completeness: Iterable[str],
                  accept_measurement: Iterable[str]) -> list[StationNormal]:
    comp, meas = list(accept_completeness), list(accept_measurement)
    return [s for s in stations if usable(s, comp, meas)]


def extract_archive(path: Path) -> tuple[list[StationNormal], int]:
    """(stations of the 50 states that carry the snow normal column, station files read) from the archive."""
    out: list[StationNormal] = []
    files = 0
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            name = Path(member.name).name
            if not member.isfile() or not name.endswith(".csv"):
                continue
            files += 1
            if not US_STATION.match(name[:-4]):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            st = parse_station_csv(fh.read().decode("utf-8", "replace"))
            if st is not None:
                out.append(st)
    out.sort(key=lambda s: s.station_id)
    return out, files


def write_extract(stations: list[StationNormal], directory: Path, source: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for s in stations:
        w.writerow([s.station_id, s.name, f"{s.lat:.4f}", f"{s.lon:.4f}",
                    "" if s.elevation_m is None else f"{s.elevation_m:.1f}",
                    "" if s.ann_snow_in is None else f"{s.ann_snow_in:.1f}",
                    s.meas_flag, s.comp_flag, "" if s.years is None else s.years])
    (directory / EXTRACT_NAME).write_text(buf.getvalue(), encoding="utf-8")
    (directory / SOURCE_NAME).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")


def load_extract(directory: Path) -> tuple[list[StationNormal], dict]:
    """(stations, source record) from the committed extract. Raises FileNotFoundError when absent."""
    source = json.loads((directory / SOURCE_NAME).read_text(encoding="utf-8"))
    out: list[StationNormal] = []
    with (directory / EXTRACT_NAME).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            years = _num(row.get("years", ""))
            out.append(StationNormal(
                station_id=row["station_id"], name=row["name"], lat=float(row["lat"]), lon=float(row["lon"]),
                elevation_m=_num(row.get("elevation_m", "")), ann_snow_in=_num(row.get("ann_snow_in", "")),
                meas_flag=(row.get("meas_flag") or "").strip(), comp_flag=(row.get("comp_flag") or "").strip(),
                years=int(years) if years is not None else None))
    return out, source


def archive_listing() -> dict:
    """{key, size, etag, last_modified} of the by station archive, from the bucket listing (S3 ListObjectsV2)."""
    body = http.get_bytes(BUCKET_URL, {"list-type": 2, "prefix": ARCHIVE_PREFIX}, accept="application/xml")
    return parse_listing(body)


def parse_listing(body: bytes) -> dict:
    root = ET.fromstring(body)
    ns = {"s3": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    q = (lambda tag: f"s3:{tag}") if ns else (lambda tag: tag)
    best = None
    for c in root.findall(q("Contents"), ns):
        key = c.findtext(q("Key"), default="", namespaces=ns)
        if key.endswith(".tar.gz") and "by-station" in key:
            item = {"key": key, "size": int(c.findtext(q("Size"), default="0", namespaces=ns)),
                    "etag": c.findtext(q("ETag"), default="", namespaces=ns).strip('"'),
                    "last_modified": c.findtext(q("LastModified"), default="", namespaces=ns)}
            if best is None or item["key"] > best["key"]:
                best = item
    if best is None:
        raise RuntimeError(f"no by station archive under {ARCHIVE_PREFIX}")
    return best


def source_record(listing: dict, today: dt.date, rows: int, files: int) -> dict:
    return {
        "source": {
            "id": SOURCE_ID,
            "url": "https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals",
            "title": "NOAA NCEI U.S. Climate Normals 1991 to 2020, annual and seasonal normals by station",
            "accessed_date": today.isoformat(),
            "note": (f"Public domain (US government work). Field {FIELD}, inches, per station. Read from the NOAA "
                     f"Open Data Dissemination mirror {BUCKET_URL}{listing['key']}."),
        },
        "confidence": "published",
        "license": "public domain",
        "field": FIELD,
        "units": "in",
        "elevation_units": "m",
        "archive": listing,
        "station_files": files,
        "rows": rows,
        "flags": {
            "measurement": {"M": "missing", "V": "too cold to compute", "W": "unused",
                            "X": "nonzero value rounded to zero", "Y": "insufficient values",
                            "Z": "logically inconsistent"},
            "completeness": {"S": "standard, 24 or more years", "R": "representative, 10 or more years, filled",
                             "P": "provisional, 10 or more years, unfilled", "E": "estimated, 2 or more years"},
        },
    }


def fetch(directory: Path = DEFAULT_DIR, *, today: Optional[dt.date] = None) -> dict:
    today = today or dt.date.today()
    listing = archive_listing()
    archive = directory / "download" / Path(listing["key"]).name
    if not archive.exists() or archive.stat().st_size != listing["size"]:
        http.download(BUCKET_URL + listing["key"], archive)
    stations, files = extract_archive(archive)
    rec = source_record(listing, today, len(stations), files)
    write_extract(stations, directory, rec)
    return {"station_files": files, "rows": len(stations), "archive": listing["key"]}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["fetch", "summary"])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args(argv)
    if args.command == "fetch":
        try:
            res = fetch(args.dir)
        except http.NetworkBlocked as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"read {res['station_files']} station files, kept {res['rows']} with {FIELD} -> {args.dir}")
        return 0
    stations, source = load_extract(args.dir)
    from collections import Counter
    print(f"{len(stations)} stations, accessed {source['source']['accessed_date']}")
    print("completeness", dict(Counter(s.comp_flag for s in stations)))
    print("measurement", dict(Counter(s.meas_flag or "blank" for s in stations)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
