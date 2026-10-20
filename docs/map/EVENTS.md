# Events and competitions: raw data contract

Owner request (2026-10-17): an Events filter on the map showing every snowsports venue, competition and event in
the United States, for every discipline, with how to sign up. This file is the contract between the research lanes
(which write `data/raw/events/*.yaml`) and the pipeline (`pipeline/events.py`, which compiles `data/build/events.json`).

The project rules in `CLAUDE.md` apply in full: every displayed value carries `source_id`, `as_of` and
`confidence`; nothing is invented; a value that cannot be sourced is left out or `null`; individual people are never
listed (organizations, clubs and venues only).

## Scope

* United States venues go on the map. One exception: international instructor events (Interski and its US team
  selection abroad) may use a venue outside the US with `country:` set; they show in the events list and calendar,
  not as a map dot.
* Adults only (owner rule, 2026-10-20): every event must be one an adult 18 or over can enter as a competitor or
  participant. Events limited to under 18 (junior championships, youth tours, kids' classes) are out. An event with
  youth and adult classes stays, without a youth level. There is no `junior` level id.
* Community competitions count as much as pro events: local rail jams, bump and mogul contests, lap and vertical
  challenges (for example Gunbarrel 25 at Heavenly), resort race leagues, fun races and any contest open to the
  public. Give them level `open_amateur`.
* Snow based sport: every ski and snowboard discipline, snowshoe, skijoring, snowkiting, sled dog racing,
  snowmobile racing, fat bike snow racing and winter ultras on snow, heritage events (longboard and retro races), and
  instructor education events (PSIA-AASI). Ice only sport (hockey, skating, curling, bobsled, luge, skeleton) is out.
* Competitions and participatory events (races, series, rallies, festivals built around a competition, exams and
  clinics for instructors). Plain resort parties and concerts are out.
* The 2026/27 season first (events between 2026-07-01 and 2027-06-30). Events whose 2026/27 date is not published
  still go in with their last edition's dates, labelled as such.

## Confidence

* `published`: read on the organizer's or sanctioning body's own page, opened.
* `reported`: read on an opened third party page (Wikipedia, a news article).
* `estimated`: seen only in a web search result. Note: `From a search result for this page; page not opened.`

## File layout

One file per research lane: `data/raw/events/<lane>.yaml`. Lanes never edit each other's files.

```yaml
batch: events-<lane>
researched_date: 2026-10-17
sources:
- id: events-birkie-2027-search          # unique across all event files; prefix with the lane or the event
  url: https://www.birkie.com/...
  title: Page title as shown
  accessed_date: 2026-10-17
  note: From a search result for this page; page not opened.
venues:                                  # only venues that are NOT a resort in data/build/resorts.json
- id: hayward-wi-birkie-trail            # lowercase, words joined with hyphens
  name: American Birkebeiner Trail
  kind: trail                            # resort | nordic_center | trail | town | lake | park | fairground | arena | jump_complex | other
  state: WI                              # two letter state; null for a venue outside the US
  country: US                            # optional; ISO two letter code, set for venues outside the US (list only)
  town: Cable                            # the nearest town as the source names it
  location:
    lat: 46.2073
    lon: -91.2900
    precision: venue                     # venue (the venue itself) | town (town center) | region
    source_id: wikipedia-cable-wisconsin
    as_of: 2026-10-17
    confidence: reported
    note: Town center of Cable, Wisconsin, from Wikipedia.
events:
- id: american-birkebeiner               # the series id; stable from year to year
  name: American Birkebeiner
  discipline: ski_marathon               # one id from the list below
  disciplines_also: [cross_country]      # optional extra ids
  levels: [open_amateur, elite]          # ids from the list below
  venue: {resort_id: null, venue_id: hayward-wi-birkie-trail}   # exactly one of the two
  organizer: {name: American Birkebeiner Ski Foundation, url: https://www.birkie.com/, source_id: ..., as_of: ..., confidence: ...}
  sanctioning: {name: Worldloppet, source_id: ..., as_of: ..., confidence: ...}   # optional
  format: {value: "50 km classic and skate races from Cable to Hayward", source_id: ..., as_of: ..., confidence: ...}   # optional, at most 160 characters, facts only
  edition_2026_27: {start: 2027-02-25, end: 2027-02-27, source_id: ..., as_of: ..., confidence: ...}   # null when not published
  last_edition: {start: 2026-02-19, end: 2026-02-21, season: 2025/26, source_id: ..., as_of: ..., confidence: ...}   # give when edition_2026_27 is null; optional otherwise
  usual_timing: {value: "Late February", source_id: ..., as_of: ..., confidence: ...}   # optional, only when a source says it
  season_long: {value: true, source_id: ..., as_of: ..., confidence: ...}   # optional: a program that runs all season with no single date
  # Season long programs (a NASTAR program, a weekly race league, rail jam nights) may leave both edition_2026_27 and
  # last_edition null when usual_timing (for example "Thursday nights, January to March") or season_long is sourced.
  signup:
    method: online_registration          # online_registration | lottery | qualification | invitation | membership_then_online | at_venue | none_spectator_only
    url: {value: https://..., source_id: ..., as_of: ..., confidence: ...}           # registration page, or the event page that explains sign up
    platform: {value: Skireg, source_id: ..., as_of: ..., confidence: ...}         # optional: RunSignup, Skireg, Zone4, Webscorer, Eventbrite, the organizer's own form
    opens: {value: 2026-09-01, source_id: ..., as_of: ..., confidence: ...}        # optional
    deadline: {value: 2027-02-10, source_id: ..., as_of: ..., confidence: ...}     # optional
    fee_usd: {value: 150, note: "50 km skate, early rate", source_id: ..., as_of: ..., confidence: ...}   # optional; the lowest adult entry fee, the note says which
    requirements: {value: "U.S. Ski & Snowboard competitor license", source_id: ..., as_of: ..., confidence: ...}   # optional
  website: {value: https://..., source_id: ..., as_of: ..., confidence: ...}
  results_url: {value: https://..., source_id: ..., as_of: ..., confidence: ...}   # optional
  calendar_url: {value: https://..., source_id: ..., as_of: ..., confidence: ...}  # optional: the body's calendar that lists this event
```

A series held at several venues on different dates (a NASTAR program, a USASA series, a skijoring circuit) is one
event record per stop: id `<series>-<venue>` with `series: {id, name}` added. A calendar that lists hundreds of youth
races goes in as a `calendars:` entry instead (below), so visitors can open it.

```yaml
calendars:                               # optional: calendars too large to list event by event
- id: ussa-alpine-calendar
  name: U.S. Ski & Snowboard alpine events calendar
  discipline: alpine_racing
  region: national                       # national or a two letter state code
  url: {value: https://..., source_id: ..., as_of: ..., confidence: ...}
```

## Discipline ids

| Group | Ids |
|---|---|
| alpine | alpine_racing, masters_racing, nastar, ski_cross |
| nordic | cross_country, ski_marathon, biathlon, ski_jumping, nordic_combined |
| freestyle | moguls, aerials, slopestyle, halfpipe, big_air, rail_jam |
| snowboard | snowboard_cross, banked_slalom, alpine_snowboard |
| freeride | big_mountain |
| backcountry | skimo, uphill, telemark |
| heritage | longboard, retro_race, ski_ballet, speed_skiing, pond_skim |
| adaptive | adaptive_alpine, adaptive_nordic |
| snowshoe | snowshoe_race |
| skijoring | skijoring |
| snowkite | snowkiting |
| sled_dog | sled_dog |
| snowmobile | snocross, hill_climb, snowmobile_race |
| winter_ultra | fat_bike, winter_ultra |
| instructor | psia_aasi_exam, psia_aasi_clinic, psia_aasi_event, interski |
| community | endurance (lap and vertical challenges such as Gunbarrel 25), fun_race (costume, dual and open fun races) |

Use the closest id. If none fits, use the closest group's first id and say so in `format`.

## Level ids

`elite` (World Cup, national team, pro tour), `collegiate`,
`masters`, `open_amateur` (anyone can enter), `adaptive`, `instructor`.
