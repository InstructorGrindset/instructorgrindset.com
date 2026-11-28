"""Snowkite layer (ROADMAP item 24): data/raw/snowkite/zones_*.yaml -> data/build/snowkite.json.

Contract: docs/map/SNOWKITE.md (the raw files, one per research lane) and docs/map/ARCHITECTURE.md "Snowkite layer"
(the build output). The map loads snowkite.json, with the layer's script and style sheet, only when a visitor turns
the Snowkite layer on or opens a link to it.

Every file is validated (pipeline/schemas/snowkite.py), then checked across files and against the build:

* zone ids are unique across files; every source a file declares is used by some zone, and every source a zone
  cites is declared (the schema checks that one);
* an established zone cites something that names snowkiting (a config ``snowkite.source_words`` word in a source's title, address or note, or in a cited text of the zone); a state is a US state code; the point lies in the lower 48 box (any state but
  Alaska) or the Alaska box (Alaska), so a swapped sign or a swapped latitude and longitude is an error;
* text stays within ``snowkite.limits`` (wind, snow and ice, terrain, each hazard, each access field, the months
  note and the report summary);
* a zone's ``events`` ids are events of data/raw/events. An event joins a zone when the zone lists it or the event's
  venue id equals the zone id or starts with it. A zone may have no location (no marker), no surface and no months.

Warnings (the build goes on): no ``data/raw/snowkite`` folder or no zone file (an empty file is written), a candidate
zone (phase 25, left out for now), a listed event with its venue far from the zone or abroad, a snowkite event near
a zone that does not list it, and report summaries still in draft.

The output has the zone facts and their sources, never a draft summary: a report summary is written only when its
status is ``reviewed`` (drafts never render in production), and ``summaries_waiting`` says how many wait for review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from .config import SnowkiteDefs
from .events import EventsData, ResortPlace, _km, event_dates
from .schemas.snowkite import ZoneIn, ZonesBatch
from .sources import SourceRegistry
from .states import STATE_NAMES

RAW_SUBDIR = "snowkite"
FILE_GLOB = "zones_*.yaml"
OUT_NAME = "snowkite.json"
# Keys of an event kept in the layer's file: enough for its line in a zone report and its mark on the map.
EVENT_KEYS = ("id", "name", "discipline", "series", "organizer", "format", "edition_2026_27", "last_edition",
              "usual_timing", "season_long", "signup", "website", "months", "program")


@dataclass
class SnowkiteData:
    zones: list[ZoneIn] = field(default_factory=list)     # established zones, in file order
    candidates: int = 0                                    # candidate zones left out (phase 25)
    files: int = 0
    files_seen: list[str] = field(default_factory=list)


def _label(path: str, z: Any) -> str:
    return f"{path} zone '{z}'"


def event_point(e, events: EventsData, places: dict[str, ResortPlace]) -> Optional[tuple[float, float]]:
    """The (lat, lon) of an event's venue: a resort of the build or a venue of an events file (None: not found)."""
    if e.venue.resort_id and e.venue.resort_id in places:
        r = places[e.venue.resort_id]
        return r.lat, r.lon
    v = events.venues.get(e.venue.venue_id or "")
    return (v.location.lat, v.location.lon) if v is not None else None


def _in_region(defs: SnowkiteDefs, state: str, lat: float, lon: float) -> bool:
    box = defs.regions.alaska if state == "AK" else defs.regions.lower48
    return box.contains(lat, lon)


def _check_zone(path: str, z: ZoneIn, defs: SnowkiteDefs, registry: SourceRegistry) -> list[str]:
    """Every rule that needs only the zone and the config."""
    errors: list[str] = []
    lim = defs.limits
    label = _label(path, z.id)

    def text(what: str, value: Optional[str], limit: int) -> None:
        if value is not None and len(value) > limit:
            errors.append(f"{label}: {what} is {len(value)} characters; keep it to {limit}")

    text("name", z.name, lim.name_max_chars)
    if z.state not in STATE_NAMES:
        errors.append(f"{label}: state {z.state} is not a US state code")
    loc = z.location
    if loc is not None and z.state in STATE_NAMES and not _in_region(defs, z.state, loc.lat, loc.lon):
        where = "Alaska" if z.state == "AK" else "the lower 48 states"
        errors.append(f"{label}: location {loc.lat}, {loc.lon} is outside {where} (state {z.state}); check the signs "
                      "and the order (lat, then lon)")
    for key in ("wind", "snow_ice", "terrain"):
        block = getattr(z, key)
        if block is not None:
            text(key, block.value, lim.text_max_chars)
    if len(z.hazards) > lim.hazards_max:
        errors.append(f"{label}: {len(z.hazards)} hazards; keep it to {lim.hazards_max}")
    for i, hz in enumerate(z.hazards):
        text(f"hazards[{i}]", hz.value, lim.hazard_max_chars)
    if z.access is not None:
        for key in ("road", "parking", "fees", "permit"):
            text(f"access.{key}", getattr(z.access, key), lim.access_field_max_chars)
    if z.months is not None:
        text("months note", z.months.note, lim.months_note_max_chars)
    if z.report is not None:
        text("report summary", z.report.summary, lim.summary_max_chars)
    if z.status == "established":
        words = defs.source_words
        texts = [z.report.summary] if z.report is not None else []
        texts += [b.value for b in (z.wind, z.snow_ice, z.terrain) if b is not None] + [h.value for h in z.hazards]
        texts += [b.note or "" for b in z.cited()]
        for sid in sorted(z.source_ids()):
            if sid in registry:
                s = registry.get(sid)
                texts.append(f"{s.title} {s.url} {s.note or ''}")
        hay = " ".join(texts).lower()
        if not any(w in hay for w in words):
            errors.append(f"{label}: nothing the zone cites names snowkiting (a source title, address or note, or a cited "
                          f"text, with one of {', '.join(words)}); an established zone needs it, else make it a candidate")
    return errors
    return errors


def load_snowkite(raw_dir: Path, registry: SourceRegistry, events: EventsData, places: dict[str, ResortPlace],
                  defs: SnowkiteDefs, rel=str) -> tuple[SnowkiteData, list[str], list[str]]:
    """Validate data/raw/snowkite/zones_*.yaml. Returns (data, errors, warnings). A missing folder or no zone file
    gives empty data, no errors and one warning."""
    import yaml
    from .tier_a import _register_sources

    data = SnowkiteData()
    errors: list[str] = []
    warnings: list[str] = []
    folder = raw_dir / RAW_SUBDIR
    files = sorted(p for p in folder.glob(FILE_GLOB) if p.is_file()) if folder.is_dir() else []
    if not files:
        warnings.append(f"no snowkite zone files in {rel(folder)} ({FILE_GLOB}); an empty snowkite.json is written")
        return data, errors, warnings

    raws: dict[Path, Any] = {}
    for p in files:
        try:
            raws[p] = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        if raws[p] is None:   # an empty file holds nothing yet
            continue
        _register_sources(raws[p], rel(p), registry, errors)

    batches: list[tuple[str, ZonesBatch]] = []
    for p in files:
        raw = raws.get(p)
        if not isinstance(raw, dict):
            continue
        try:
            batches.append((rel(p), ZonesBatch.model_validate(raw, context=registry.context())))
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                label = rel(p)
                if len(loc) >= 2 and loc[0] == "zones" and isinstance(loc[1], int):
                    try:
                        label = f"{rel(p)} zone '{raw['zones'][loc[1]].get('id')}'"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{label}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
    data.files = len(batches)
    data.files_seen = [path for path, _ in batches]

    event_by_id = {e.id: e for e in events.events}
    snowkite_events = [e for e in events.events if e.discipline == defs.event_discipline]
    seen: dict[str, str] = {}
    used_sources: set[str] = set()
    listed: set[str] = set()
    for path, batch in batches:
        for z in batch.zones:
            used_sources |= z.source_ids()
            if z.id in seen:
                errors.append(f"{_label(path, z.id)}: id also used in {seen[z.id]}; zone ids are unique across files")
                continue
            seen[z.id] = path
            errs = _check_zone(path, z, defs, registry)
            for eid in z.events:
                e = event_by_id.get(eid)
                if e is None:
                    errs.append(f"{_label(path, z.id)}: events lists '{eid}', which is no event of data/raw/events")
                    continue
                pt = event_point(e, events, places)
                if pt is None:
                    warnings.append(f"{_label(path, z.id)}: event '{eid}' is at a venue abroad; it is not mapped")
                    continue
                km = _km(z.location.lat, z.location.lon, pt[0], pt[1]) if z.location is not None else 0.0
                if km > defs.event_zone_km:
                    warnings.append(f"{_label(path, z.id)}: event '{eid}' is {km:.0f} km from the zone point "
                                    f"(limit {defs.event_zone_km:g} km); check that the zone is the venue")
                listed.add(eid)
            errors += errs
            if z.status == "candidate":
                data.candidates += 1
                continue
            if not errs:
                data.zones.append(z)
    # An event whose venue id equals a zone id or starts with it belongs to that zone (owner rule 2026-11-28).
    for z in data.zones:
        for e in events.events:
            vid = e.venue.venue_id or e.venue.resort_id or ""
            if e.id not in z.events and (vid == z.id or vid.startswith(z.id)):
                z.events.append(e.id)
                listed.add(e.id)
    for path, batch in batches:
        for s in batch.sources:
            if s.id not in used_sources:
                errors.append(f"{path} source '{s.id}': no zone cites it; remove it or cite it")
    nopoint = sum(1 for z in data.zones if z.location is None)
    if nopoint:
        warnings.append(f"{nopoint} zone(s) have no location; they list and search with a no map point note and draw no marker")
    if data.candidates:
        warnings.append(f"{data.candidates} candidate zone(s) are left out of snowkite.json (they arrive with phase 25)")
    # A snowkite event next to a zone that does not list it: the layer shows it at its venue; the zone report only
    # lists the events the zone names.
    for e in snowkite_events:
        pt = event_point(e, events, places)
        if pt is None or e.id in listed:
            continue
        near = sorted((_km(z.location.lat, z.location.lon, pt[0], pt[1]), z.id) for z in data.zones if z.location is not None)
        if near and near[0][0] <= defs.event_zone_km:
            warnings.append(f"snowkite event '{e.id}' is {near[0][0]:.0f} km from zone '{near[0][1]}' but no zone lists "
                            "it; add its id to the zone's events if it is held there")
    waiting = sum(1 for z in data.zones if z.report is not None and z.report.status == "draft")
    if waiting:
        warnings.append(f"{waiting} zone report summar{'y' if waiting == 1 else 'ies'} wait for review (status draft)")
    return data, errors, warnings


# ---------------------------------------------------------------- compile

def _dump(model) -> Optional[dict]:
    return model.model_dump(mode="json", exclude_none=True) if model is not None else None


def zone_dict(z: ZoneIn) -> dict:
    """One zone as snowkite.json holds it: facts with their sources; the summary only when reviewed."""
    out: dict[str, Any] = {"id": z.id, "name": z.name, "kind": z.kind, "state": z.state, "surface": list(z.surface)}
    if z.location is not None:
        out["location"] = _dump(z.location)
    for key in ("elevation_ft", "months", "wind", "snow_ice", "terrain", "access", "website"):
        d = _dump(getattr(z, key))
        if d is not None:
            out[key] = d
    if z.hazards:
        out["hazards"] = [_dump(h) for h in z.hazards]
    if z.events:
        out["events"] = list(z.events)
    if z.report is not None and z.report.status == "reviewed":
        out["report"] = {"summary": z.report.summary, "source_ids": list(z.report.source_ids)}
    return out


def _event_dict(e: dict, venue: dict) -> dict:
    d = {k: e[k] for k in EVENT_KEYS if k in e}
    d["venue"] = {k: venue[k] for k in ("id", "name", "state", "town", "location") if k in venue}
    return d


def compile_snowkite(data: SnowkiteData, defs: SnowkiteDefs, events_compiled: Optional[dict], header: dict) -> dict:
    """The snowkite.json payload. events_compiled is the events.json payload (events.compile_events), or None."""
    state_name = STATE_NAMES
    zones = sorted(data.zones, key=lambda z: (state_name.get(z.state, z.state), z.name.lower(), z.id))
    listed = {eid for z in zones for eid in z.events}
    venues = {v["id"]: v for v in (events_compiled or {}).get("venues", [])}
    out_events: list[dict] = []
    for e in (events_compiled or {}).get("events", []):
        if e["discipline"] != defs.event_discipline and e["id"] not in listed:
            continue
        v = venues.get(e["venue_id"])
        if v is None or not v.get("mapped"):
            continue   # a venue abroad is listed in events.json only
        out_events.append(_event_dict(e, v))
    event_ids = {e["id"] for e in out_events}
    zone_dicts = []
    for z in zones:
        d = zone_dict(z)
        if "events" in d:
            d["events"] = [x for x in d["events"] if x in event_ids]
            if not d["events"]:
                del d["events"]
        zone_dicts.append(d)
    waiting = sum(1 for z in zones if z.report is not None and z.report.status == "draft")
    return {
        **header,
        "kinds": [{"id": k.id, "label": k.label} for k in defs.kinds],
        "surfaces": [{"id": s.id, "label": s.label, "color": s.color} for s in defs.surfaces],
        "month_order": list(defs.month_order),
        "season": (events_compiled or {}).get("season", {}).get("label"),   # the events season, for date wording
        "states": {c: state_name[c] for c in sorted({z.state for z in zones}) if c in state_name},
        "zones": zone_dicts,
        "events": out_events,
        "summaries_waiting": waiting,
        "counts": {
            "zones": len(zone_dicts), "events": len(out_events),
            "reports": sum(1 for z in zone_dicts if "report" in z), "summaries_waiting": waiting,
            "by_surface": {s.id: sum(1 for z in zones if s.id in z.surface) for s in defs.surfaces},
            "by_state": {c: sum(1 for z in zones if z.state == c) for c in sorted({z.state for z in zones})},
        },
    }


def write(out_dir: Path, compiled: dict, write_json) -> None:
    write_json(out_dir / OUT_NAME, compiled, compact=True)


def source_ids(compiled: dict) -> set[str]:
    """Every source id snowkite.json references."""
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if isinstance(o.get("source_id"), str):
                found.add(o["source_id"])
            if isinstance(o.get("source_ids"), list):
                found.update(s for s in o["source_ids"] if isinstance(s, str))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(compiled.get("zones"))
    walk(compiled.get("events"))
    return found
