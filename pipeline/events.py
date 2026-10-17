"""Events and competitions (ROADMAP item 22): data/raw/events/*.yaml -> data/build/events.json.

Contract: docs/map/EVENTS.md (raw files, one per research lane) and docs/map/ARCHITECTURE.md "Events" (the build
output). The map's EVENTS mode loads events.json only when a visitor turns the mode on; events-summary.json (a few
hundred bytes) gives the header's EVENTS pill its count at idle, and Resort.events lists the events a resort hosts
for its panel and its route stub.

Every file is validated (pipeline/schemas/events.py), then checked across files and against the build:

* event, venue and calendar ids are unique across all files (a venue may repeat only with identical fields, the way a
  source id may repeat with the same url); a venue id never equals a resort id;
* an event's venue is exactly one of a resort of this build (``resort_id``) or a venue of some events file
  (``venue_id``);
* discipline, extra disciplines and levels are ids of config ``events``; calendars cite a discipline id and
  ``national`` or a state code; a US venue's state is a US state or territory code and its point lies in the US box;
* an ``edition_2026_27`` starts inside the configured season; a ``last_edition`` starts before it; ``format`` stays
  within ``events.format_max_chars``.

Warnings (the build goes on): a venue no event uses (left out), a venue of kind resort next to a resort of the build
(cite it by resort_id), and an event abroad outside ``events.abroad_groups``.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import EventsDefs
from .links import load_batches
from .schemas.events import CalendarIn, EventIn, EventsBatch, VenueIn
from .sources import SourceRegistry
from .states import STATE_NAMES

RAW_SUBDIR = "events"
OUT_NAME = "events.json"
SUMMARY_NAME = "events-summary.json"
# A loose box around the 50 states (Alaska's Aleutians to Maine, the Florida Keys to the Arctic coast): a point
# outside it is a typo (a swapped sign or swapped coordinates).
US_BOX = {"south": 18.0, "north": 72.0, "west": -180.0, "east": -64.0}


@dataclass
class ResortPlace:
    """What the events build needs from a resort: its name and base area point (resorts.json)."""

    name: str
    state: str
    lat: float
    lon: float
    location_source_id: str


@dataclass
class EventsData:
    venues: dict[str, VenueIn] = field(default_factory=dict)        # used venues only, by id
    events: list[EventIn] = field(default_factory=list)
    calendars: list[CalendarIn] = field(default_factory=list)
    files: int = 0


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _same_venue(a: VenueIn, b: VenueIn) -> bool:
    keep = ("name", "kind", "state", "country", "town")
    return all(getattr(a, k) == getattr(b, k) for k in keep) and \
        abs(a.location.lat - b.location.lat) < 1e-4 and abs(a.location.lon - b.location.lon) < 1e-4


def load_events(raw_dir: Path, registry: SourceRegistry, resorts: dict[str, ResortPlace], defs: EventsDefs,
                rel=str) -> tuple[EventsData, list[str], list[str]]:
    """Validate data/raw/events/*.yaml. Returns (data, errors, warnings); no folder gives empty data and no errors."""
    batches, errors = load_batches(raw_dir, RAW_SUBDIR, EventsBatch, registry, rel,
                                   record_lists=(("events", "id"), ("venues", "id"), ("calendars", "id")))
    warnings: list[str] = []
    data = EventsData(files=len(batches))
    group_of = defs.discipline_group()
    levels = set(defs.ids("levels"))
    season = defs.season

    venue_where: dict[str, str] = {}
    all_venues: dict[str, VenueIn] = {}
    for path, batch in batches:
        assert isinstance(batch, EventsBatch)
        for v in batch.venues:
            label = f"{path} venue '{v.id}'"
            if v.id in resorts:
                errors.append(f"{label}: id is a resort id of this build; cite the resort with venue: {{resort_id: {v.id}}}")
                continue
            if v.id in all_venues:
                if not _same_venue(v, all_venues[v.id]):
                    errors.append(f"{label}: also defined in {venue_where[v.id]} with different fields; "
                                  "a venue id may repeat only with the same name, kind, place and point")
                continue
            if not v.abroad:
                if v.state not in STATE_NAMES:
                    errors.append(f"{label}: state {v.state} is not a US state code")
                loc = v.location
                if not (US_BOX["south"] <= loc.lat <= US_BOX["north"] and US_BOX["west"] <= loc.lon <= US_BOX["east"]):
                    errors.append(f"{label}: location {loc.lat}, {loc.lon} is outside the United States; check the "
                                  "signs and the order (lat, then lon), or set country for a venue abroad")
            if v.kind == "resort" and not v.abroad:
                near = sorted((_km(v.location.lat, v.location.lon, r.lat, r.lon), rid) for rid, r in resorts.items())
                if near and near[0][0] <= defs.resort_match_km:
                    warnings.append(f"{label}: {near[0][0]:.1f} km from resort '{near[0][1]}' ({resorts[near[0][1]].name}) "
                                    f"of this build; cite it by resort_id if it is that resort")
            all_venues[v.id] = v
            venue_where[v.id] = path

    event_where: dict[str, str] = {}
    used: set[str] = set()
    for path, batch in batches:
        for e in batch.events:
            label = f"{path} event '{e.id}'"
            if e.id in event_where:
                errors.append(f"{label}: id also used in {event_where[e.id]}; event ids are unique across all files")
                continue
            event_where[e.id] = path
            bad = False
            if e.discipline not in group_of:
                errors.append(f"{label}: discipline '{e.discipline}' is not a discipline id of config events.groups")
                bad = True
            for d in e.disciplines_also:
                if d not in group_of:
                    errors.append(f"{label}: disciplines_also '{d}' is not a discipline id of config events.groups")
                    bad = True
            for lv in e.levels:
                if lv not in levels:
                    errors.append(f"{label}: level '{lv}' is not a level id of config events.levels")
                    bad = True
            if e.venue.resort_id is not None and e.venue.resort_id not in resorts:
                errors.append(f"{label}: venue resort_id '{e.venue.resort_id}' matches no resort in this build")
                bad = True
            if e.venue.venue_id is not None and e.venue.venue_id not in all_venues:
                errors.append(f"{label}: venue venue_id '{e.venue.venue_id}' is not defined in the venues of any events file")
                bad = True
            if e.format is not None and len(e.format.value) > defs.format_max_chars:
                errors.append(f"{label}: format is {len(e.format.value)} characters; keep it to {defs.format_max_chars}")
                bad = True
            ed = e.edition_2026_27
            if ed is not None and not season.start <= ed.start <= season.end:
                errors.append(f"{label}: edition_2026_27 starts {ed.start}, outside the {season.label} season "
                              f"({season.start} to {season.end}); give it as last_edition or fix the date")
                bad = True
            le = e.last_edition
            if le is not None and le.start is not None and le.start >= season.start:
                errors.append(f"{label}: last_edition starts {le.start}, inside the {season.label} season; "
                              "that is edition_2026_27")
                bad = True
            v = all_venues.get(e.venue.venue_id or "")
            if v is not None and v.abroad and group_of.get(e.discipline) not in defs.abroad_groups:
                warnings.append(f"{label}: venue '{v.id}' is in {v.country}; the contract keeps venues abroad for "
                                f"{', '.join(defs.abroad_groups) or 'no'} events (listed, never mapped)")
            if bad:
                continue
            if e.venue.venue_id:
                used.add(e.venue.venue_id)
            data.events.append(e)

    cal_where: dict[str, str] = {}
    for path, batch in batches:
        for c in batch.calendars:
            label = f"{path} calendar '{c.id}'"
            if c.id in cal_where:
                errors.append(f"{label}: id also used in {cal_where[c.id]}; calendar ids are unique across all files")
                continue
            cal_where[c.id] = path
            if c.discipline not in group_of:
                errors.append(f"{label}: discipline '{c.discipline}' is not a discipline id of config events.groups")
                continue
            if c.region != "national" and c.region not in STATE_NAMES:
                errors.append(f"{label}: region '{c.region}' is neither national nor a US state code")
                continue
            data.calendars.append(c)

    for vid in sorted(set(all_venues) - used):
        if not any(e.venue.venue_id == vid for _, b in batches for e in b.events):
            warnings.append(f"{venue_where[vid]} venue '{vid}': no event uses it; left out")
    data.venues = {vid: all_venues[vid] for vid in sorted(used)}
    return data, errors, warnings


# ---------------------------------------------------------------- compile

def event_dates(e: EventIn) -> tuple[Optional[dt.date], Optional[dt.date], bool, Optional[str]]:
    """(start, end, published, season): the 2026/27 edition when published, else the last edition. start and end
    are None where the source gives no date (an end not seen; a last edition known by its season only)."""
    if e.edition_2026_27 is not None:
        return e.edition_2026_27.start, e.edition_2026_27.end, True, None
    le = e.last_edition
    if le is None:   # a season long program or a date no source gives
        return None, None, False, None
    return le.start, le.end, False, le.season


MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
     "December"])}
MONTH_NAMES.update({name[:3]: i for name, i in list(MONTH_NAMES.items())} | {"Sept": 9})
_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December|" \
         r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"
MONTH_RE = re.compile(rf"\b{_MONTH}\b")
RANGE_RE = re.compile(rf"\b{_MONTH}\s*(?:to|through|until|into|-|\u2013)\s*(?:early |mid |late )?{_MONTH}\b")


def timing_months(text: Optional[str], season_start_month: int = 7) -> list[int]:
    """Months a usual_timing text names, in season order: "Saturdays, January to March" -> [1, 2, 3], "Late
    February" -> [2], "December through February" -> [12, 1, 2]. Month names count only capitalized (as in
    pipeline/hiring.py: "may" is never a month). [] when the text names none."""
    if not text:
        return []
    found: set[int] = set()
    rest = text
    for m in RANGE_RE.finditer(text):
        a, b = MONTH_NAMES[m.group(1)], MONTH_NAMES[m.group(2)]
        k = a
        while True:
            found.add(k)
            if k == b or len(found) >= 12:
                break
            k = k % 12 + 1
        rest = rest.replace(m.group(0), " ")
    found.update(MONTH_NAMES[m.group(1)] for m in MONTH_RE.finditer(rest))
    return sorted(found, key=lambda x: (x - season_start_month) % 12)


def event_months(e: EventIn, season_start_month: int = 7) -> list[int]:
    """The months an event touches: its dates (the start month alone when the end is not known), else the months its
    usual_timing names (a season long program, a last edition known by its season); [] when nothing says."""
    start, end, _, _ = event_dates(e)
    if start:
        return months_between(start, end or start)
    return timing_months(e.usual_timing.value if e.usual_timing else None, season_start_month)


def months_between(start: dt.date, end: dt.date) -> list[int]:
    """Month numbers (1 to 12) a date span touches, in calendar order from its start, each once."""
    out: list[int] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month) and len(out) < 12:
        out.append(m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def season_position(d: dt.date, season_start: dt.date) -> dt.date:
    """The same month and day inside the season that starts on season_start (July to June), for ordering a last
    edition among the season's dates. February 29 maps to February 28 in a common year."""
    year = season_start.year if d.month >= season_start.month else season_start.year + 1
    try:
        return d.replace(year=year)
    except ValueError:
        return dt.date(year, d.month, 28)


def sort_key(e: EventIn, season_start: dt.date) -> tuple:
    """Date order through the season: a dated edition by its start, a last edition by its month and day, and a last
    edition known only by its season at the end; ties by name."""
    start, _, published, _ = event_dates(e)
    when = start if published else (season_position(start, season_start) if start else dt.date.max)
    return (when, 0 if published else 1, e.name.lower(), e.id)


def _dump(model) -> Optional[dict]:
    return model.model_dump(mode="json", exclude_none=True) if model is not None else None


def compile_events(data: EventsData, resorts: dict[str, ResortPlace], defs: EventsDefs, header: dict) -> dict:
    """The events.json payload: vocabulary, venues, events, calendars and counts. Source ids only; the source
    records themselves are in sources.json (the build registers every events source)."""
    group_of = defs.discipline_group()
    events = sorted(data.events, key=lambda e: sort_key(e, defs.season.start))
    venues: dict[str, dict] = {}
    for e in events:
        rid = e.venue.resort_id
        if rid and rid not in venues:
            r = resorts[rid]
            venues[rid] = {"id": rid, "name": r.name, "kind": "resort", "state": r.state, "resort_id": rid,
                           "mapped": True,
                           "location": {"lat": r.lat, "lon": r.lon, "precision": "venue", "source_id": r.location_source_id}}
    for vid, v in data.venues.items():
        out = {"id": v.id, "name": v.name, "kind": v.kind, "state": v.state, "town": v.town, "mapped": not v.abroad,
               "location": _dump(v.location)}
        if v.abroad:
            out["country"] = v.country
        venues[vid] = {k: x for k, x in out.items() if x is not None}

    out_events = []
    by_group = {g.id: 0 for g in defs.groups}
    by_month = {str(m): 0 for m in range(1, 13)}
    dated = 0
    for e in events:
        _, _, published, _ = event_dates(e)
        months = event_months(e, defs.season.start.month)
        d = e.model_dump(mode="json", exclude_none=True, exclude={"venue", "disciplines_also"})
        also = [x for x in dict.fromkeys(e.disciplines_also) if x != e.discipline]
        d.update({"group": group_of[e.discipline], "venue_id": e.venue.resort_id or e.venue.venue_id,
                  "levels": [lv for lv in defs.ids("levels") if lv in e.levels], "months": months})
        if also:
            d["disciplines_also"] = also
        if e.program:
            d["program"] = True   # a season long program (the "Season long" group and chip)
        out_events.append(d)
        by_group[d["group"]] += 1
        for m in months:
            by_month[str(m)] += 1
        dated += published

    calendars = []
    for c in sorted(data.calendars, key=lambda c: ([g.id for g in defs.groups].index(group_of[c.discipline]),
                                                   c.region != "national", c.region, c.name.lower())):
        calendars.append({**c.model_dump(mode="json", exclude_none=True), "group": group_of[c.discipline]})

    used_venues = [venues[k] for k in sorted(venues)]
    return {
        **header,
        "season": {"label": defs.season.label, "start": defs.season.start.isoformat(), "end": defs.season.end.isoformat()},
        "groups": [{"id": g.id, "label": g.label, "color": g.color,
                    "disciplines": [{"id": x.id, "label": x.label} for x in g.disciplines]} for g in defs.groups],
        "levels": [{"id": x.id, "label": x.label} for x in defs.levels],
        "signup_methods": [{"id": x.id, "label": x.label} for x in defs.signup_methods],
        "venue_kinds": [{"id": x.id, "label": x.label} for x in defs.venue_kinds],
        "month_chips": list(defs.month_chips),
        "venues": used_venues,
        "events": out_events,
        "calendars": calendars,
        "counts": {
            "events": len(out_events), "dated": dated, "programs": sum(1 for e in out_events if e.get("program")),
            "venues": sum(1 for v in used_venues if v["mapped"]),
            "venues_abroad": sum(1 for v in used_venues if not v["mapped"]), "calendars": len(calendars),
            "by_group": by_group, "by_month": by_month,
        },
    }


def summary(compiled: dict) -> dict:
    """events-summary.json: the season, the totals and the sorted end dates of the events with a published date in
    the season, so the header pill counts the events still ahead with the viewer's own date."""
    ends = sorted(ed.get("end") or ed["start"] for e in compiled["events"] if (ed := e.get("edition_2026_27")))
    keep = ("version", "generated_at")
    return {**{k: compiled[k] for k in keep if k in compiled}, "season": compiled["season"],
            "events": compiled["counts"]["events"], "calendars": compiled["counts"]["calendars"], "dated_ends": ends}


def resort_events(data: EventsData, defs: EventsDefs) -> dict[str, dict]:
    """Resort.events for every resort that hosts an event: {count, items} with every event at the resort in date
    order (the map season's dated editions first, then last editions by their place in the season)."""
    labels = {d.id: d.label for g in defs.groups for d in g.disciplines}
    group_of = defs.discipline_group()
    out: dict[str, list] = {}
    for e in sorted(data.events, key=lambda e: (0 if e.edition_2026_27 else 1, sort_key(e, defs.season.start))):
        rid = e.venue.resort_id
        if not rid:
            continue
        span = e.edition_2026_27 or e.last_edition
        start, end, published, season = event_dates(e)
        item = {"id": e.id, "name": e.name, "discipline": e.discipline, "discipline_label": labels[e.discipline],
                "group": group_of[e.discipline], "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "season": defs.season.label if published else season, "published": published}
        if span is not None:   # the dates' provenance; an event without a date has none to show
            item.update(source_id=span.source_id, as_of=span.as_of.isoformat(), confidence=span.confidence)
            if span.note:
                item["note"] = span.note
        if e.program:
            cite = e.usual_timing or e.season_long
            item.update(season_long=True, timing=e.usual_timing.value if e.usual_timing else None,
                        source_id=cite.source_id, as_of=cite.as_of.isoformat(), confidence=cite.confidence)
        item = {k: v for k, v in item.items() if v is not None}
        out.setdefault(rid, []).append(item)
    return {rid: {"count": len(items), "items": items} for rid, items in out.items()}


def write(out_dir: Path, compiled: dict, write_json) -> None:
    """events.json (compact) and events-summary.json."""
    write_json(out_dir / OUT_NAME, compiled, compact=True)
    write_json(out_dir / SUMMARY_NAME, summary(compiled), compact=True)


def source_ids(compiled: dict) -> set[str]:
    """Every source id events.json references (for checks and the route stub)."""
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if isinstance(o.get("source_id"), str):
                found.add(o["source_id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(compiled.get("venues"))
    walk(compiled.get("events"))
    walk(compiled.get("calendars"))
    return found
