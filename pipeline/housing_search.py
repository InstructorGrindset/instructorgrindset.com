"""Housing search links per resort (owner request 2026-10-14; ROADMAP item 8c).

data/raw/housing_search/<batch>.yaml lists, per resort, the Furnished Finder search page for its nearest town, and,
per cluster or resort, the housing search tools that sources name for the area. The build copies the link and ranks
the tools into Resort.housing_search. The site only links to them, in a new tab; it never copies, scrapes or embeds
listings.

Ranking (owner decision 2026-10-14, "best means named by the most independent sources"): tools scoped to the resort
first, then tools scoped to its cluster; inside each group the tools named by more independent sources come first,
then by name. Independent sources are counted by publisher: sources whose urls share a registered domain (two Reddit
threads, two pages of one newspaper) count once. A tool listed for the resort and for its cluster (same url) shows
once, in the resort group. At most ``housing_search.max_tools`` tools are kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from .links import domain_of, load_batches
from .schemas.housing_search import FurnishedFinderIn, HousingSearch, HousingSearchBatch, HousingToolIn
from .sources import SourceRegistry

RAW_SUBDIR = "housing_search"


@dataclass
class HousingSearchData:
    furnished_finder: dict[str, FurnishedFinderIn] = field(default_factory=dict)
    resort_tools: dict[str, list[HousingToolIn]] = field(default_factory=dict)
    cluster_tools: dict[str, list[HousingToolIn]] = field(default_factory=dict)


def load_housing_search(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], cluster_ids: set[str],
                        furnished_finder_domains: Iterable[str], rel=str) -> tuple[HousingSearchData, list[str]]:
    """Validate data/raw/housing_search/*.yaml. Returns (data, errors). A missing or empty folder gives no data."""
    batches, errors = load_batches(raw_dir, RAW_SUBDIR, HousingSearchBatch, registry, rel,
                                   record_lists=(("furnished_finder", "resort_id"), ("tools", "name")))
    ff_domains = set(furnished_finder_domains)
    data = HousingSearchData()
    ff_where: dict[str, str] = {}
    tool_where: dict[tuple[str, str, str], str] = {}
    for path, batch in batches:
        for f in batch.furnished_finder:
            label = f"{path} furnished_finder '{f.resort_id}'"
            if f.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            if f.resort_id in data.furnished_finder:
                errors.append(f"{label}: also has a Furnished Finder link in {ff_where[f.resort_id]}")
            if domain_of(f.url) not in ff_domains:
                errors.append(f"{label}: url domain {domain_of(f.url) or '(none)'} is not a Furnished Finder domain "
                              f"({', '.join(sorted(ff_domains))}; config housing_search.furnished_finder_domains)")
            data.furnished_finder[f.resort_id] = f
            ff_where[f.resort_id] = path
        for t in batch.tools:
            label = f"{path} tool '{t.name}'"
            if t.scope == "resort" and t.scope_id not in known_ids:
                errors.append(f"{label}: scope_id '{t.scope_id}' matches no resort in this build")
            if t.scope == "cluster" and t.scope_id not in cluster_ids:
                errors.append(f"{label}: scope_id '{t.scope_id}' matches no cluster in this build")
            key = (t.scope, t.scope_id, url_key(t.url))
            if key in tool_where:
                errors.append(f"{label}: url also listed for {t.scope} '{t.scope_id}' in {tool_where[key]}")
                continue
            tool_where[key] = path
            target = data.resort_tools if t.scope == "resort" else data.cluster_tools
            target.setdefault(t.scope_id, []).append(t)
    return data, errors


def url_key(url: str) -> str:
    """Comparison key for a url: the host in lower case without "www.", the path without a trailing slash, the
    query kept, the scheme and fragment dropped."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = u.path.rstrip("/")
    return f"{host}{path}" + (f"?{u.query}" if u.query else "")


def independent_sources(source_ids: Iterable[str], registry: SourceRegistry) -> int:
    """How many independent sources name a tool: the distinct registered domains of its sources' urls.
    A source without a readable host counts on its own."""
    keys = set()
    for sid in source_ids:
        dom = domain_of(registry.get(sid).url) if sid in registry else ""
        keys.add(dom or f"id:{sid}")
    return len(keys)


def rank_tools(resort_tools: Iterable[HousingToolIn], cluster_tools: Iterable[HousingToolIn],
               registry: SourceRegistry, max_tools: Optional[int] = None) -> list[dict]:
    """Resort scoped tools first, then cluster scoped; each group by independent sources (most first), then name.
    A url already listed in the resort group is dropped from the cluster group."""
    rows = []
    for group, tools in enumerate((resort_tools, cluster_tools)):
        for t in tools:
            n = independent_sources(t.source_ids, registry)
            rows.append(((group, -n, t.name.casefold(), t.name, t.url), n, t))
    rows.sort(key=lambda r: r[0])
    out: list[dict] = []
    seen: set[str] = set()
    for _, n, t in rows:
        k = url_key(t.url)
        if k in seen:
            continue
        seen.add(k)
        out.append({"name": t.name, "kind": t.kind, "url": t.url, "why": t.why, "scope": t.scope, "named_by": n,
                    "source_ids": list(t.source_ids), "as_of": t.as_of.isoformat(), "confidence": t.confidence,
                    "note": t.note})
    return out if max_tools is None else out[:max_tools]


def resort_housing_search(data: HousingSearchData, resort_id: str, cluster_id: Optional[str],
                          registry: SourceRegistry, max_tools: Optional[int] = None) -> Optional[dict]:
    """Resort.housing_search in resorts.json, or None when the resort has no link and no tool."""
    ff = data.furnished_finder.get(resort_id)
    tools = rank_tools(data.resort_tools.get(resort_id, []), data.cluster_tools.get(cluster_id or "", []),
                       registry, max_tools)
    if ff is None and not tools:
        return None
    out = {"furnished_finder": ({"town": ff.town, "url": ff.url, "source_id": ff.source_id,
                                 "as_of": ff.as_of.isoformat(), "confidence": ff.confidence, "note": ff.note}
                                if ff else None),
           "tools": tools}
    return HousingSearch.model_validate(out, context=registry.context()).model_dump(mode="json")
