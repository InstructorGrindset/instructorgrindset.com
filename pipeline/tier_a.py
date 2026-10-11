"""Load Tier A research batches and ownership files, cross check, match OpenSkiMap."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import ValidationError

from .config import MatchingDefs
from .geo import distance_to_geometry_km
from .names import name_similarity
from .openskimap import SkiArea
from .schemas.common import Source
from .schemas.raw import OwnershipFile, TierABatch, TierARecord
from .sources import SourceConflictError, SourceRegistry

# Owner substrings that require a matching ownership file (ROADMAP Section 3).
VERIFIED_OPERATORS = {"vail": "Vail Resorts", "alterra": "Alterra Mountain Company"}
OWNERSHIP_NAME_MIN = 0.8


class BuildError(Exception):
    """One or more validation problems; ``errors`` lists each one."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class LoadedBatch:
    path: Path
    batch: TierABatch


@dataclass
class LoadedOwnership:
    path: Path
    file: OwnershipFile


@dataclass
class RawInputs:
    batches: list[LoadedBatch] = field(default_factory=list)
    ownership: list[LoadedOwnership] = field(default_factory=list)

    @property
    def records(self) -> list[tuple[TierABatch, TierARecord]]:
        return [(b.batch, r) for b in self.batches for r in b.batch.resorts]


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _describe(err: ValidationError, raw: Any, rel: str) -> list[str]:
    """Readable messages, naming the resort record (by id or name) when possible."""
    out = []
    for e in err.errors():
        loc = list(e["loc"])
        where = rel
        if len(loc) >= 2 and loc[0] == "resorts" and isinstance(loc[1], int):
            try:
                rec = raw["resorts"][loc[1]]
                label = rec.get("id") or rec.get("name") or f"#{loc[1]}"
            except (KeyError, IndexError, TypeError, AttributeError):
                label = f"#{loc[1]}"
            where = f"{rel} resort '{label}'"
            loc = loc[2:]
        field_path = ".".join(str(x) for x in loc) or "(record)"
        out.append(f"{where}: {field_path}: {e['msg']}")
    return out


def _register_sources(raw: Any, rel: str, registry: SourceRegistry, errors: list[str]) -> None:
    if not isinstance(raw, dict):
        errors.append(f"{rel}: expected a mapping at the top level")
        return
    for i, s in enumerate(raw.get("sources") or []):
        try:
            registry.add(Source.model_validate(s), rel)
        except ValidationError as e:
            sid = s.get("id") if isinstance(s, dict) else None
            for m in e.errors():
                errors.append(f"{rel}: sources[{sid or i}].{'.'.join(map(str, m['loc']))}: {m['msg']}")
        except SourceConflictError as e:
            errors.append(f"{rel}: {e}")


def load_raw(raw_dir: Path, registry: SourceRegistry, repo_root: Path | None = None) -> RawInputs:
    """Validate every data/raw/tier_a and data/raw/ownership YAML file.

    Sources from every file are registered first, so a record may cite a
    source declared in another research file. Raises BuildError listing every
    problem found.
    """
    errors: list[str] = []
    tier_files = _yaml_files(raw_dir / "tier_a")
    own_files = _yaml_files(raw_dir / "ownership")
    base = repo_root or raw_dir.parent.parent

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(base))
        except ValueError:
            return str(p)

    raws: dict[Path, Any] = {}
    for p in tier_files + own_files:
        try:
            raws[p] = _read_yaml(p)
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)

    ctx = registry.context()
    out = RawInputs()
    for p in tier_files:
        if p not in raws or not isinstance(raws[p], dict):
            continue
        try:
            out.batches.append(LoadedBatch(p, TierABatch.model_validate(raws[p], context=ctx)))
        except ValidationError as e:
            errors.extend(_describe(e, raws[p], rel(p)))
    for p in own_files:
        if p not in raws or not isinstance(raws[p], dict):
            continue
        try:
            out.ownership.append(LoadedOwnership(p, OwnershipFile.model_validate(raws[p], context=ctx)))
        except ValidationError as e:
            errors.extend(_describe(e, raws[p], rel(p)))

    seen: dict[str, str] = {}
    for b in out.batches:
        for r in b.batch.resorts:
            if r.id in seen:
                errors.append(f"{rel(b.path)} resort '{r.id}': id also used in {seen[r.id]}")
            seen[r.id] = rel(b.path)
    if errors:
        raise BuildError(errors)
    return out


def verified_operator(owner_value: str | None) -> str | None:
    """'vail' or 'alterra' when the owner names a company whose portfolio must be verified."""
    if not owner_value:
        return None
    low = owner_value.lower()
    for key in VERIFIED_OPERATORS:
        if key in low:
            return key
    return None


def ownership_cross_check(inputs: RawInputs) -> tuple[list[str], list[str]]:
    """(errors, warnings). A Tier A resort owned by Vail or Alterra must be listed
    in that operator's ownership file; a listed Tier A candidate without a Tier A
    record is a warning."""
    errors: list[str] = []
    warnings: list[str] = []
    files_by_op: dict[str, list[LoadedOwnership]] = {}
    for lo in inputs.ownership:
        key = verified_operator(lo.file.operator)
        if key:
            files_by_op.setdefault(key, []).append(lo)

    matched_entries: set[tuple[str, str, str]] = set()
    for batch, rec in inputs.records:
        key = verified_operator(rec.owner.value if rec.owner else None)
        if key is None:
            continue
        files = files_by_op.get(key, [])
        if not files:
            errors.append(
                f"Tier A resort '{rec.id}' is owned by {rec.owner.value} but no ownership file "
                f"for {VERIFIED_OPERATORS[key]} exists in data/raw/ownership/"
            )
            continue
        hit = None
        for lo in files:
            for entry in lo.file.resorts:
                if entry.state == rec.state and name_similarity(entry.name, rec.name) >= OWNERSHIP_NAME_MIN:
                    hit = (lo.file.operator, entry.name, entry.state)
                    break
            if hit:
                break
        if hit is None:
            errors.append(
                f"Tier A resort '{rec.id}' ({rec.name}, {rec.state}) is owned by {rec.owner.value} "
                f"but is missing from the {VERIFIED_OPERATORS[key]} ownership file"
            )
        else:
            matched_entries.add(hit)

    for lo in inputs.ownership:
        for entry in lo.file.resorts:
            if entry.tier_a_candidate and (lo.file.operator, entry.name, entry.state) not in matched_entries:
                warnings.append(
                    f"{lo.file.operator}: {entry.name} ({entry.state}) is marked tier_a_candidate "
                    f"but has no Tier A record"
                )
    return errors, warnings


@dataclass
class MatchResult:
    by_tier_a: dict[str, list[SkiArea]]
    unmatched: list[SkiArea]


def match_openskimap(records: Iterable[TierARecord], areas: Iterable[SkiArea], rule: MatchingDefs) -> MatchResult:
    """Pair OpenSkiMap areas with Tier A records so Tier A is never duplicated.

    An area matches a Tier A record when it lies within ``radius_km`` (0 when the
    Tier A point is inside the area polygon) and names are at least
    ``name_similarity_min`` alike, or within ``strong_name_radius_km`` with
    names at least ``strong_name_similarity_min`` alike. Each area goes to its
    best scoring record; a record can absorb several areas.
    """
    records = list(records)
    by: dict[str, list[SkiArea]] = {r.id: [] for r in records}
    unmatched: list[SkiArea] = []
    for area in areas:
        best: tuple[float, float, str] | None = None
        for r in records:
            d = distance_to_geometry_km(r.lat, r.lon, area.geometry, (area.lon, area.lat))
            if d > max(rule.radius_km, rule.strong_name_radius_km):
                continue
            sim = name_similarity(r.name, area.name)
            ok = (d <= rule.radius_km and sim >= rule.name_similarity_min) or (
                d <= rule.strong_name_radius_km and sim >= rule.strong_name_similarity_min
            )
            if ok:
                cand = (-sim, d, r.id)
                if best is None or cand < best:
                    best = cand
        if best is None:
            unmatched.append(area)
        else:
            by[best[2]].append(area)
    return MatchResult(by_tier_a=by, unmatched=unmatched)
