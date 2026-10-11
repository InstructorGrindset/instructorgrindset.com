"""Load Tier A research batches and ownership files, cross check, match OpenSkiMap."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import ValidationError

from .config import MatchingDefs
from .geo import distance_to_geometry_km
from .names import name_similarity, name_tokens
from .openskimap import SkiArea
from .schemas.common import Source
from .schemas.raw import OwnershipFile, SourcesFile, TierABatch, TierARecord
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
    shared_files = _yaml_files(raw_dir / "sources")
    tier_files = _yaml_files(raw_dir / "tier_a")
    own_files = _yaml_files(raw_dir / "ownership")
    base = repo_root or raw_dir.parent.parent

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(base))
        except ValueError:
            return str(p)

    for p in shared_files:
        try:
            shared = SourcesFile.model_validate(_read_yaml(p))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        except ValidationError as e:
            errors.extend(f"{rel(p)}: {'.'.join(map(str, m['loc']))}: {m['msg']}" for m in e.errors())
            continue
        for src in shared.sources:
            try:
                registry.add(src, rel(p))
            except SourceConflictError as e:
                errors.append(f"{rel(p)}: {e}")

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


def _entry_matches(entry, rec: TierARecord) -> bool:
    if entry.resort_id:
        return entry.resort_id == rec.id
    return entry.state == rec.state and name_similarity(entry.name, rec.name) >= OWNERSHIP_NAME_MIN


def ownership_cross_check(inputs: RawInputs, missing_record: str = "warn") -> tuple[list[str], list[str]]:
    """(errors, warnings).

    * A Tier A resort whose owner names Vail or Alterra must be listed in that
      operator's ownership file (error).
    * Every resort listed in an ownership file needs a Tier A record (ROADMAP
      Section 3, operator compensation). ``missing_record`` is "warn" while
      research is in progress, "fail" once it is complete.
    """
    errors: list[str] = []
    warnings: list[str] = []
    files_by_op: dict[str, list[LoadedOwnership]] = {}
    for lo in inputs.ownership:
        key = verified_operator(lo.file.operator)
        if key:
            files_by_op.setdefault(key, []).append(lo)

    records = [r for _, r in inputs.records]
    for rec in records:
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
        if not any(_entry_matches(e, rec) for lo in files for e in lo.file.resorts):
            errors.append(
                f"Tier A resort '{rec.id}' ({rec.name}, {rec.state}) is owned by {rec.owner.value} "
                f"but is missing from the {VERIFIED_OPERATORS[key]} ownership file"
            )

    for lo in inputs.ownership:
        for entry in lo.file.resorts:
            if not any(_entry_matches(entry, rec) for rec in records):
                msg = (f"{lo.file.operator}: {entry.name} ({entry.state}) is in the ownership file "
                       f"but has no Tier A record")
                (errors if missing_record == "fail" else warnings).append(msg)
    return errors, warnings


@dataclass
class MatchResult:
    by_tier_a: dict[str, list[SkiArea]]
    unmatched: list[SkiArea]
    # Tier A id -> OpenSkiMap names listed in openskimap_names that were not found.
    missing_names: dict[str, list[str]] = field(default_factory=dict)
    # Tier A id -> other areas that passed the rule but were left as Tier B (single best match).
    runners_up: dict[str, list[SkiArea]] = field(default_factory=dict)


def _norm(name: str) -> str:
    return " ".join(name_tokens(name))


def match_openskimap(records: Iterable[TierARecord], areas: Iterable[SkiArea], rule: MatchingDefs) -> MatchResult:
    """Pair OpenSkiMap areas with Tier A records so Tier A is never duplicated.

    Indoor and training center records take part only through an explicit
    ``openskimap_names`` list, so a training center inside a resort never
    takes the resort's area by proximity.

    * A record with ``openskimap_names`` absorbs exactly the areas with those
      names that lie within ``strong_name_radius_km``.
    * Any other record absorbs only its single best area: within ``radius_km``
      (0 when the Tier A point is inside the area polygon) with names at least
      ``name_similarity_min`` alike, or within ``strong_name_radius_km`` with
      names at least ``strong_name_similarity_min`` alike. Each area goes to
      the record it suits best; highest name similarity wins, then distance.
    """
    records = [r for r in records if r.facility_type == "ski_area" or r.openskimap_names]
    areas = list(areas)
    by: dict[str, list[SkiArea]] = {r.id: [] for r in records}
    taken: set[int] = set()
    missing: dict[str, list[str]] = {}

    for r in records:
        if not r.openskimap_names:
            continue
        for wanted in r.openskimap_names:
            hits = [
                i for i, a in enumerate(areas)
                if i not in taken and _norm(a.name) == _norm(wanted)
                and distance_to_geometry_km(r.lat, r.lon, a.geometry, (a.lon, a.lat)) <= rule.strong_name_radius_km
            ]
            if not hits:
                missing.setdefault(r.id, []).append(wanted)
            for i in hits:
                taken.add(i)
                by[r.id].append(areas[i])

    auto = [r for r in records if not r.openskimap_names]
    candidates: dict[str, list[tuple[float, float, int]]] = {r.id: [] for r in auto}
    for i, area in enumerate(areas):
        if i in taken:
            continue
        best: tuple[float, float, str] | None = None
        for r in auto:
            d = distance_to_geometry_km(r.lat, r.lon, area.geometry, (area.lon, area.lat))
            if d > max(rule.radius_km, rule.strong_name_radius_km):
                continue
            sim = name_similarity(r.name, area.name)
            ok = (d <= rule.radius_km and sim >= rule.name_similarity_min) or (
                d <= rule.strong_name_radius_km and sim >= rule.strong_name_similarity_min
            )
            if ok and (best is None or (-sim, d, r.id) < best):
                best = (-sim, d, r.id)
        if best is not None:
            candidates[best[2]].append((best[0], best[1], i))

    runners_up: dict[str, list[SkiArea]] = {}
    for r in auto:
        ranked = sorted(candidates[r.id])
        if ranked:
            taken.add(ranked[0][2])
            by[r.id].append(areas[ranked[0][2]])
        if len(ranked) > 1:
            runners_up[r.id] = [areas[i] for _, _, i in ranked[1:]]

    unmatched = [a for i, a in enumerate(areas) if i not in taken]
    return MatchResult(by_tier_a=by, unmatched=unmatched, missing_names=missing, runners_up=runners_up)
