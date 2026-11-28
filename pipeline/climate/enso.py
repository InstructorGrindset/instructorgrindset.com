"""NOAA CPC El Niño and La Niña index: the Relative Oceanic Niño Index (RONI), for the jet stream layer.

NOAA's Climate Prediction Center made the RONI its official ENSO index in February 2026 (NWS Public Information
Statement 26-05). It is the three month running mean of the relative Niño 3.4 index: the Niño 3.4 sea surface
temperature anomaly minus the tropical mean anomaly (20N to 20S), on the 1991 to 2020 base period. CPC also keeps
the legacy ONI. Both text files:

    https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt
    https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt

Each row is a three month season (DJF, JFM, ..., NDJ), a year and the index value in the last column (the ONI file
also has the total temperature first). CPC's site is not reachable from every network, so the GitHub workflow
``.github/workflows/enso-index.yml`` runs ``fetch`` and commits the files.

Commands::

    python -m pipeline.climate.enso fetch     # download both files into data/raw/climate/enso/
    python -m pipeline.climate.enso summary   # rows and the latest season in the committed files

The build reads ``data/raw/climate/enso/RONI.ascii.txt`` with :func:`parse_index` (pipeline/jetstream.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "climate" / "enso"
FILES = {
    "RONI.ascii.txt": "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt",
    "oni.ascii.txt": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
}
USER_AGENT = "SnowsportsCareerMapResearch/1.0"
SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")


def parse_index(text: str) -> list[tuple[str, int, float]]:
    """(season, year, value) rows of a CPC index text file, in file order; header and blank lines are skipped."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0].upper() not in SEASONS:
            continue
        try:
            year, value = int(parts[1]), float(parts[-1])
        except ValueError:
            continue
        if value <= -99:  # CPC fills missing values with -99.9
            continue
        rows.append((parts[0].upper(), year, value))
    if not rows:
        raise ValueError("no index rows found")
    return rows


def fetch() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    accessed = dt.date.today().isoformat()
    record = {"files": {}}
    for name, url in FILES.items():
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
        rows = parse_index(text)  # refuse to commit a page that is not the index
        (OUT_DIR / name).write_text(text if text.endswith("\n") else text + "\n")
        record["files"][name] = {"url": url, "rows": len(rows), "first": f"{rows[0][0]} {rows[0][1]}", "last": f"{rows[-1][0]} {rows[-1][1]}"}
        print(f"{name}: {len(rows)} rows, {rows[0][0]} {rows[0][1]} to {rows[-1][0]} {rows[-1][1]}")
    source = {
        "source": {
            "id": "noaa-cpc-roni",
            "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/",
            "title": "NOAA Climate Prediction Center, Relative Oceanic Niño Index (RONI), historical El Niño and La Niña episodes",
            "accessed_date": accessed,
            "note": (
                "RONI.ascii.txt (official ENSO index since February 2026, NWS Public Information Statement 26-05) and the "
                "legacy oni.ascii.txt. The most recent values are preliminary. Public domain (US government work)."
            ),
        },
        "confidence": "published",
        "license": "public domain",
        **record,
    }
    (OUT_DIR / "source.json").write_text(json.dumps(source, indent=2) + "\n")


def summary() -> None:
    for name in FILES:
        rows = parse_index((OUT_DIR / name).read_text())
        print(f"{name}: {len(rows)} rows, last {rows[-1][0]} {rows[-1][1]} = {rows[-1][2]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.climate.enso")
    p.add_argument("cmd", choices=["fetch", "summary"])
    args = p.parse_args(argv)
    fetch() if args.cmd == "fetch" else summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
