"""Resort facts overlay: season dates, length, snowfall and size for every resort (Tier A and B).

data/raw/facts/<batch>.yaml records are merged into Resort.season, Resort.snow.annual_avg_in and
Resort.size. Per field the higher confidence wins (published > reported > estimated, from config
display.priority); on a tie the value already on the resort (Tier A YAML or OpenSkiMap) wins.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import yaml
from pydantic import ValidationError

from .schemas.facts import FactsBatch, FactsRecord
from .sources import SourceRegistry

# facts field -> (resort block, field in that block)
TARGETS = {
    "open_typical": ("season", "open_typical"), "close_typical": ("season", "close_typical"),
    "length_days": ("season", "length_days"), "training_start": ("season", "training_start"),
    "annual_snowfall_in": ("snow", "annual_avg_in"),
    "acres": ("size", "acres"), "vertical_ft": ("size", "vertical_ft"), "lifts": ("size", "lifts"),
}


def load_facts(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str
               ) -> tuple[dict[str, FactsRecord], list[str]]:
    """Validate data/raw/facts/*.yaml. Returns ({resort_id: record}, errors)."""
    from .tier_a import _register_sources, _yaml_files

    errors: list[str] = []
    files = _yaml_files(raw_dir / "facts")
    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    out: dict[str, FactsRecord] = {}
    where: dict[str, str] = {}
    for p in files:
        if not isinstance(raws.get(p), dict):
            continue
        try:
            batch = FactsBatch.model_validate(raws[p], context=registry.context())
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                label = rel(p)
                if len(loc) >= 2 and loc[0] == "records" and isinstance(loc[1], int):
                    try:
                        label = f"{rel(p)} resort '{raws[p]['records'][loc[1]].get('resort_id')}'"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{label}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
            continue
        for r in batch.records:
            if r.resort_id not in known_ids:
                errors.append(f"{rel(p)} resort '{r.resort_id}': no such resort in this build")
            if r.resort_id in out:
                errors.append(f"{rel(p)} resort '{r.resort_id}': also has a facts record in {where[r.resort_id]}")
            out[r.resort_id] = r
            where[r.resort_id] = rel(p)
    return out, errors


def merge_facts(draft: dict, record: FactsRecord, priority: Sequence[str]) -> list[str]:
    """Merge one facts record into a resort draft in place. Returns the fields taken from the record."""
    rank = {c: i for i, c in enumerate(priority)}
    taken = []
    for field, (block, key) in TARGETS.items():
        new = getattr(record, field)
        if new is None:
            continue
        holder = draft.get(block) or {}
        old = holder.get(key)
        if old is None or rank[new.confidence] < rank[old["confidence"]]:
            holder[key] = new.model_dump(mode="json")
            draft[block] = holder
            taken.append(field)
    return taken
