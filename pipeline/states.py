"""US state reference table, states.json, and the us-states.geojson build step.

us-states.geojson is produced by scripts/build-states.mjs from the npm
packages us-atlas and topojson-client (dev tooling; node_modules is
gitignored). Only the output is committed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable

# FIPS code -> (USPS code, name). Keep in sync with scripts/build-states.mjs.
STATES: dict[str, tuple[str, str]] = {
    "01": ("AL", "Alabama"), "02": ("AK", "Alaska"), "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"), "06": ("CA", "California"), "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"), "10": ("DE", "Delaware"), "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"), "13": ("GA", "Georgia"), "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"), "17": ("IL", "Illinois"), "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"), "20": ("KS", "Kansas"), "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"), "23": ("ME", "Maine"), "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"), "26": ("MI", "Michigan"), "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"), "29": ("MO", "Missouri"), "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"), "32": ("NV", "Nevada"), "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"), "35": ("NM", "New Mexico"), "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"), "38": ("ND", "North Dakota"), "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"), "41": ("OR", "Oregon"), "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"), "45": ("SC", "South Carolina"), "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"), "48": ("TX", "Texas"), "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"), "51": ("VA", "Virginia"), "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"), "55": ("WI", "Wisconsin"), "56": ("WY", "Wyoming"),
    "60": ("AS", "American Samoa"), "66": ("GU", "Guam"),
    "69": ("MP", "Commonwealth of the Northern Mariana Islands"),
    "72": ("PR", "Puerto Rico"), "78": ("VI", "United States Virgin Islands"),
}

STATE_NAMES: dict[str, str] = {code: name for code, name in STATES.values()}


def build_states(resorts: Iterable[tuple[str, str, str]]) -> list[dict]:
    """states.json entries from (resort_id, state, cluster_id) triples.

    ``cluster_ids`` lists every cluster with at least one member resort in the
    state, sorted. Only states with resorts are returned, sorted by code.
    """
    clusters: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for _rid, state, cid in resorts:
        clusters.setdefault(state, set()).add(cid)
        counts[state] = counts.get(state, 0) + 1
    return [
        {"code": code, "name": STATE_NAMES[code], "cluster_ids": sorted(clusters[code]), "resort_count": counts[code]}
        for code in sorted(counts)
    ]


def build_us_states_geojson(repo_root: Path, out_path: Path) -> bool:
    """Run scripts/build-states.mjs. Returns False (no error) when node or
    node_modules is unavailable."""
    script = repo_root / "scripts" / "build-states.mjs"
    node = shutil.which("node")
    if not node or not script.exists() or not (repo_root / "node_modules" / "us-atlas").is_dir():
        return False
    subprocess.run([node, str(script), str(out_path)], check=True, cwd=repo_root, capture_output=True)
    return True
