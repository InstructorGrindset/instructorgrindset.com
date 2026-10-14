"""Official resort trail maps (owner requests 2026-10-13 and 2026-10-14).

data/raw/trailmaps_official/<batch>.yaml lists, per resort, the resort's own official trail map page or file
(PDF, JPG or PNG on the resort's or its operator's domain) and, when found, the map file itself (`image_url`: a JPG,
PNG, WebP or PDF on the resort's domain or its operator's image CDN). The build copies both to
Resort.official_trailmap. The maps are copyrighted art: the trail map dialog shows the file straight from the
resort's servers (hotlinked, credited) and the site never downloads, copies, rehosts or modifies it.
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


def image_domain_ok(image_url: str, page_url: str, source_url: Optional[str], cdn_domains) -> bool:
    """The map file sits on the resort's own site (the page url's or the image source's registered domain) or on an
    operator image CDN listed in config `trailmap.official_image_domains`. A listed entry matches its host name and
    every subdomain ("vailresorts.com" covers scene7.vailresorts.com)."""
    host = (urlparse(image_url).hostname or "").lower().rstrip(".")
    own = {registered_domain(urlparse(page_url).hostname)}
    if source_url:
        own.add(registered_domain(urlparse(source_url).hostname))
    if registered_domain(host) in own:
        return True
    return any(host == d or host.endswith("." + d) for d in (str(x).lower().strip(".") for x in cdn_domains or ()))


def load_official_trailmaps(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str,
                            image_domains=()) -> tuple[dict[str, OfficialTrailmapRecord], list[str]]:
    """Validate data/raw/trailmaps_official/*.yaml. Returns ({resort_id: record}, errors). image_domains: the
    operator image CDN host names from config `trailmap.official_image_domains`."""
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
            if r.image_url is not None:
                isrc = sources.get(r.image_source_id) or (registry.get(r.image_source_id) if r.image_source_id in registry else None)
                if isrc is not None and not image_domain_ok(r.image_url, isrc.url, None, image_domains):
                    errors.append(f"{label}: image_url host {urlparse(r.image_url).hostname} differs from its source "
                                  f"{urlparse(isrc.url).hostname}; use the resort's site or a CDN in config "
                                  f"trailmap.official_image_domains")
                if not image_domain_ok(r.image_url, r.url, src.url if src is not None else None, image_domains):
                    errors.append(f"{label}: image_url host {urlparse(r.image_url).hostname} is neither the resort's "
                                  f"site ({urlparse(r.url).hostname}) nor an operator CDN in config "
                                  f"trailmap.official_image_domains")
            out[r.resort_id] = r
            where[r.resort_id] = rel(p)
    return out, errors


def as_output(record: Optional[OfficialTrailmapRecord]) -> Optional[dict]:
    """Resort.official_trailmap in resorts.json (the record without resort_id)."""
    if record is None:
        return None
    return record.model_dump(mode="json", exclude={"resort_id"})
