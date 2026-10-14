"""Median rent across sources: the primary rent for lucrativeness (owner decision 2026-10-14).

Research writes sourced "median rent" figures for each resort's town to data/raw/rent/median_rent_<batch>.yaml
(Zillow, Redfin, Zumper, Rentometer, PadMapper, SFR Analytics, news articles, blogs). For one resort, with the
settings in config ``rent.median``:

1. Figures whose period ended more than ``max_age_months`` (18) months before the build are ignored.
2. The publisher is the registered domain of the figure's source URL (``publishers`` names it). A figure from a
   ``last_resort_publishers`` domain (Foreclosure.com) counts only when no other publisher has a figure, of any
   unit size, for the same town at the same geography level.
3. Only the best geography level with a usable figure counts, in ``geography_order`` (zip, city, county, metro);
   levels are never mixed.
4. Within that level, figures for all unit sizes (``all``) when there are any, else one bedroom (``1br``); the unit
   used is recorded. Two and three bedroom figures stay in the research file but are not used.
5. The median of those figures (one per source page): the middle value, or the mean of the two middle values for
   an even count. Outliers stay as published; the median and the listed sources show them.

The result carries the count, the publishers, every source id, the place, the period range, the oldest ``as_of`` and
the weakest confidence of the figures it used. Contract: docs/map/ARCHITECTURE.md, "Phase 4 career".
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml
from pydantic import ValidationError

from .career import is_stale
from .schemas.rent import MedianRentBatch, MedianRentFigure
from .sources import SourceRegistry

FILE_GLOB = "median_rent*.y*ml"
CONFIDENCE_RANK = {"published": 0, "reported": 1, "estimated": 2}


@dataclass
class MedianRentData:
    figures: list[MedianRentFigure] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)     # source id -> url (the publisher is its domain)
    titles: dict[str, str] = field(default_factory=dict)   # source id -> title

    def for_resort(self, resort_id: str) -> list[MedianRentFigure]:
        return [f for f in self.figures if resort_id in f.resort_ids]


def load_median_rent(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], rel=str
                     ) -> tuple[MedianRentData, list[str]]:
    """Validate data/raw/rent/median_rent*.yaml. Returns (data, errors)."""
    from .tier_a import _register_sources

    errors: list[str] = []
    d = raw_dir / "rent"
    files = sorted(p for p in d.glob(FILE_GLOB) if p.is_file()) if d.is_dir() else []
    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    data = MedianRentData()
    for p in files:
        if not isinstance(raws.get(p), dict):
            continue
        try:
            batch = MedianRentBatch.model_validate(raws[p], context=registry.context())
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                where = rel(p)
                if len(loc) >= 2 and loc[0] == "figures" and isinstance(loc[1], int):
                    try:
                        fig = raws[p]["figures"][loc[1]]
                        where = f"{rel(p)} figures[{loc[1]}] ({fig.get('town')}, {fig.get('source_id')})"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{where}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
            continue
        for i, f in enumerate(batch.figures):
            for rid in f.resort_ids:
                if rid not in known_ids:
                    errors.append(f"{rel(p)} figures[{i}] ({f.town}): resort_id '{rid}' matches no resort in this build")
        data.figures.extend(batch.figures)
    for sid in {f.source_id for f in data.figures}:
        if sid in registry:   # declared in this file or in another research file
            data.urls[sid], data.titles[sid] = registry.get(sid).url, registry.get(sid).title
    return data, errors


# ------------------------------------------------------------------- helpers
def median(values: Sequence[float]) -> float:
    """The middle value; for an even count the mean of the two middle values."""
    if not values:
        raise ValueError("median of no values")
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def period_end(period: str) -> dt.date:
    """Last day covered by a period: '2025' -> 2025-12-31, '2026-02' -> 2026-02-28, '2026-09-30' itself."""
    parts = [int(x) for x in period.split("-")]
    if len(parts) == 1:
        return dt.date(parts[0], 12, 31)
    if len(parts) == 2:
        return dt.date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
    return dt.date(parts[0], parts[1], parts[2])


def is_current(f: MedianRentFigure, today: dt.date, max_age_months: int) -> bool:
    """A figure counts while its period ended no more than ``max_age_months`` months before ``today``."""
    return not is_stale(period_end(f.period), today, max_age_months)


def registered_domain(url: Optional[str]) -> Optional[str]:
    """'https://assets.rentometer.com/x' -> 'rentometer.com'."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    labels = [x for x in host.split(".") if x]
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or None)


def publisher_of(f: MedianRentFigure, urls: dict[str, str]) -> str:
    """Publisher identity of a figure: its source URL's registered domain (the source id when it has no URL)."""
    return registered_domain(urls.get(f.source_id)) or f.source_id


def publisher_label(f: MedianRentFigure, urls: dict[str, str], names: dict[str, str]) -> str:
    """The name shown: ``rent.median.publishers`` for the domain, else the figure's publisher_name, else the domain."""
    dom = publisher_of(f, urls)
    return names.get(dom) or f.publisher_name or dom


def _town_key(town: str) -> str:
    return re.sub(r"\s+", " ", town.strip().lower())


def place_name(figs: Sequence[MedianRentFigure], level: str) -> str:
    """Where the figures are: 'Vail, CO', 'Bozeman metro', 'Breckenridge micro area', 'Municipality of Anchorage'."""
    names = []
    for f in figs:
        town = f.town.strip()
        city = town.split(",")[0].split("(")[0].strip()
        low = town.lower()
        if level == "metro":
            name = f"{city} micro area" if "micro" in low else f"{city} metro"
        elif "municipality" in low:
            name = f"Municipality of {city}"
        else:
            name = re.sub(r"\s*\(.*\)\s*$", "", town)
        names.append(name)
    uniq = list(dict.fromkeys(names))
    return uniq[0] if len(uniq) == 1 else ", ".join(uniq[:-1]) + " and " + uniq[-1]


def usable_figures(data: MedianRentData, today: dt.date, defs: Any) -> list[MedianRentFigure]:
    """Current figures after the last resort rule: a ``last_resort_publishers`` figure is dropped when any other
    publisher (outside that list) has a current figure, of any unit size, for the same town at the same level."""
    current = [f for f in data.figures if is_current(f, today, defs.max_age_months)]
    last = set(defs.last_resort_publishers)
    others = {(_town_key(f.town), f.geography) for f in current if publisher_of(f, data.urls) not in last}
    return [f for f in current
            if publisher_of(f, data.urls) not in last or (_town_key(f.town), f.geography) not in others]


def median_rent_for(resort_id: str, data: MedianRentData, today: dt.date, defs: Any,
                    usable: Optional[list[MedianRentFigure]] = None) -> tuple[Optional[dict], int]:
    """(rent used block or None, figures ignored as older than ``defs.max_age_months``) for one resort.

    ``defs`` is config ``rent.median``. ``usable`` is ``usable_figures(...)`` when the caller has it already."""
    mine = data.for_resort(resort_id)
    ignored = sum(1 for f in mine if not is_current(f, today, defs.max_age_months))
    usable = usable_figures(data, today, defs) if usable is None else usable
    pool = [f for f in usable if resort_id in f.resort_ids and f.unit in defs.units]
    for level in defs.geography_order:
        at_level = [f for f in pool if f.geography == level]
        if not at_level:
            continue
        unit = next(u for u in defs.units if any(f.unit == u for f in at_level))
        used: list[MedianRentFigure] = []
        for f in at_level:   # one figure per source page, in file order
            if f.unit == unit and all(g.source_id != f.source_id for g in used):
                used.append(f)
        value = median([f.median_rent_usd for f in used])
        publishers = list(dict.fromkeys(publisher_label(f, data.urls, defs.publishers) for f in used))
        periods = sorted(f.period for f in used)
        n = len(used)
        place = place_name(used, level)
        return {
            "value": round(value, 2), "basis": "median_rent", "source_ids": [f.source_id for f in used],
            "as_of": min(f.as_of for f in used).isoformat(),
            "confidence": max((f.confidence for f in used), key=lambda c: CONFIDENCE_RANK[c]),
            "note": ("single source" if n == 1 else f"median of {n} sources") + f" ({', '.join(publishers)}) for "
                    + {"all": "all unit sizes", "1br": "one bedroom", "studio": "a studio", "2br": "two bedrooms",
                       "3br": "three bedrooms"}[unit] + f", {level} level, {place}",
            "unit": unit, "count": n, "publishers": publishers,
            "period": {"first": periods[0], "last": periods[-1]},
            "geography": {"level": level, "id": None, "name": place}, "stale": False,
        }, ignored
    return None, ignored


def coverage(data: MedianRentData, resort_ids: Iterable[str], today: dt.date, defs: Any) -> dict[str, dict]:
    """{resort id: rent block} for every resort with a median rent (for reports and tests)."""
    usable = usable_figures(data, today, defs)
    out = {}
    for rid in resort_ids:
        block, _ = median_rent_for(rid, data, today, defs, usable)
        if block:
            out[rid] = block
    return out
