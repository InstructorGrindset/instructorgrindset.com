# Jet stream layer: data contract

Owner request (2026-11-01): a map overlay of the average jet stream path over the ski season, with the last 10
winters as a baseline and El Niño, La Niña and neutral winters to compare, each one toggled on and off by itself,
showing which states and mountains each path passes over. Owner answers: the season is November to April; the
baseline is the last 10 complete seasons; the El Niño, La Niña and neutral averages use every season since 1979/80;
strength classes (weak, moderate, strong) get their own toggles only when their paths differ enough, otherwise one
toggle per phase; each path draws as a line with a band, and the states and resorts under the band light up and are
listed.

The project rules in `CLAUDE.md` apply: data only in `/data`, every displayed value carries `source_id`, `as_of` and
`confidence`, all thresholds live in `config/definitions.yaml` (`jetstream:`), map code loads only on `/map/` routes.

## Sources

| Source id | What | Raw file |
|---|---|---|
| `era5-arco-250hpa-winds` | ECMWF ERA5 hourly 250 hPa u and v (Copernicus Climate Change Service), read from Google's public ARCO ERA5 bucket | `data/raw/climate/era5_jet/season_means_250hpa.json` (+ `source.json`), written by `python -m pipeline.climate.era5_jet fetch` |
| `noaa-cpc-roni` | NOAA CPC Relative Oceanic Niño Index (official ENSO index since February 2026) and the legacy ONI | `data/raw/climate/enso/RONI.ascii.txt`, `oni.ascii.txt` (+ `source.json`), written monthly by `.github/workflows/enso-index.yml` |

The ERA5 extract holds, per season (November 1 to April 30, 1979/80 to 2025/26), the mean u and v at 250 hPa on a
1 degree grid, latitude 15N to 75N and longitude 170W to 50W, from one hour per day (00 UTC on even days of the
year, 12 UTC on odd days) averaged on the native 0.25 degree grid and then into trapezoid weighted 1 degree boxes.

## Method (`pipeline/jetstream.py`; every number in `config/definitions.yaml` `jetstream`)

1. **Classify each season** from the RONI. An episode is a run of at least `enso.episode_min_seasons` consecutive
   overlapping three month values at or above `+enso.threshold` (El Niño) or at or below `-enso.threshold` (La Niña).
   A season takes the phase of the episode that holds its `enso.season_value` (DJF) value; otherwise it is neutral.
   Strength is the largest class of `enso.strength` (weak, moderate, strong; NOAA's very strong counts as strong)
   whose floor the episode holds for at least `enso.strength_min_seasons` (3) consecutive values, the usual
   convention for ENSO strength.
2. **Average** the season mean u and v over the seasons of each layer (vector mean), and take the wind speed of
   that mean wind.
3. **Find the jet axis** per longitude: local maxima of speed between `axis.lat_min` and `axis.lat_max` with at
   least `axis.min_speed_ms` and a prominence of `axis.min_prominence_ms`, refined to a fraction of a degree with a
   parabola through the three points around the peak. Peaks link across neighboring longitudes when they move less
   than `axis.max_step_deg`; chains shorter than `axis.min_length_deg` drop. The chain with the most integrated
   speed is the main (fastest) branch, id `main`; the others get ids `north`, `south`, then `north_2` and so on, by
   their mean latitude against the main branch where they overlap. A chain expects its next latitude along its slope
   and prefers a peak of similar speed, so jets that cross keep their own peaks. Each chain is smoothed with a
   running mean over `axis.smooth_deg` of longitude. With `drop_branches_without_states` a branch whose band crosses
   no US state drops (the main one stays). Labels say where a branch runs: "Jet stream" when a layer has one,
   otherwise "Northern branch", "Southern branch", "Middle branch", "Far northern branch"; `fastest` marks the main
   one.
4. **Band**: north and south of the axis, the latitude where speed falls to `band.fraction` of the axis speed,
   capped at `band.max_half_width_deg`. Within `band.taper_deg` of a branch end that is not the edge of the data
   grid, the band narrows linearly to `band.taper_min_fraction` of its width (0: a point). The build warns when the
   cap, not the fraction, draws most of a band.
5. **Overflies**: a state is listed when a band covers any of its area, sampled on a `overflies.sample_deg` grid; a
   ski area (facility type `ski_area`; indoor slopes and training centers stay out) is listed when its point lies
   inside a band, and its state joins the state list even where the sample grid missed it. Lists run west to east.
6. **Strength groups**: a class with fewer than `split.min_seasons` seasons joins its weaker neighbor. Then, from
   weak to strong, each class joins the group before it unless their main axes differ by at least
   `split.min_mean_shift_deg` of latitude on average over `split.lon_west` to `split.lon_east` (the lower 48). One
   group is one toggle for the phase; several groups are one toggle each (`el_nino_weak`,
   `el_nino_moderate_strong`).

## Build output: `data/build/jetstream.json` (loaded only when a jet stream toggle is first turned on)

```json
{
  "version": 1,
  "generated_at": "2026-11-01T00:00:00Z",
  "season_label": "November to April",
  "level_hpa": 250,
  "sources": {
    "winds": {"source_id": "era5-arco-250hpa-winds", "as_of": "2026-11-01", "confidence": "published"},
    "enso": {"source_id": "noaa-cpc-roni", "as_of": "2026-11-01", "confidence": "published"}
  },
  "layers": [
    {
      "id": "last10",
      "label": "Last 10 winters",
      "kind": "baseline",
      "phase": null,
      "strength": null,
      "color": "#c4b5fd",
      "seasons": ["2016/17", "2017/18"],
      "season_range": "2016/17 to 2025/26",
      "branches": [
        {
          "id": "main",
          "label": "Jet stream",
          "fastest": true,
          "axis": [[-170, 38.4, 31.2], [-169, 38.5, 31.6]],
          "band": [[-170, 33.1, 43.9], [-169, 33.2, 44.0]]
        }
      ],
      "overflies": {
        "states": [{"code": "CA", "name": "California", "branches": ["main"]}],
        "resorts": [{"id": "mammoth-mountain", "state": "CA", "branch": "main"}]
      },
      "source_id": "era5-arco-250hpa-winds",
      "as_of": "2026-11-01",
      "confidence": "estimated"
    }
  ],
  "seasons": [
    {"season": "1979/80", "phase": "neutral", "strength": null, "value": 0.65, "peak": null,
     "source_id": "noaa-cpc-roni", "as_of": "2026-11-01", "confidence": "published"}
  ],
  "current": {"season_code": "JAS", "year": 2026, "months": "July to September 2026", "value": 1.69,
              "phase": "el_nino", "strength": "strong", "layer": "el_nino_moderate_strong", "preliminary": true,
              "source_id": "noaa-cpc-roni", "as_of": "2026-11-01", "confidence": "published"},
  "split": {"el_nino": {"split": true, "groups": [["weak"], ["moderate", "strong"]], "shift_deg": 2.74,
                        "pairs": [{"a": "weak", "b": "moderate", "shift_deg": 2.74}]}},
  "method": "Plain text summary of the method above, shown under the toggles."
}
```

* `layers` order is the toggle order: `last10`, then each phase (`el_nino`, `la_nina`, `neutral`), or its strength
  group layers (for example `el_nino_weak`, `el_nino_moderate_strong`, labelled "Moderate and strong El Niño", with
  `strengths: ["moderate", "strong"]`) when `split.<phase>.groups` holds more than one group.
* `axis` points are `[lon, lat, speed_ms]`, one per degree of longitude, west to east; `band` points are
  `[lon, lat_south, lat_north]` at the same longitudes. A branch may cover only part of the longitude range.
* Paths are derived from ERA5 by this method, so they carry `confidence: "estimated"`; the season classes copy the
  RONI and carry `published`.
* `color` comes from `jetstream.colors` in config.

## UI (`/map/` only)

* A "Jet stream" section in the Layers panel: one checkbox per layer with its color swatch and season count
  ("El Niño, 13 winters"), and a short method line with links to both sources.
* Each visible layer draws its band (translucent fill) and its axis (line) per branch, under the resort dots.
* The most recently turned on layer is the active one: states under its band get a tint, resort dots under it get
  a ring, and a card lists its states and resorts by state, west to east. A selector in the card switches the active
  layer when several are visible.
* Nothing of this loads before the first toggle; `jetstream.json` is lazy.

### As built (`map/js/jetstream.js`, `map/css/jetstream.css`)

* **Loading.** On the startup path there is only a closed "Jet stream" box (`#jet-box`, a `details` after the LAYERS
  box) and the `?jet=` reading in `router.js`. The file cannot list its layers before it loads, so opening the box is
  the first toggle: it brings `jetstream.js`, `jetstream.css`, `jetstream.json` (validated per layer and per branch by
  Zod `JetStreamFile` in `schemas.js`) and `sources.json` (the source titles). A `?jet=` link loads after the first
  paint; it opens the box only where the Layers panel sits beside the map (1200 px of stage or more), so phones and
  narrow desktops show the paths and the legend with the box closed. A 404 says "The jet stream paths arrive with the
  next data build." and drops `?jet=`; any other failure says "The jet stream paths could not load. Try again." with a
  Try again button, keeps `?jet=`, and loads again on the next open of the box.
* **Box.** One checkbox per layer in file order: a swatch (band and line in the layer color), the label over its
  winters ("4 winters"; the baseline shows its `season_range`, "2016/17 to 2025/26"), named "La Niña, 19 winters".
  "El Niño" and "La Niña" keep a no break space. Then "Average jet stream about 10 km up (250 hPa), November to
  April.", NOAA's latest reading from `current` ("NOAA's latest reading, July to September 2026: +1.69, strong El
  Niño (preliminary).", its badge, date and source, and "Show the Moderate and strong El Niño path" when `current.layer`
  names a layer; nothing when `current` is null), the card, the two sources and "How the paths are made". The box
  title counts the paths on ("2 on"). Opening the box below 1200 px folds LAYERS and keeps the map beside the panel
  (`padding` in `app.js`); on phones it scrolls the sheet to the box.
* **Map.** One GeoJSON source `jet` (credited "Jet stream: <both source titles>" while a path shows) with, per visible
  layer and branch, the band polygon, its two edges (thin and solid) and the axis (with a dark casing for SATELLITE),
  under `resorts-glow`. The band fill fades out between zoom 5 and 7, so cluster and resort views keep only the edges
  and the line. `jet-states` outlines the active band's states in its color, under the selected state's line.
  `jet-label` (source `jet-labels`) sits above the dots: one label per branch, at a point of its axis inside the open
  map area (the map minus the panels and the legend), turned along the line, placed after every move so labels of
  several paths clear each other. With one path on a label names the branch ("Northern branch", "Jet stream"); with
  several it names the layer ("La Niña, Northern branch"). `map.js` `setResortRing` rings the active band's ski areas
  with a dark gap and a ring in the path color (`resorts-jet-gap`, `resorts-jet-ring`; they follow the Type and Show
  only filters, and EVENTS mode hides them with the dots). Gaps and null points split a branch into runs.
* **2D picture.** Before the WebGL map starts, `preview.js` `addPreviewOverlay` draws the same bands (with the same
  fade), edges, lines and rings; in EVENTS mode it draws no rings.
* **Active layer and card.** The last path turned on is active; turning it off hands over to the most recent other one.
  The card (`#jet-card`, inside the box, so the phone gets it in the layers sheet) is titled "Under the La Niña band".
  With several paths on, "List for" radios switch the active layer. With two or more, a comparison: "Under La Niña
  only: Washington, Oregon, ... (46 ski areas)." and, with exactly two, the same for the other path ("Every state under
  the Weak El Niño band also sits under the La Niña band." when there is none). Then "34 states and 110 ski areas, west
  to east." ("8 states, west to east. This band crosses no ski areas."), with "12 of 110 shown by your filters." when
  the Type and Show only filters hide some. The states with ski areas follow, west to east, each with its count; when
  they lie under different branches they group under a heading per branch. Up to 12 ski areas open every state; more
  open state by state, with an "Open every state" button (its label changes; no pressed state). The other states
  follow in one line ("Also under the band: Texas, Louisiana."). Then "Computed from data read on <date>" with the
  path's badge and source, "El Niño and La Niña years read on <date>" with the RONI's, and the winters averaged. A
  resort whose state is missing from the file's states list shows under its own state. Turning a path on, a link's
  first load, the WebGL map's start and Color by's arrival scroll the panel so the card's top shows.
* **Panel line.** While a path is on, the state, cluster and resort panels get one line per visible path (the active
  one first) under the heading: "Stevens Pass sits under the La Niña band, northern branch.", "The La Niña band
  crosses Washington: 19 ski areas.", "All 13 ski areas in the Cascades cluster sit under the La Niña band.", or,
  from the band geometry at the place's middle longitude against its latitude span, "The La Niña band passes south of
  Colorado.", "Alpha Peak sits between the La Niña bands." (`bandRelation`). `panel.js` calls `onRendered` after every
  render, so the line survives the resort record's late load.
* **Legend strip.** While a path shows: "Jet stream", a line per visible layer (the active one bold) and "Ringed: under
  the band" (left out in EVENTS mode), in a row of its own above the resort legend that wraps on desktop; on phones the
  items come first in the sideways strip, which fades at its right end to show it scrolls.
* **Address bar.** `?jet=<id>,<id>` in the order the paths came on (the active one last). `router.js` adds it to every
  history entry (`locationFor`), rewrites it on back and forward to the current paths, and drops ids the file lacks,
  saying "A path in this link is no longer available." once. An old strength id maps to the layer whose `strength` or
  `strengths` holds it (`el_nino_strong` to `el_nino_moderate_strong`). Links in the page leave it out; the Link button
  copies it. The list view hides the box.
* **Left for later.** Faded line ends: a `line-gradient` with `lineMetrics` would need a layer per path color.
* **Tests.** `jetStreamChecks` in `tests/map/browser_test.mjs` (fixture `tests/map/fixtures/jetstream.json` from
  `make_fixtures.py`: straight synthetic paths with tapered ends, La Niña with two branches, the southern one over part
  of the range, a synthetic `current`) covers the lazy load, drawing and removing, the outline, fade, rings and labels,
  the card, List for, the comparison, the filters, the fallback, the panel lines, the address bar and old ids, the 2D
  picture, tones, EVENTS mode, the 1024 px layout, other layer ids, a missing file, a failed load with Try again, the
  transforms and the phone sheet; `realJetStreamChecks` checks every real layer's card against its lists and the latest
  reading's button. `tests/map/audit.mjs` runs axe on the card (desktop, before the map starts, phone) and checks that
  the budget runs load nothing of the jet stream.

## Refresh

* **ENSO index:** `.github/workflows/enso-index.yml` commits CPC's RONI and ONI on the 12th of each month. The map
  changes only when a season is added, so a new RONI file alone needs no rebuild.
* **A new season:** once ERA5 covers April (final ERA5 data arrive about two to three months later, so July), run
  `python -m pipeline.climate.era5_jet fetch --last <start year>` (it reuses the cached seasons under `download/`),
  then `python pipeline/build.py`. The baseline moves to the newest 10 seasons, and the new season joins its RONI
  phase. 2026/27 can join in July 2027.

## First build (2026-11-01)

* 47 seasons, 1979/80 to 2025/26. RONI classes (3 consecutive values for a strength): El Niño 13 (weak 4,
  moderate 5, strong 4), La Niña 19 (weak 10, moderate 4, strong 5), neutral 15. Under the RONI, 2023/24 is a
  moderate El Niño (peak 1.42).
* El Niño gives two toggles: weak (4 winters) differs from moderate by 2.7 degrees on average over the lower 48,
  and moderate and strong differ by 1.0, so they share one toggle (9 winters). La Niña stays one toggle (largest
  difference 0.8 degrees).
* Over the West the seasonal mean jet runs in two branches in the last 10, La Niña and neutral winters: a southern
  (subtropical) branch over Baja, northern Mexico and the Gulf Coast, and a northern branch off the Pacific. In El
  Niño winters one jet runs along the southern tier; in moderate and strong El Niño winters it follows the Gulf
  Coast (8 states, no ski area under the band). La Niña brings its northern branch ashore over Washington, Oregon,
  Idaho and Montana (34 states and 110 ski areas under its bands).
* The latest RONI value (July to September 2026, 1.69, preliminary) points to a strong El Niño in 2026/27;
  `current` names the Moderate and strong El Niño layer for it.
