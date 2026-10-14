"""Live webcam links (owner request 2026-10-13).

data/raw/webcams/<batch>.yaml lists, per resort, the resort's own cams page and optionally named cams (a page, a
still image or a stream). The build copies them to Resort.webcams. The site only links to them, in a new tab; it
never embeds, proxies or rehosts an image or a stream.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .links import domain_of, load_batches
from .schemas.webcams import WebcamBatch, WebcamRecord
from .sources import SourceRegistry

RAW_SUBDIR = "webcams"


def load_webcams(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str
                 ) -> tuple[dict[str, WebcamRecord], list[str]]:
    """Validate data/raw/webcams/*.yaml. Returns ({resort_id: record}, errors). No folder gives ({}, [])."""
    batches, errors = load_batches(raw_dir, RAW_SUBDIR, WebcamBatch, registry, rel)
    out: dict[str, WebcamRecord] = {}
    where: dict[str, str] = {}
    for path, batch in batches:
        for r in batch.records:
            label = f"{path} resort '{r.resort_id}'"
            if r.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            if r.resort_id in out:
                errors.append(f"{label}: also has webcams in {where[r.resort_id]}")
            links = ([("cams_page", r.cams_page.url, r.cams_page.source_id)] if r.cams_page else []) + [
                (f"cam '{c.label}'", c.url, c.source_id) for c in r.cams]
            for what, url, source_id in links:
                if source_id not in registry:
                    continue  # reported by the schema
                src = registry.get(source_id)
                if domain_of(url) != domain_of(src.url):
                    errors.append(f"{label}: {what} url domain {domain_of(url)} differs from its source "
                                  f"{domain_of(src.url)}; cite the page that publishes the cam")
            out[r.resort_id] = r
            where[r.resort_id] = path
    return out, errors


def as_output(record: Optional[WebcamRecord], max_cams: int) -> Optional[dict]:
    """Resort.webcams in resorts.json, or None."""
    if record is None:
        return None
    return record.output(max_cams).model_dump(mode="json")
