"""Compile data/raw + config into data/build (docs/map/ARCHITECTURE.md).

    python pipeline/build.py [--fetch] [--no-report] [--no-routes]
    python -m pipeline.build ...

Outputs: resorts.json, resorts.geojson, clusters.geojson, states.json,
sources.json, us-states.geojson (when missing, via scripts/build-states.mjs),
and the Tier A review document docs/map/tier-a-draft.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):  # executed as a script: python pipeline/build.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pydantic import ValidationError  # noqa: E402

from pipeline import openskimap  # noqa: E402
from pipeline.clusters import ClusterAssignmentError, ClusterInfo, Member, assign_clusters, build_clusters  # noqa: E402
from pipeline.config import Config, load_config  # noqa: E402
from pipeline.names import slugify  # noqa: E402
from pipeline.schemas import Cluster, Resort, Source, State  # noqa: E402
from pipeline.schemas.raw import TierABatch, TierARecord  # noqa: E402
from pipeline.sources import SourceRegistry  # noqa: E402
from pipeline.states import STATE_NAMES, build_states, build_us_states_geojson  # noqa: E402
from pipeline.tier_a import BuildError, RawInputs, load_raw, match_openskimap, ownership_cross_check  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_VERSION = 1


@dataclass
class BuildResult:
    out_dir: Path
    resorts: list[Resort]
    clusters: list[Cluster]
    states: list[State]
    source_count: int
    warnings: list[str] = field(default_factory=list)
    inputs: Optional[RawInputs] = None

    @property
    def tier_a_count(self) -> int:
        return sum(1 for r in self.resorts if r.tier == "A")


def generated_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    now = dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc) if epoch else dt.datetime.now(dt.timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, data: Any, compact: bool = False) -> None:
    """Write ``data``; leave the file untouched when only ``generated_at`` would change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and isinstance(data, dict):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict) and {**old, "generated_at": None} == {**data, "generated_at": None}:
            return
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(
        data, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def _sourced(value: Any, source_id: str, as_of: dt.date, note: str) -> dict:
    return {"value": value, "source_id": source_id, "as_of": as_of.isoformat(), "confidence": "estimated", "note": note}


LIFTS_NOTE = "lifts mapped in OpenStreetMap (OpenSkiMap)"
VERTICAL_NOTE = "highest minus lowest mapped lift or downhill run elevation (OpenSkiMap)"
BASE_NOTE = "lowest mapped lift or downhill run elevation (OpenSkiMap)"
SUMMIT_NOTE = "highest mapped lift or downhill run elevation (OpenSkiMap)"


def _osm_fill(d: dict, area: openskimap.SkiArea, as_of: dt.date) -> None:
    """Fill null size and elevation fields from one OpenSkiMap area (confidence estimated)."""
    size, elev = d.setdefault("size", {}), d.setdefault("elevation", {})
    for block, key, value, note in (
        (size, "lifts", area.lift_count, LIFTS_NOTE),
        (size, "vertical_ft", area.vertical_ft, VERTICAL_NOTE),
        (elev, "base_ft", area.base_ft, BASE_NOTE),
        (elev, "summit_ft", area.summit_ft, SUMMIT_NOTE),
    ):
        # zero mapped lifts or zero vertical says nothing; an elevation of 0 ft is still a value
        if block.get(key) is None and value is not None and (value or block is elev):
            block[key] = _sourced(value, openskimap.SOURCE_ID, as_of, note)


def _tier_a_draft(batch: TierABatch, rec: TierARecord, matches: list[openskimap.SkiArea],
                  osm_as_of: Optional[dt.date]) -> dict:
    d = rec.model_dump(mode="json")
    d.pop("cluster_hint")
    if len(matches) == 1 and osm_as_of is not None:
        _osm_fill(d, matches[0], osm_as_of)
    d.update(tier="A", tier_a_status="approved" if batch.status == "approved" else "draft",
             career=None, editorial=None)
    return d


def _tier_b_draft(area: openskimap.SkiArea, rid: str, as_of: dt.date) -> dict:
    d: dict[str, Any] = {
        "id": rid, "name": area.name, "tier": "B", "tier_a_criteria": [], "tier_a_status": None,
        "owner": None, "lat": round(area.lat, 5), "lon": round(area.lon, 5),
        "location_source_id": openskimap.SOURCE_ID, "state": area.state,
        "size": {"acres": None, "vertical_ft": None, "lifts": None, "ski_school_staff_est": None},
        "elevation": {"base_ft": None, "summit_ft": None},
        "career": None, "editorial": None,
    }
    _osm_fill(d, area, as_of)
    return d


def _merge_climate_sources(path: Path, registry: SourceRegistry, errors: list[str], rel=str) -> None:
    """Add the climate ETL's sources (data/build/climate/sources.json) to the registry."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{rel(path)}: unreadable ({e})")
        return
    for i, raw in enumerate(data.get("sources") or []):
        try:
            registry.add(Source.model_validate(raw), rel(path))
        except ValidationError as e:
            for m in e.errors():
                errors.append(f"{rel(path)}: sources[{i}].{'.'.join(map(str, m['loc']))}: {m['msg']}")
        except ValueError as e:
            errors.append(f"{rel(path)}: {e}")


def _unique_id(base: str, state: str, taken: set[str]) -> str:
    base = base or "ski-area"
    for cand in (base, f"{base}-{state.lower()}"):
        if cand not in taken:
            return cand
    i = 2
    while f"{base}-{state.lower()}-{i}" in taken:
        i += 1
    return f"{base}-{state.lower()}-{i}"


def _load_openskimap(raw_dir: Path, fetch: bool, warnings: list[str], rel=str, *,
                     filter_kw: Optional[dict] = None, state_shapes_path: Optional[Path] = None,
                     ) -> Optional[openskimap.Extract]:
    """Refresh the committed US extract from the cached full download when present
    (so filter rule changes apply), then load it."""
    osm_dir = raw_dir / "openskimap"
    full = osm_dir / openskimap.FULL_NAME
    extract_path = osm_dir / openskimap.US_NAME
    kw = dict(filter_kw or {})
    if state_shapes_path is not None and state_shapes_path.exists():
        kw["state_shapes"] = openskimap.load_state_shapes(state_shapes_path)
    fetched_today = False
    if fetch:
        try:
            print(f"fetching {openskimap.URL} ...")
            openskimap.fetch(full)
            fetched_today = True
        except Exception as e:  # network failures must not stop a Tier A build
            warnings.append(f"OpenSkiMap download failed ({type(e).__name__}: {e}); using the cached data if any")
    if full.exists():
        n = openskimap.write_us_extract(full, extract_path, dt.date.today() if fetched_today else None, **kw)
        print(f"filtered {rel(full)} -> {rel(extract_path)}: {n} US operating downhill areas")
    if not extract_path.exists():
        warnings.append(
            f"no OpenSkiMap extract at {rel(extract_path)}; building Tier A only (no Tier B resorts). "
            f"Run `python pipeline/build.py --fetch` with network access to add them."
        )
        return None
    return openskimap.load_us_extract(extract_path)


def build(
    repo_root: Path = REPO_ROOT,
    *,
    raw_dir: Optional[Path] = None,
    config_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    report_path: Optional[Path] = None,
    fetch: bool = False,
    write_report: bool = True,
    states_geojson: bool = True,
) -> BuildResult:
    raw_dir = raw_dir or repo_root / "data" / "raw"

    def rel(p: Path) -> str:
        return str(p.relative_to(repo_root)) if p.is_relative_to(repo_root) else str(p)

    out_dir = out_dir or repo_root / "data" / "build"
    report_path = report_path or repo_root / "docs" / "map" / "tier-a-draft.md"
    cfg: Config = load_config(config_dir or repo_root / "config")
    warnings: list[str] = []
    errors: list[str] = []

    registry = SourceRegistry()
    inputs = load_raw(raw_dir, registry, repo_root)
    if not inputs.batches:
        warnings.append(f"no Tier A research files in {rel(raw_dir / 'tier_a')}; no Tier A resorts in this build")
    if not inputs.ownership:
        warnings.append(f"no ownership files in {rel(raw_dir / 'ownership')}")

    own_errors, own_warnings = ownership_cross_check(inputs)
    errors += own_errors
    warnings += own_warnings

    us_states_path = out_dir / "us-states.geojson"
    if states_geojson and not us_states_path.exists() and not build_us_states_geojson(repo_root, us_states_path):
        warnings.append("us-states.geojson is missing and node or node_modules is unavailable; run `npm install && npm run build:states`")
    extract = _load_openskimap(raw_dir, fetch, warnings, rel, filter_kw=cfg.definitions.openskimap.filter_kwargs(),
                               state_shapes_path=us_states_path if us_states_path.exists() else repo_root / "data" / "build" / "us-states.geojson")
    areas = extract.areas if extract else []
    if extract:
        registry.add(extract.source, "data/raw/openskimap")

    climate_sources = out_dir / "climate" / "sources.json"
    if climate_sources.exists():
        _merge_climate_sources(climate_sources, registry, errors, rel)

    records = inputs.records
    match = match_openskimap([r for _, r in records], areas, cfg.definitions.matching)

    drafts: list[dict] = []
    members: list[Member] = []
    taken: set[str] = set()
    for batch, rec in records:
        drafts.append(_tier_a_draft(batch, rec, match.by_tier_a.get(rec.id, []),
                                    extract.fetched_date if extract else None))
        members.append(Member(rec.id, rec.state, rec.lat, rec.lon, "A", rec.cluster_hint))
        taken.add(rec.id)
    unknown_state = 0
    for area in sorted(match.unmatched, key=lambda a: (a.state, a.name.lower(), a.osm_id)):
        if area.state not in STATE_NAMES:
            unknown_state += 1
            continue
        rid = _unique_id(slugify(area.name), area.state, taken)
        taken.add(rid)
        drafts.append(_tier_b_draft(area, rid, extract.fetched_date))
        members.append(Member(rid, area.state, area.lat, area.lon, "B"))
    if unknown_state:
        warnings.append(f"skipped {unknown_state} OpenSkiMap areas with an unknown state code")

    try:
        assignment = assign_clusters(members, cfg.clusters, STATE_NAMES)
    except ClusterAssignmentError as e:
        errors += str(e).splitlines()
        assignment = {}

    if errors:
        raise BuildError(errors)

    climate_dir = out_dir / "climate"
    ctx = registry.context()
    resorts: list[Resort] = []
    for d in drafts:
        d["cluster_id"] = assignment[d["id"]]
        d["climate_available"] = (climate_dir / f"{d['id']}.json").is_file()
        d["sources"] = sorted(_refs(d))
        try:
            resorts.append(Resort.model_validate(d, context=ctx))
        except ValidationError as e:
            for m in e.errors():
                errors.append(f"resort '{d['id']}': {'.'.join(map(str, m['loc'])) or '(record)'}: {m['msg']}")
    resorts.sort(key=lambda r: r.id)

    infos: list[ClusterInfo] = build_clusters(members, assignment, cfg.clusters, STATE_NAMES) if members else []
    tier_by_id = {r.id: r.tier for r in resorts}
    clusters: list[Cluster] = []
    for info in infos:
        try:
            clusters.append(Cluster.model_validate({
                "id": info.id, "name": info.name, "states": info.states, "polygon": info.polygon,
                "resort_ids": info.resort_ids,
                "tier_a_count": sum(1 for rid in info.resort_ids if tier_by_id.get(rid) == "A"),
                "overview_md": None, "sources": info.sources,
            }, context=ctx))
        except ValidationError as e:
            for m in e.errors():
                errors.append(f"cluster '{info.id}': {'.'.join(map(str, m['loc']))}: {m['msg']}")
    empty = [c.id for c in cfg.clusters.clusters if c.id not in set(assignment.values())]
    if empty:
        warnings.append(f"{len(empty)} configured clusters have no member resorts yet and are not emitted: {', '.join(empty)}")

    states = [State.model_validate(s) for s in build_states((r.id, r.state, r.cluster_id) for r in resorts)]
    if errors:
        raise BuildError(errors)

    # ------------------------------------------------------------- write
    stamp = generated_at()
    header = {"version": OUTPUT_VERSION, "generated_at": stamp}
    resort_dicts = [r.model_dump(mode="json") for r in resorts]
    _write_json(out_dir / "resorts.json", {**header, "resorts": resort_dicts})
    display = cfg.definitions.display

    def num(s: Optional[dict]) -> Any:
        return s["value"] if s else None

    _write_json(out_dir / "resorts.geojson", {
        "type": "FeatureCollection", **header,
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "id": r["id"], "name": r["name"], "tier": r["tier"], "cluster_id": r["cluster_id"],
                "state": r["state"], "acres": num(r["size"]["acres"]), "vertical_ft": num(r["size"]["vertical_ft"]),
                "lifts": num(r["size"]["lifts"]), "size_rank": display.size_rank(num(r["size"]["acres"])),
            },
        } for r in resort_dicts],
    }, compact=True)
    _write_json(out_dir / "clusters.geojson", {
        "type": "FeatureCollection", **header,
        "features": [{
            "type": "Feature",
            "geometry": c.polygon.model_dump(),
            "properties": {
                "id": c.id, "name": c.name, "states": c.states, "resort_ids": c.resort_ids,
                "tier_a_count": c.tier_a_count, "overview_md": c.overview_md, "sources": c.sources,
            },
        } for c in clusters],
    }, compact=True)
    _write_json(out_dir / "states.json", {**header, "states": [s.model_dump(mode="json") for s in states]})
    _write_json(out_dir / "sources.json", {**header, "sources": [s.model_dump(mode="json") for s in registry.sorted()]})


    result = BuildResult(out_dir, resorts, clusters, states, len(registry), warnings, inputs)
    if write_report:
        from pipeline.tier_a_report import write_report as _write_report
        _write_report(report_path, result, registry, cfg)
    return result


def _refs(d: dict) -> set[str]:
    """source_ids referenced by a resort draft dict (mirrors schemas.referenced_source_ids)."""
    found = {d["location_source_id"]}
    found.update(c["source_id"] for c in d.get("tier_a_criteria") or [])

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if "source_id" in o and "value" in o:
                found.add(o["source_id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for key in ("owner", "size", "elevation", "season", "terrain", "career"):
        walk(d.get(key))
    return found


def run_routes(repo_root: Path) -> Optional[int]:
    try:
        from pipeline.routes import generate_routes
    except ImportError as e:
        print(f"skipping deep link routes: pipeline/routes.py is not available ({e})")
        return None
    n = generate_routes(repo_root)
    print(f"generated {n} deep link route stubs")
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download the OpenSkiMap ski areas file first")
    ap.add_argument("--no-report", action="store_true", help="skip docs/map/tier-a-draft.md")
    ap.add_argument("--no-routes", action="store_true", help="skip pipeline/routes.py deep link stubs")
    args = ap.parse_args(argv)
    try:
        result = build(REPO_ROOT, fetch=args.fetch, write_report=not args.no_report)
    except BuildError as e:
        print(f"BUILD FAILED: {len(e.errors)} validation error(s)", file=sys.stderr)
        for msg in e.errors:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    for w in result.warnings:
        print(f"WARNING: {w}")
    tier_b = len(result.resorts) - result.tier_a_count
    print(
        f"built {len(result.resorts)} resorts ({result.tier_a_count} Tier A, {tier_b} Tier B), "
        f"{len(result.clusters)} clusters, {len(result.states)} states, {result.source_count} sources "
        f"-> {result.out_dir.relative_to(REPO_ROOT) if result.out_dir.is_relative_to(REPO_ROOT) else result.out_dir}"
    )
    if not args.no_routes:
        try:
            run_routes(REPO_ROOT)
        except Exception as e:  # routes are the frontend's; report, but data outputs are already written
            print(f"ERROR: route generation failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
