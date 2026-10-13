"""Official resort trail map links (owner request 2026-10-13).

data/raw/trailmaps_official/<batch>.yaml lists, per resort, the resort's own official trail map page or file
(PDF, JPG or PNG on the resort's or its operator's domain). The build copies the link to Resort.official_trailmap.
The maps themselves are copyrighted art: the site links to them and never downloads, rehosts or embeds them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from .schemas.trailmaps_official import OfficialTrailmapBatch, OfficialTrailmapRecord
from .sources import SourceRegistry

RAW_SUBDIR = "trailmaps_official"


def registered_domain(host: Optional[str]) -> str:
    """Last two labels of a host name ("www.vail.com" -> "vail.com"); enough for the operator domains in use."""
    parts = (host or "").lower().rstrip(".").split(".")
    return ".".join(parts[-2:])


def load_official_trailmaps(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str
                            ) -> tuple[dict[str, OfficialTrailmapRecord], list[str]]:
    """Validate data/raw/trailmaps_official/*.yaml. Returns ({resort_id: record}, errors)."""
    from .tier_a import _register_sources, _yaml_files

    errors: list[str] = []
    files = _yaml_files(raw_dir / RAW_SUBDIR)
    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    out: dict[str, OfficialTrailmapRecord] = {}
    where: dict[str, str] = {}
    for p in files:
        if not isinstance(raws.get(p), dict):
            continue
        try:
            batch = OfficialTrailmapBatch.model_validate(raws[p], context=registry.context())
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                label = rel(p)
                if len(loc) >= 2 and loc[0] == "trailmaps" and isinstance(loc[1], int):
                    try:
                        label = f"{rel(p)} resort '{raws[p]['trailmaps'][loc[1]].get('resort_id')}'"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{label}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
            continue
        sources = {s.id: s for s in batch.sources}
        for r in batch.trailmaps:
            label = f"{rel(p)} resort '{r.resort_id}'"
            if r.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            if r.resort_id in out:
                errors.append(f"{label}: also has an official trail map in {where[r.resort_id]}")
            src = sources.get(r.source_id) or (registry.get(r.source_id) if r.source_id in registry else None)
            if src is not None and registered_domain(urlparse(r.url).hostname) != registered_domain(urlparse(src.url).hostname):
                errors.append(f"{label}: url domain {urlparse(r.url).hostname} differs from its source "
                              f"{urlparse(src.url).hostname}; link only the resort's or operator's own site")
            out[r.resort_id] = r
            where[r.resort_id] = rel(p)
    return out, errors


def as_output(record: Optional[OfficialTrailmapRecord]) -> Optional[dict]:
    """Resort.official_trailmap in resorts.json (the record without resort_id)."""
    if record is None:
        return None
    return record.model_dump(mode="json", exclude={"resort_id"})
