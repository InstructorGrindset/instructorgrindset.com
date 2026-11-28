# Snowkite layer: data contract

Owner request (2026-11-28): a map layer showing snowkite zones and events, with a zone report for each zone. Then
mark every outstandingly ideal snowkite zone in the United States from snow and ice conditions, wind, terrain and
access, and build on that to suggest multiday snowkite expeditions across the US and Alaska.

The project rules in `CLAUDE.md` apply in full. Every displayed value carries `source_id`, `as_of` and `confidence`.
A value that cannot be sourced is `null`. Individual people are never listed. Editorial text (summaries, advice,
expedition write ups) is drafted only from cited sources and stays `status: draft` until Alexandre marks it
`reviewed`; drafts never render in production. A score is a computed value, never a fact: it carries
`confidence: estimated` and shows its inputs.

## Phases

| Phase | What | Data |
|---|---|---|
| 24 | Snowkite layer: established zones, snowkite events, zone reports | `data/raw/snowkite/zones_*.yaml`, `data/raw/events/snowkite*.yaml` |
| 25 | Ideal zones: criteria from sources, a score from sourced inputs, every outstanding zone marked | `docs/map/snowkite-criteria.md`, `config/definitions.yaml` `snowkite`, `data/raw/snowkite/candidates_*.yaml` |
| 26 | Multiday expeditions across the US and Alaska | `data/raw/snowkite/expeditions_*.yaml` |

## Raw zone files: `data/raw/snowkite/zones_<lane>.yaml`

```yaml
batch: snowkite-zones-<lane>
researched_date: 2026-11-28
sources:
- id: snowkite-<lane>-<topic>          # unique across all snowkite files
  url: https://...
  title: Page title as shown
  accessed_date: 2026-11-28
  note: From a search result for this page; page not opened.   # only when it was not opened
zones:
- id: mille-lacs-lake-mn               # lowercase words and hyphens, stable
  name: Mille Lacs Lake
  status: established                  # established (kiters use it, a source says so) | candidate (phase 25)
  kind: lake                           # lake | reservoir | plateau | valley | glacier | prairie | dunes | alpine_basin | river | other
  state: MN                            # two letter code; for an area crossing states, the state of the center
  location: {lat: 46.2, lon: -93.6, precision: area, source_id: ..., as_of: ..., confidence: ...}   # precision: spot | area | region
  elevation_ft: {value: 1250, source_id: ..., as_of: ..., confidence: ...}     # optional
  surface: [frozen_lake]               # frozen_lake | open_snowfield | glacier | alpine_basin | prairie | frozen_river | dunes_snow
  months: {value: [12, 1, 2, 3], note: "Ice usually safe January to early March", source_id: ..., as_of: ..., confidence: ...}
  wind: {value: "Open fetch in every direction; north and northwest winds after cold fronts are the usual kite wind", source_id: ..., as_of: ..., confidence: ...}   # facts only, at most 240 characters
  snow_ice: {value: "...", source_id: ..., as_of: ..., confidence: ...}       # at most 240 characters
  terrain: {value: "...", source_id: ..., as_of: ..., confidence: ...}        # at most 240 characters
  hazards: [{value: "Pressure ridges and open water near river inlets", source_id: ..., as_of: ..., confidence: ...}]
  access: {road: "Public landing off Highway 169", parking: "...", fees: "...", permit: "...", source_id: ..., as_of: ..., confidence: ...}   # every field optional, sourced as a block
  website: {value: https://..., source_id: ..., as_of: ..., confidence: ...}    # a club, school or official page
  events: [event ids from data/raw/events/*.yaml]                                # optional links
  report:
    status: draft                      # draft | reviewed; only reviewed renders in production
    summary: "Two or three plain sentences from the cited sources."
    source_ids: [...]
```

* Facts (wind, snow_ice, terrain, hazards, access) render as soon as they pass validation. Only the `report.summary`
  waits for review. A zone with no `report` still shows its facts.
* A zone needs at least one source that names snowkiting there. A road or a lake that merely looks windy is a
  candidate, not an established zone.
* Coordinates: `spot` when a source names the launch or parking point, `area` for the lake or plateau center from
  Wikipedia or an official page, `region` for a broad range.

## Events

Snowkite events use the existing contract (`docs/map/EVENTS.md`) in `data/raw/events/snowkite*.yaml`, discipline
`snowkiting`, venue a zone's place (a resort id when the event is at a resort, else a `venues:` record). Adults only.
A zone's `events:` list links its events by id.

## Candidates, scoring and expeditions (phases 25 and 26)

Written when those phases start, after `docs/map/snowkite-criteria.md` fixes what the sources say makes a good spot.
Every threshold and weight lives in `config/definitions.yaml` `snowkite`; wind comes from ERA5 10 m winds computed
for each zone (a computed value, `confidence: estimated`); snow from SNOTEL and NOAA normals already in
`data/raw`; terrain from the Terrain Tiles extract; access from sourced research.
