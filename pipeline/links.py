"""Shared loading for the link only research batches (webcams, jobs): read every YAML file under one
data/raw subfolder, register its sources, validate it with a batch model and report errors per record."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ValidationError

from .sources import SourceRegistry
from .trailmaps_official import registered_domain

__all__ = ["load_batches", "domain_of", "registered_domain"]


def domain_of(url: Optional[str]) -> str:
    """Registered domain (last two labels) of a URL's host."""
    try:
        return registered_domain(urlparse(url or "").hostname)
    except ValueError:
        return ""


def load_batches(raw_dir: Path, subdir: str, model: type[BaseModel], registry: SourceRegistry, rel=str,
                 record_lists: tuple[tuple[str, str], ...] = (("records", "resort_id"),),
                 ) -> tuple[list[tuple[str, BaseModel]], list[str]]:
    """Validate data/raw/<subdir>/*.yaml with ``model``. A missing or empty folder gives no batches and no errors.

    Sources of every file are registered before any file is validated, so a record may cite a source declared in
    another file. ``record_lists`` names the list keys (and the id key inside them) used to label errors.
    Returns ([(file label, batch)], errors).
    """
    from .tier_a import _register_sources, _yaml_files

    errors: list[str] = []
    files = _yaml_files(raw_dir / subdir)
    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        if raws[p] is None:  # an empty file holds nothing yet
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    out: list[tuple[str, BaseModel]] = []
    for p in files:
        raw = raws.get(p)
        if not isinstance(raw, dict):
            continue
        try:
            out.append((rel(p), model.model_validate(raw, context=registry.context())))
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                label = rel(p)
                for key, id_key in record_lists:
                    if len(loc) >= 2 and loc[0] == key and isinstance(loc[1], int):
                        try:
                            item = raw[key][loc[1]]
                            label = f"{rel(p)} {key[:-1] if key.endswith('s') else key} '{item.get(id_key)}'"
                        except (KeyError, IndexError, AttributeError, TypeError):
                            pass
                        loc = loc[2:]
                        break
                errors.append(f"{label}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
    return out, errors
