"""Median rent across sources: the primary rent for lucrativeness (owner decision 2026-10-14).

Research writes sourced "median rent" figures for each resort's town to data/raw/rent/median_rent_<batch>.yaml
(Zillow, Redfin, Apartments.com, RentCafe, Rent.com, Zumper, news articles, blogs). For one resort:

1. Figures whose period ended more than ``rent.median.max_age_months`` (18) months before the build are ignored.
2. Units in ``rent.median.units`` order: figures for all unit sizes (``all``) when any exist, else one bedroom
   (``1br``). The unit used is recorded.
3. One figure per source: the most specific geography (``rent.median.geography_order``: city, zip, county, metro),
   then the latest period, then the order in the file.
4. The median of those figures: the middle value, or the mean of the two middle values for an even count.

The result carries the count, the publishers, every source id, the period range, the oldest ``as_of`` and the
weakest confidence of the figures it used. Contract: docs/map/ARCHITECTURE.md, "Phase 4 career".
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml
from pydantic import ValidationError

from .career import is_stale
from .schemas.rent import MedianRentBatch, MedianRentFigure
from .sources import SourceRegistry

FILE_GLOB = "median_rent*.y*ml"
PUBLISHER_LABELS = {"zillow": "Zillow", "redfin": "Redfin", "apartments_com": "Apartments.com",
                    "rentcafe": "RentCafe", "rent_com": "Rent.com", "zumper": "Zumper"}
CONFIDENCE_RANK = {"published": 0, "reported": 1, "estimated": 2}
UNIT_WORDS = {"all": "all unit sizes", "studio": "a studio", "1br": "one bedroom", "2br": "two bedrooms",
              "3br": "three bedrooms"}


@dataclass
class MedianRentData:
    figures: list[MedianRentFigure] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)   # source id -> title (publisher name fallback)

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
        for s in batch.sources:
            data.titles[s.id] = s.title
    for sid in {f.source_id for f in data.figures} - set(data.titles):
        if sid in registry:   # cited from another research file
            data.titles[sid] = registry.get(sid).title
    return data, errors


# ------------------------------------------------------------------- the math
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


def publisher_label(f: MedianRentFigure, titles: dict[str, str]) -> str:
    """'Zillow', 'Redfin', ...; for news, blog and other the figure's publisher_name, else its source title."""
    if f.publisher in PUBLISHER_LABELS:
        return PUBLISHER_LABELS[f.publisher]
    return f.publisher_name or titles.get(f.source_id) or f.source_id


def median_rent_for(figures: Iterable[MedianRentFigure], today: dt.date, defs: Any,
                    titles: Optional[dict[str, str]] = None) -> tuple[Optional[dict], int]:
    """(rent used block or None, figures ignored as older than ``defs.max_age_months``) for one resort's figures.

    ``defs`` is config ``rent.median`` (units, geography_order, max_age_months)."""
    titles = titles or {}
    figs = list(figures)
    current = [f for f in figs if is_current(f, today, defs.max_age_months)]
    ignored = len(figs) - len(current)
    geo_rank = {g: i for i, g in enumerate(defs.geography_order)}
    for unit in defs.units:
        cands = [(i, f) for i, f in enumerate(current) if f.unit == unit]
        if not cands:
            continue
        best: dict[str, tuple[int, MedianRentFigure]] = {}
        for i, f in cands:
            key = (geo_rank.get(f.geography, len(geo_rank)), -period_end(f.period).toordinal(), i)
            old = best.get(f.source_id)
            if old is None or key < (geo_rank.get(old[1].geography, len(geo_rank)),
                                     -period_end(old[1].period).toordinal(), old[0]):
                best[f.source_id] = (i, f)
        used = [f for _, f in sorted(best.values(), key=lambda t: t[0])]
        value = median([f.median_rent_usd for f in used])
        publishers = list(dict.fromkeys(publisher_label(f, titles) for f in used))
        periods = sorted(f.period for f in used)
        conf = max((f.confidence for f in used), key=lambda c: CONFIDENCE_RANK[c])
        n = len(used)
        return {
            "value": round(value, 2), "basis": "median_rent", "source_ids": [f.source_id for f in used],
            "as_of": min(f.as_of for f in used).isoformat(), "confidence": conf,
            "note": (f"median of {n} sources" if n > 1 else "1 source")
                    + f" for {UNIT_WORDS[unit]}: " + ", ".join(publishers),
            "unit": unit, "count": n, "publishers": publishers,
            "period": {"first": periods[0], "last": periods[-1]}, "geography": None, "stale": False,
        }, ignored
    return None, ignored
