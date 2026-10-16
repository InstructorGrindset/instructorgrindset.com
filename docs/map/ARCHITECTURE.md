# Career Map architecture and data contract

This is the contract between the Python pipeline (`/pipeline`) and the map frontend (`/map`). Change it here first, then in both the Pydantic schemas and the Zod schemas.

## Stack (Phase 0 decision)

The existing site is hand built HTML on GitHub Pages with no build step. The roadmap says to match the existing site, and introducing Astro would mean moving the live site to a GitHub Actions build. The map therefore ships as plain static files:

* `/map/index.html` uses the existing sidebar partial and `/assets/global.css`.
* Native ES modules in `/map/js/`, no bundler.
* Third party libraries are vendored from npm into `/map/vendor/` at pinned versions (MapLibre GL JS, Zod, Chart.js). `scripts/vendor.sh` refreshes them. Nothing loads from a CDN.
* Basemap: state outlines from `us-atlas` compiled to `/data/build/us-states.geojson`, over a satellite style underlay served with the site (`/images/basemap/`, see "Basemap underlay") and, from zoom 6.5, USGS imagery tiles. A Protomaps PMTiles basemap plugs in later through `basemap.pmtiles_url` in `/map/js/config.js` once tiles are hosted on R2.
* Deep links (`/map/co/i70-corridor/vail/`) are served by static route stubs that `pipeline/routes.py` generates from the build output. Each stub is a copy of the map shell, so every URL works on GitHub Pages without rewrites.

Astro remains an option for Phase 8 if per resort static pages outgrow the stub approach.

## Identifiers

* `state.code`: two letter USPS code, upper case (`CO`). URL segment is lower case (`co`).
* `cluster.id`: kebab case slug, unique (`i70-corridor`, `lake-tahoe`, `wasatch`).
* `resort.id`: kebab case slug, unique (`vail`, `palisades-tahoe`, `jackson-hole`).
* `source.id`: kebab case slug, unique (`vail-10k-fy2026`, `wikipedia-vail-ski-resort`).
* Resort URL: `/map/{state lower}/{cluster id}/{resort id}/`. Cluster URL: `/map/{state lower}/{cluster id}/`. State URL: `/map/{state lower}/`. A cluster spanning states uses the state of the URL the user came from; the canonical cluster URL uses the first state in `cluster.states`. A resort in a multi state cluster gets a route stub under every state in that cluster (`/map/ca/lake-tahoe/heavenly/` and `/map/nv/lake-tahoe/heavenly/`); its canonical URL uses the resort's own `state`.

## The Sourced wrapper

Every displayed number or fact is a `Sourced` object, or `null` when unknown:

```json
{ "value": 5317, "source_id": "wikipedia-vail-ski-resort", "as_of": "2026-10-11", "confidence": "published" }
```

* `confidence`: `published` | `reported` | `estimated`
* `as_of`: ISO date the value was verified or computed
* `note` (optional): short qualifier, e.g. `"skiable acres per resort site"`

## Build outputs (`/data/build`)

All files carry a top level `{ "version": <int>, "generated_at": <ISO datetime>, ... }`.

### `resorts.json`
```
{ version, generated_at, resorts: Resort[] }
Resort {
  id, name, tier: "A" | "B",
  facility_type: "ski_area" | "indoor" | "training_center",   // Tier B is always ski_area; indoor and training records may have no size data
  tier_a_criteria: [{ criterion: diehard|luxury|athlete_development|heritage|operator_compensation|year_round_training|owner_selection,
                      rationale, source_id }],
  tier_a_status: "draft" | "approved" | null,      // null for Tier B
  approval: { by: string, date: "YYYY-MM-DD", note: string | null } | null,   // set only when tier_a_status is approved
  owner: Sourced<string> | null,                   // "Vail Resorts" | "Alterra Mountain Company" | operator name
  lat, lon,                                        // base area, decimal degrees
  location_source_id,
  cluster_id, state,
  size: { acres, vertical_ft, lifts, ski_school_staff_est },          // each Sourced<number> | null
  elevation: { base_ft, summit_ft },                                  // each Sourced<number> | null; Tier A research first, else OpenSkiMap (estimated)
  snow: { annual_avg_in: Sourced<number> | null } | null,   // resort reported average annual snowfall (inches), e.g. Wikipedia infobox; null when absent
  access: { rating: convenient | transit_friendly | mixed | car_centric, summary_md, source_id, as_of, confidence, note } | null,
                                                   // owner decision 6; public labels Convenient, Transit friendly, Mixed, Car centric; summary_md is one public sentence
  season: { open_typical, close_typical, length_days, training_start }, // each Sourced | null; dates as "MM-DD"
  terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags },  // pcts Sourced<number 0 to 100> | null; tags string[]
  fit: { enthusiast_types: string[], professional_types: string[] },
  clients: { profile_tags: string[], notes_md: string | null },
  career: Career | null,   // Phase 4, see "Phase 4 career" below; null when a resort has no pay records and no career inputs
  editorial: Editorial | null,   // Phase 6; only reviewed entries, see "Phase 6 editorial"; null otherwise
  climate_available: boolean,                      // true when climate/<id>.json exists
  detail_available: boolean,                       // true when detail/<id>.json exists (Phase 5)
  trailmap_available: boolean,                     // true when trailmap/<id>.json exists (Trail map)
  official_trailmap: { url, format: pdf|jpg|png|page, season: "2025/26" | null, source_id, as_of, confidence, note,
                       image_url, image_format: jpg|png|webp|pdf, image_source_id, image_as_of, image_confidence,
                       image_note } | null,        // the resort's own trail map page and, when found, the map file
                                                   // the trail map dialog shows from its servers (image_* null when none)
  webcams: { cams_page: { url, source_id, as_of, confidence, note } | null,
             cams: [{ label, url, kind: page|image|stream, source_id, as_of, confidence, note }] } | null,
                                                   // live cams links, at most webcams.max_named_cams cams (Webcams and jobs)
  jobs: { links: [JobLink], feed: boolean,          // jobs links by role group; employers in jobs/employers.json
          hiring_window: { text, source_id, as_of, confidence, note,
                           start_month: "YYYY-MM" | null, end_month: "YYYY-MM" | null } | null } | null,
                                                   // null without links and window; months read from the text
                                                   // by pipeline/hiring.py, both null when it is not explicit
  passes: [{ pass: epic|ikon|mountain_collective|indy, access: unlimited|limited|unspecified,
             days: int | null, blackout: boolean | null, season: "2026/27", season_fallback: boolean,
             source_id, as_of, confidence, note }],
                                                   // pass memberships of passes.season in pass order, [] for none;
                                                   // season_fallback true when the record is the previous season's
                                                   // (see "Pass membership"); never inferred from owner
  housing_summary: { site_count, cheapest_rent_monthly_low, cheapest_site_id, has_transit } | null,   // Phase 5
  housing_search: { furnished_finder: { town, url, source_id, as_of, confidence, note } | null,
                    tools: [{ name, kind, url, why, scope: resort|cluster, named_by, source_ids, as_of, confidence, note }] } | null,
                                                   // Furnished Finder link and ranked housing search tools, links only
                                                   // (see "Housing search"); null without a link and a tool
  sources: string[]                                // every source_id referenced by this resort
}
```

### `clusters.geojson`
GeoJSON FeatureCollection, one Polygon or MultiPolygon per cluster. `properties`:
```
{ id, name, states: string[], resort_ids: string[], tier_a_count, overview_md: string | null, alexandre_take_md: string | null, sources: string[] }
```
Polygons are convex hulls of member resorts buffered by about 15 km, or hand drawn polygons from `config/clusters.yaml` when supplied.

### `resorts.geojson`
Point FeatureCollection mirroring `resorts.json` for MapLibre. `properties`: `{ id, name, tier, facility_type, cluster_id, state, acres, vertical_ft, lifts, size_rank, season_length_days, snowfall_annual_in, snowfall_annual_source, access_rating, lucrativeness_mid }` where `size_rank` is 1 to 5 from acres (null acres gives 1). The last five are for map recolor modes (numbers or null, plus the snowfall source and the access rating): `season_length_days` is `season.length_days.value`, `snowfall_annual_in` is the resort's published `snow.annual_avg_in` when present, else the sum of the climate file's monthly `snowfall_in` over months whose `snowfall_source` is not null (null when neither exists), and `snowfall_annual_source` says which (`published` | `climate` | null), `access_rating` is `access.rating`, `lucrativeness_mid` is `career.lucrativeness.mid`. No professionalism or operator scale field exists in any build output (owner decision 2026-10-13; `tests/pipeline/test_no_professionalism.py`).

### `states.json`
```
{ version, generated_at, states: [{ code, name, cluster_ids: string[], resort_count }] }
```
Only states with at least one resort.

### `sources.json`
```
{ version, generated_at, sources: [{ id, url, title, accessed_date, note }] }   // note: string | null (optional qualifier)
```
Includes every source declared in `/data/raw` plus the climate ETL's `data/build/climate/sources.json`, merged by id,
and the NOAA normals source record (`data/raw/climate/noaa_normals/source.json`, id `noaa-ncei-normals-1991-2020`).

### `definitions.json`
Display relevant subset of `config/definitions.yaml`, so the UI never hardcodes thresholds:
```
{ version, generated_at,
  snow: { powder_day_in, banner_day_in },            // new snow in 24 hours
  density: { dry_max_pct, moist_min_pct },           // dry < dry_max_pct, average between, moist > moist_min_pct
  display: { priority: ["published", "reported", "estimated"] },
  reports: { aggregation_min },                      // approved reports needed before a Reported range shows
  submissions: { endpoint: string | null },          // report form target; the form is hidden while null
  staleness: { max_age_months },                     // records older than this are flagged
  heat: { ramp: { name, colors: hex[] },             // continuous ramp for modes without classes (see "Heat maps")
          neutral: hex,                              // neutral grey: the snowfall no snow class and the far fade color
          regions: { lower48: Box, alaska: Box },    // Box { west, south, east, north }
          field: { neighbors, min_distance_km, decluster_km, full_opacity_km, fade_end_km, max_alpha, floor_alpha,
                   fade_to: neutral|alpha, px_per_degree, grid_step_px, max_pixels },
          modes: { lucrativeness, season, snowfall: HeatMode },
          season: { px_per_degree, grid_step_px, full_opacity_km, fade_end_km, floor_alpha, fade_to, no_data_reach_km } } }
HeatMode { scale: linear|sqrt|classed, domain?: [min, max], domain_min?, ticks?,
           breaks?: number[], colors?: hex[],        // classed: breaks.length + 1 classes, class i from breaks[i - 1] to breaks[i]
           legend_compress_below?, legend_ticks? }   // classed legend layout (see "Heat maps", Legend)
```
`heat` is `config/definitions.yaml` `heat` without its `snowfall` block (pipeline only). `season` holds the season length
fade, which the browser fallback uses too. The UI never hardcodes the colors or the field settings; without a valid
`heat` block the heat map modes are disabled ("Heat map settings did not load."). Config validation fails when a
classed palette has neighbouring colors closer than 6 in OKLab (x 100) or the wrong number of colors.

### `us-states.geojson`
State outlines (simplified) with `properties: { code, name }`.

### `heat/<mode>.json` and `heat/<mode>-<region>.png` (heat map rasters, `pipeline/heat.py`)
Three modes are precomputed: `snowfall` (NOAA normals and NRCS SNOTEL mountain stations, shaped by the terrain, plus
resort pockets, with isolines), `season` (season length from the resorts' sourced values only) and `lucrativeness`
(the resorts' computed `career.lucrativeness.mid`, `pipeline/heat_lucrativeness.py`; see the note after the manifest).
Manifest version 2:
```
{ version: 2, generated_at, mode: "snowfall" | "season", units: "in" | "days", scale: "classed",
  domain: { min, max },                          // snowfall: heat.modes.snowfall.domain (0 to 900); season: the sourced resort range
  ticks: number[],                               // legend ticks (heat.modes.<mode>.legend_ticks, else the breaks)
  breaks: number[], colors: hex[],               // the classes the PNGs are colored with (heat.modes.<mode>)
  legend: { ticks, compress_below: number | null },
  ramp?: hex[],                                  // only for a mode configured linear or sqrt (heat.ramp)
  // snowfall only:
  background: { source_id: "noaa-ncei-normals-1991-2020", as_of, confidence: "published", field: "ANN-SNOW-NORMAL",
                stations },                      // usable NOAA stations inside the regions
  mountain_stations?: { source_id: "nrcs-snotel-awdb-annual-snowfall", as_of, confidence: "estimated",
                field: "annual_snowfall_in", method, water_years: [first, last], stations },   // usable SNOTEL stations
  terrain?: { source_id: "aws-terrain-tiles", as_of, confidence: "published", step_deg },     // when the field follows it
  pockets: { field: "snow.annual_avg_in", resorts, radius_km, terrain: boolean },
  isolines: { levels: number[], major_step, files: [{ region, file: "snowfall-isolines-<region>.geojson", bytes, features }] },
  // season only:
  values: { field: "season.length_days", resorts, points },   // resorts with a sourced value; points after declustering
  method: { interpolation, neighbors, min_distance_km, grid_step_px, px_per_degree, full_opacity_km, fade_end_km,
            floor_alpha, fade_to, decluster_km?, no_data_reach_km?,
            elevation? },                        // snowfall with terrain: heat.snowfall.elevation as configured
  attribution: "Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL"   // snowfall with SNOTEL stations
             | "Snowfall normals: NOAA NCEI 1991 to 2020" | null,          // NOAA only; season: null
  inputs_sha256,                                 // points, outlines, heat config and the code; unchanged means no rebuild
  rasters: [{ region: "lower48" | "alaska", image: "<mode>-<region>.png", width, height,
              coordinates: [[w, n], [e, n], [e, s], [w, s]],   // exact corners, the region box from config
              bytes, stations?, mountain_stations?, terrain?, pockets?, resorts?, no_data_states? }] }
```
* PNG: 8 bit RGBA written with the stdlib (`pipeline/png.py`, zlib; Up filter for season length, no filter for
  snowfall, whose flat class colors compress about 30 percent better unfiltered). Columns evenly spaced in longitude,
  rows evenly spaced in Web Mercator y (MapLibre image sources expect that), pixel centers at half steps inside the
  corners. Each pixel takes its class color; on a class edge the color is the mean of four subsamples, each bilinear
  between pixel centers, so edges stay smooth when MapLibre magnifies the image. Alpha is the mode's opacity rule
  times the share of the pixel inside the region's state outlines (antialiased scanline coverage, 4 samples per row).
  Fully transparent pixels are zero. Each file must stay under 400 KB.
* Snowfall: 32 px per degree (about 2.8 km at 40 degrees north): 1862 by 1023 px, 136 KB for the lower 48 and 84 KB
  for Alaska (160 KB and 110 KB with the Up filter before item 14; the terrain field alone would have made them about
  195 KB and 116 KB). Opacity from `heat.snowfall` (full to 300 km from a station or resort, then the alpha alone fades
  to 0.35 at 800 km; it only touches remote Alaska). The field method is under Color modes, Snowfall.
* Season length: 16 px per degree (931 by 511 px), 94 KB for the lower 48 and 71 KB for Alaska. Values: `season.length_days`, else the
  days from a sourced `open_typical` to a sourced `close_typical`, both counted (`heat.season_days`); unsourced values are
  ignored. The `heat.field` interpolation (decluster 20 km, 8 neighbors) at every 4th pixel, bilinear between. Fade from
  `heat.season`: full color within 250 km of a resort with data, then a blend into `heat.neutral` and down to 0.6
  opacity by 450 km. States with no resort data (`no_data_states`) keep color only near their border: the share kept
  falls smoothly from 1 at the border to 0 at `no_data_reach_km` (120 km) past it, measured with a chamfer distance
  transform from the states with data (`heat.border_distance`). So the southern Plains and the Deep South stay clear.
* A region without outlines is skipped. Without the NOAA extract the build leaves the snowfall files alone; without
  the SNOTEL extract it draws NOAA stations only; without the terrain extract it falls back to plain station
  interpolation (both with a build warning); without `us-states.geojson` it leaves both modes. The frontend falls back to the browser surface (`map/js/heat.js`) when a manifest
  is absent or invalid.

* Lucrativeness (`heat/lucrativeness.json`, `pipeline/heat_lucrativeness.py`, kept apart from `heat.py` so its changes
  leave the snowfall and season digests alone): `mode: "lucrativeness"`, `units: "usd"`, `scale: "linear"` with `ramp`
  (`heat.ramp`), `domain` and `ticks` from `legend_scale`, the twin of `legendScale` in `map/js/heat.js` (a Node test
  checks they agree), `values: { field: "career.lucrativeness.mid", resorts, points }`, `method` from `heat.field`,
  `attribution: null`. The surface is the browser's: the `heat.field` interpolation, resolution (12 px per degree) and
  fade. Written only when a resort has a computed lucrativeness (pay); otherwise its files are removed and
  the mode stays disabled.

### `heat/snowfall-isolines-<region>.geojson` (snowfall isolines, `pipeline/heat.py`)
```
{ type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "LineString", coordinates: [[lon, lat], ...] },
  properties: { value: 200..900, major: boolean, label: boolean, region: "lower48" | "alaska" } }] }   // compact JSON
```
* Lines of equal snowfall every `heat.snowfall.isolines.step` (50 in) from `start` (200) to `end` (900), `major` every
  `major_step` (100). Traced on the same field as the raster with marching squares (`heat.refined_isolines`): in every
  pixel cell within one step of the lowest level (and its neighbours) the field is resampled `supersample` (4) times
  finer, the background bilinear and each resort pocket evaluated exactly (its terrain target with the ground height
  and gradient bilinear between pixel centers too), so pocket edges come out smooth. Saddles use the cell mean;
  segments are joined into lines (closed lines repeat their first point); lines shorter than `min_length_px` (4 raster
  px) are dropped; Douglas Peucker at `simplify_px` (0.2 raster px, 0.1 before item 14: the terrain makes longer, more
  winding lines); coordinates round to 0.001 degree. `label` marks lines at least `label_min_px` (12 raster px) long.
* About 128 KB for the lower 48 (266 lines) and 15 KB for Alaska (40 lines), against 121 KB and 13 KB before item 14;
  each file must stay under 300 KB.

### `map-index.json` (Phase 8, written by `pipeline/routes.py`)
Slim national index the map loads first. Compact JSON, one row per resort as an array in `fields` order:
```
{ version, generated_at,                 // copied from resorts.json
  fields: ["id", "name", "tier", "tier_a_status", "facility_type", "state", "cluster_id", "lat", "lon",
           "acres", "size_rank", "climate_available", "detail_available", "webcams_available", "jobs_available",
           "season_length_days", "snowfall_annual_in", "lucrativeness_mid", "hiring_window", "job_employers", "passes"],
  resorts: [[...], ...] }
```
Every value is copied from `resorts.json` (`acres` is `size.acres.value`; `webcams_available` is `webcams != null`,
`jobs_available` is `jobs.links` not empty, `hiring_window` is `[start_month, end_month]` of `jobs.hiring_window` or
null, `job_employers` is the sorted ids of the linked employers that have a feed in `jobs/employers.json`, every linked
employer when that file is absent, null when `jobs.feed` is false, `passes` is the resort's pass ids in pass order
(`["ikon", "mountain_collective"]`) or null) and `resorts.geojson` (`size_rank` and the four
recolor values); without `resorts.geojson`, `size_rank` uses `display.size_rank_acres_breaks` and the recolor values
come from `resorts.json` as in the `resorts.geojson` rules, with `snowfall_annual_in` null. Resorts with an unsafe id or
no coordinates are left out. About 16 KB gzipped against 87 KB for `resorts.json` plus 21 KB for `resorts.geojson`.
The frontend builds its resort points from this file and falls back to `resorts.json` and `resorts.geojson` when it
is absent.

### `sitemap.xml` (written by `pipeline/routes.py`)
Only the `/map/` entries are rewritten; every other entry stays byte for byte. The map entries are the canonical URLs of
`/map/`, every state page, every cluster page and Tier A resort pages. Tier B resort pages are generated, linked and
crawlable (no `noindex`) but left out of the sitemap. Hand written pages in `routes.STATIC_PAGES` (`/map/credits/`) join
the map entries when their `index.html` exists; `routes.py` never writes or removes them (`credits` is protected).

### Resort route stubs embed their record
Each resort page under `/map/` carries `<script type="application/json" id="resort-record">` with
`{ version, generated_at, resort: <its resorts.json record>, sources: [<the sources listed in resort.sources>] }`
(every `<` escaped as `<`; an editorial block that is not `reviewed` is dropped). The panel validates it with the
same Zod schema as `resorts.json` and renders the resort without waiting for the full file.

### Share cards (`images/share/`, written by `pipeline/share.py`)
`pipeline/routes.py` calls `share.write_cards` with every page's canonical URL. Each cluster page and each Tier A
resort page gets a 1200 x 630 PNG card; its stubs (every state copy) set
`og:image` (`https://instructorgrindset.com/images/share/...`), `og:image:width` 1200, `og:image:height` 630,
`og:image:alt` and `twitter:card` `summary_large_image`. **Tier B resort pages, state pages and `/map/` keep the site
logo** (`/images/ig-logo.png`, `twitter:card` `summary`): about 415 Tier B cards would add roughly 11 MB to the
repository for pages that are not in the sitemap.
```
images/share/{state}/{cluster}.png              // cluster card; state = the cluster's canonical state
images/share/{state}/{cluster}/{resort}.png     // Tier A resort card; state = the resort's canonical state
images/share/.cards.json                        // { generated_by, note, cards: { "<path>": key } } (dotfile; Jekyll skips it)
```
* **Content.** The site name "Instructor Grindset Career Map" with the favicon mark; an eyebrow ("Tier A ski area",
  "Tier A indoor ski facility", "Tier A year round training center", or "Cluster"); the resort or cluster name
  (Inter Bold, 88 px down to 38 px, up to 4 lines, then an ellipsis); the cluster and state ("I 70 Corridor,
  Colorado"; the state alone for an `other-` cluster or one named after its state; for a cluster, its states); and
  a map panel: the focus state outline(s) from `us-states.geojson` with neighbouring states, the cluster polygon,
  the cluster's resorts (amber Tier A, blue Tier B) and, on a resort card, the resort as a ringed amber dot.
* **Facts.** A resort card shows up to three facts, each only when its `Sourced` value is a positive number with a
  `source_id`: `size.acres` ("Skiable acres"), `season.length_days` ("Season length", "N days") and
  `snow.annual_avg_in` ("Annual snowfall", "N in", the resort's own figure), each with its confidence label in the
  badge colors. A cluster card shows its resort count and Tier A count. Heat map values, pay, lucrativeness and the
  operator scale never reach a card (`tests/pipeline/test_share.py`).
* **Alt text.** "Career Map card for Vail, I 70 Corridor, Colorado, with the resort marked on a map of Colorado.
  5,317 skiable acres, 158 day season and 354 in annual snowfall." and "Career Map card for the Lake Tahoe cluster
  in California and Nevada, with its resorts marked on a map. 20 resorts, 7 Tier A."
* **Drawing (stdlib only).** `pipeline/raster.py` fills antialiased rings (nonzero winding, exact coverage along
  rows, 5 samples down each pixel, 32 alpha steps) into a palette image; `Canvas.indexed` keeps the solid colors and
  the most used blends (256 at most). Text uses vector outlines from `pipeline/fonts/inter-bold.json` and
  `inter-regular.json` (Inter 4.0, SIL OFL 1.1, `pipeline/fonts/OFL.txt`) with advance widths and the `kern`
  feature's pair kerning; `python -m pipeline.otf <font.otf> <out.json>` regenerates them from an Inter OTF.
  `png.encode_indexed` writes an 8 bit palette PNG (unfiltered rows, zlib level 9). Cards average about 27 KB and
  stay under 60 KB; 179 cards are about 5 MB.
* **Determinism and rebuilds.** Point coordinates are rounded to 1/256 px, so the same inputs give the same bytes.
  Each card's key hashes its plan, the state outlines and the drawing code (`share.py`, `raster.py`, `glyphs.py`,
  `png.py`, the font files). A card is drawn only when its key differs from `.cards.json` and written only when its
  bytes differ; cards no page uses are deleted. A first build draws all cards in about 75 s; a rebuild with no
  change takes under a second.
* **Fallbacks.** Without `us-states.geojson`, or when `images/share` resolves outside the site root (the browser
  test's throwaway site links the repository's `images`), no card is written and every page keeps the logo.

### `climate/{resort_id}.json` (Phase 3, lazy loaded per resort)
```
{ version, generated_at, resort_id,
  source_ids: string[], confidence: "estimated",
  snowfall_null_reason: string | null,          // why snowfall values are null; required when every month is null
  period: { start_year, end_year },             // Oct 1 of start_year to Sep 30 of end_year
  months: ClimateMonth[12] }   // ordered by ski season: Oct, Nov, Dec, Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep
ClimateMonth { resort_id, month (1..12), snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               snowfall_source: "snotel_swe" | "era5" | null,   // covers the four snowfall fields
               density_pct_median, density_mix: { dry, average, moist },   // fractions summing to 1, or null
               density_source: "snotel" | "estimated" | null,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
```
Monthly values are climatological means over `period`; `powder_days` and `banner_days` are mean days per month. Climate values are modeled (ERA5 reanalysis and SNOTEL derived), so every file carries `confidence: "estimated"`; the UI uses `as_of` equal to the file's `generated_at` and sources from `source_ids`.

Snowfall sources (`snowfall_source`), in order of preference:
* `snotel_swe`: daily new snow at the chosen NRCS SNOTEL station, from daily SWE gains divided by new snow density, never below the daily depth gain. Values describe the station elevation.
* `era5`: Open Meteo ERA5 daily snowfall, used only without a usable station and only for low relief resorts or where the ERA5 grid cell height is close to the resort base (thresholds in `config/definitions.yaml` `climate`).
* `null`: no source fits, or the derived annual total is more than `climate.validation_suppress_pct` from the published `Resort.snow.annual_avg_in`. When `snowfall_in` is null, `powder_days`, `banner_days` and `avg_storm_depth_in` are null too. Temperatures, wind, sun and density stay.

Open Meteo access: by default the ETL calls the free endpoint `https://archive-api.open-meteo.com/v1/archive` and keeps under the free tier limits. When the environment variable `OPEN_METEO_API_KEY` is set, it calls the customer endpoint `https://customer-archive-api.open-meteo.com/v1/archive` with the key in the `apikey` query parameter, as Open Meteo's docs describe. The daily budget guard then uses `climate.open_meteo.paid_daily_budget` from `config/definitions.yaml`; null keeps the free tier limits even with a key. The key lives only in the environment. It is never written to config, cache, build output or logs, and error messages redact it.

`climate/provenance.json` (`{ version, generated_at, resorts: { id: {...} } }`) records the ERA5 grid cell, the SNOTEL station and how it was chosen, QC counts and the validation result per resort. `docs/map/climate-validation.md` is generated from it.

## Phase 4 career

### Raw inputs (research fills these later; every value sourced)
* `data/raw/pay/*.yaml`: `{ batch, researched_date, sources: [...], records: [PayRecord] }` (see `data/raw/pay/README.md`)
  ```
  PayRecord { resort_id, season ("2025-26"), cert_level: none|level_1|level_2|level_3|trainer|examiner|unspecified,
              hourly_low, hourly_high, confidence: published|reported|estimated, source_id, verified_date, note?,
              aggregate_count? }   // reported rows only: number of approved reports aggregated (Phase 7)
  ```
* `data/raw/career/*.yaml`: `{ batch, researched_date, sources: [...], resorts: [CareerInputs] }` (see `data/raw/career/README.md`)
  ```
  CareerInputs { resort_id,
    private_rate: Sourced<{ low, high, unit: hour|half_day|full_day }> | null,   // guest price
    private_split: Sourced<number 0..100> | null,                               // instructor share, percent
    professionalism_inputs: { cert_pay_differential, paid_training_hours, examiner_trainer_presence,
                              private_lesson_share, year_round_staff, clinic_access },  // each Sourced<number 0..1 normalized> | null
    client_profile_tags: string[], enthusiast_types: string[], professional_types: string[] }  // from config vocab
  ```
  Tag vocab lives in `config/definitions.yaml` `career.vocab` (client profiles from ROADMAP Section 1:
  `destination_families`, `local_kids_programs`, `adaptive`, `race`, `freestyle`, `international`, `uhnw_privates`;
  enthusiast and professional vocab lists stay empty until Alexandre fills them; unknown tags fail). Career tags are
  merged into `resort.clients.profile_tags` and `resort.fit`.

### Build output: `Resort.career`
```
career: {
  pay: [ { cert_level, hourly_low, hourly_high, confidence, source_id, as_of, season, stale } ],
        // one row per cert_level (ordered none, level_1, level_2, level_3, trainer, examiner, unspecified): the most recent
        // season, then highest confidence (published > reported > estimated), then latest verified_date, then source_id.
        // `reported` rows only come from aggregates of reports.aggregation_min (3)+ approved reports and are never single reports
  private_rate: Sourced | null, private_split: Sourced | null,
  lucrativeness: { low, mid, high (USD estimated net income for the season, before tax) | null, status: computed|assumptions_unset|insufficient_data,
                   assumptions: [{ key, value, label }],
                   rent: RentUsed | null },   // the town's rent for the Housing section, never in the figure
  stale_fields: string[]   // "pay.<cert_level>", "private_rate", "private_split", "lucrativeness.rent"
}
RentUsed { value (USD per month), basis: median_rent | zillow_zori | acs_median | fmr_one_br | ... (the step used),
           label ("Median rent", "Zillow typical rent (ZORI)", "Census ACS median gross rent",
                  "HUD Fair Market Rent, one bedroom", ...),
           source_ids: Slug[] (every source behind the value, each linked), as_of, confidence, note,
           unit: all | studio | 1br | 2br | 3br | null,     // median_rent: the unit size used
           count: int | null, publishers: string[],          // median_rent: figures in the median and their publishers
           period: { first, last } | null,                   // median_rent: the data periods of those figures
           geography: { level: zip | city | county | metro, id, name } | null,   // where the rent applies
           stale: boolean }                                  // as_of older than staleness.max_age_months
```
Schema: `pipeline/schemas/rent.py` (`RentUsed`); Zod: `RentUsed` in `map/js/schemas.js` (a bad block drops the rent
line, never the resort).
* **Staleness:** a value is stale when its `as_of` / `verified_date` plus `staleness.max_age_months` (18) calendar months is
  before the build date. A value exactly 18 months old is still current.
* **Professionalism (owner decision, 2026-10-13, later the same day):** removed from the public site. `operator_scale`
  stays in `data/raw/tier_a` as an internal note; the build drops it, `build_career` emits no professionalism block, and
  no file in `data/build`, no route stub and no map module names it (`tests/pipeline/test_no_professionalism.py`). The
  six component model in `pipeline/career.py` stays for the owner's private use only.
* **Lucrativeness (owner decisions 4, 2026-10-13, and 2026-10-14):** estimated net income for the season, before
  tax, USD.
  season months = `season.length_days` / `days_per_month` (30.4) when sourced, else `default_season_months` (5).
  net income = hourly × `hours_per_week` (40, standard full time) × season months × `weeks_per_month` (4.33).
  No rent, tips, tax, food, transport, pass or gear (owner decision 2026-10-14: "remove rent and tips from math").
  The wage range comes from the displayed pay rows, any cert level: bottom = lowest `hourly_low`, top = highest
  `hourly_high`. `low` is the net income at the bottom, `high` at the top, and `mid` at the middle of the range,
  (bottom + top) / 2 (the mid earnings case). Every resort with a pay row computes. Status `insufficient_data` when
  there is no pay row; `assumptions_unset` only if hours or the default season months are null in config.
  `assumptions` (each `{key, value, label}`): `wage_mid_usd` (label "Middle of the posted wage range (USD per hour)"),
  `wage_top_usd`, `wage_bottom_usd`, `hours_per_week`, `season_months` (label says sourced length or standard season),
  `season_months_basis` (`season_length` | `default`), `weeks_per_month`, `tax` (label "Before tax and other living
  costs"). `rent` is the town's rent block (see "Lucrativeness rent" below), kept for the Housing section only.
  Wording (owner decision 2026-10-14): the panel heading and the Color by mode read "Estimated net income". The panel
  shows only `mid` and the line "Range: $low to $high", with links to the pay and season length sources. It shows
  no formula, no assumptions list and no rent. The figure is never called savings.
* **Lucrativeness rent (owner decision 2026-10-14; `pipeline/rent_used.py`):** the first step of `rent.lucrativeness_rent`
  (`[median_rent, zillow_zori, acs_median, fmr_one_br]`) with a value becomes `lucrativeness.rent`; nothing is ever
  interpolated. It shows in the Housing section ("Rent near the resort") and no longer feeds the figure. Also available as steps: `employee_housing`, `reported_area`, `baseline`, `fmr_studio`, `fmr_two_br`.
  A rent whose `as_of` is older than `staleness.max_age_months` is `stale` (Needs update, `stale_fields`
  `lucrativeness.rent`, a build warning). A Tier A record whose location source says its point is a placeholder
  (MetroSlopes at SFO) gets no rent by place (ZORI, ACS, HUD).
  * `median_rent` (`pipeline/rent_median.py`, settings `rent.median`): raw `data/raw/rent/median_rent_<batch>.yaml`
    `{ batch, researched_date, sources, figures: [{ resort_ids, town, geography: city|zip|county|metro,
    unit: all|studio|1br|2br|3br, median_rent_usd, period (YYYY | YYYY-MM | YYYY-MM-DD), publisher, publisher_name?,
    source_id, as_of, confidence (never reported), note }] }`. Figures whose period ended more than `max_age_months` (18)
    before the build are ignored. The publisher is the registered domain of the source URL, named by
    `rent.median.publishers` (else `publisher_name`, else the domain). A `last_resort_publishers` figure (Foreclosure.com)
    counts only when no other publisher has a figure, of any unit size, for the same town at the same level. Only the best
    level with a usable figure counts (`geography_order`: zip, city, county, metro; never mixed); within it, `all` unit
    figures when any, else `1br` (`units`); 2br and 3br figures are recorded but never used. The value is the median of
    those figures, one per source page (even count: the mean of the two middle values); outliers stay as published.
    `as_of` is the oldest figure's, `confidence` the weakest, `geography.name` the town ("Vail, CO"), "<city> metro",
    "<city> micro area" or "Municipality of Anchorage".
  * `zillow_zori` (`pipeline/rent_zillow.py`, settings `rent.zori`): Zillow Observed Rent Index, "ZORI (Smoothed): All
    Homes Plus Multifamily Time Series ($)". The build reads the committed `data/raw/rent/zillow/zori_extract.csv`; full
    CSVs (`County_`, `Zip_`, `Metro_zori_uc_sfrcondomfr_sm_month.csv`, downloaded by `python -m pipeline.rent_zillow
    fetch` into `zillow/download/` or placed by hand in `zillow/`, both gitignored) rebuild it on the next build. Each
    region's value is its latest month with a value (`as_of` = that month end); the trailing 12 month mean is kept
    (`value: trailing_mean` would use it). Match: the nearest ZIP whose Census Gazetteer ZCTA centroid lies within
    `zip_radius_km` (10) of the base (skipped without the Gazetteer file), else the county by FIPS, else the county's
    metro, else none. Source `zillow-zori` ("Zillow Observed Rent Index (ZORI), Zillow Research",
    https://www.zillow.com/research/data/, confidence `published`, note "ZORI is a typical market rent for all homes and
    multifamily, smoothed"). Owner instructions: `data/raw/rent/README.md`.
  * `acs_median`, `fmr_one_br`: the county baseline (Phase 5), with `geography` the county.
* **Shared calculator model (Phase 9):** lucrativeness is computed by `pipeline/season_income.py`, the Python mirror of
  `assets/js/season-income.mjs` used by the Season Income Calculator, unchanged, called with rent, tips and every
  offset at 0 (`lucrativeness_inputs` with season length = months × 4.33 × 7 days): net income = its `net_before_tax`
  (then equal to its `gross`), so the map and the calculator agree for the same inputs. The model follows the
  calculator on main: the rest of a private day is paid at the group rate, federal tax applies after the 14600
  standard deduction, and transportation is a monthly cost. With no privates, no transport and a before tax figure,
  the map's values are the same doubles as before. Whole dollars
  use `round_half_up` (JS `Math.round`, ties toward positive infinity). The calculator's headline net is after tax;
  the map's lucrativeness is net income before tax (ROADMAP Section 6) and applies no tax assumption, which stays the
  owner's call. The calculator has state income tax rates for only 13 states (WA, WY, NV, AK, NH, CO, UT, ID, VT, MT,
  NY, OR, CA).

## Phase 5 housing and commute

Raw inputs (every value sourced; research and ETL fill them later):
* data/raw/housing/*.yaml: `{batch, researched_date, sources: [...], sites: [HousingSite]}`
  HousingSite { id, resort_id, name, town, type: resort_owned|master_lease|unspecified|market, lat, lon, location_source_id,
                listing_source_id | null,
                precision: building|neighborhood,   # building only when the address is publicly listed
                rent_monthly: Sourced<{low, high}> | null, occupancy: shared|private|mixed|null, deposit: Sourced<number>|null,
                eligibility_note: string|null, confidence, verified_date }
* data/raw/rent/: median rent figures (`median_rent_*.yaml`) and the Zillow ZORI extract (`zillow/zori_extract.csv`)
  for lucrativeness (Phase 4, "Lucrativeness rent"); HUD Fair Market Rents and Census ACS median gross rent, cached API responses or CSV
  (county and ZIP level), loaded by pipeline/rent.py into a baseline per resort (county of the base area,
  plus ZIPs of housing sites). HUD API needs a token in env HUD_API_TOKEN; ACS works without a key at low volume.
* data/raw/transit/<agency>/gtfs.zip (gitignored) -> pipeline/transit.py extracts routes and stops within
  `transit.search_radius_km` of any housing site or the base area; committed extract data/raw/transit/<agency>.json.
  data/raw/transit/feeds.yaml lists each feed from the Mobility Database catalog: the agency's own `url`, the catalog
  `mdb_source_id`, its Google Cloud Storage `mirror_url` (downloaded first), the catalog `license` and optional
  `agency_ids` (data/raw/transit/README.md).
* data/raw/routing/ cache (gitignored) of routing responses. pipeline/routing.py: provider in config
  (`routing.provider: valhalla|osrm|ors`, base URL, API key env var), drive time/distance and route geometry
  housing -> base, and drive isochrones at `routing.isochrone_minutes` [15, 30, 45] around the base area.
  Transit commute times come from GTFS schedules (walk to stop + ride, simplest sound model, documented) or are
  null; never estimated from memory.
* Lifts: OpenSkiMap https://tiles.openskimap.org/geojson/lifts.geojson (reachable now); keep lifts whose
  skiAreas reference the OpenSkiMap area(s) matched to each Tier A resort; committed extract
  data/raw/openskimap/us_lifts_tier_a.geojson (full download gitignored).

Build outputs:
* data/build/detail/<resort_id>.json, lazy loaded by the resort detail map (Tier A only):
```
{ version, generated_at, resort_id,
  base_area: { lat, lon, source_id },
  lifts: FeatureCollection<LineString> props { name, lift_type, source_id: "openskimap-lifts" },
  housing: [ { ...HousingSite fields, rent_monthly (Sourced range, display priority applied, HUD/ACS baseline
               used only when no site rent exists and then confidence "estimated" with the baseline source),
               commutes: [ { mode: drive|bus|walk|shuttle, minutes, distance_mi, transit_route_ids, geometry
               (LineString), source_id } ] } ],
  rent_baseline: { county_fips, county_name, fmr: { studio, one_br, two_br } each Sourced, acs_median_gross_rent:
                   Sourced } | null,
  transit: { routes: [ { id, agency, agency_name, agency_url, short_name, long_name, color, geometry (MultiLineString) } ],
             stops: [ { id, name, lat, lon, route_ids } ], source_ids,
             feeds: [ { source_id, agency, as_of, valid_to, agencies: [ { name, url } ], covers_winter, ended } ] } | null,
             // agency = feed slug; agency_name and agency_url from agency.txt (url only when http or https);
             // as_of = start of the feed's validity; covers_winter and ended drive the schedule note (null when unknown)
  isochrones: [ { minutes, mode: "drive", geometry (Polygon|MultiPolygon), source_id } ] }
```
  Written for every Tier A resort that has at least lifts or housing; empty lists or null where data is missing.
* resorts.json Resort gains `detail_available: bool` and
  `housing_summary: { site_count, cheapest_rent_monthly_low (number|null), cheapest_site_id, has_transit } | null`.
* (Superseded by owner decision 2026-10-14: lucrativeness takes its median rent from `pipeline/rent_used.py`; see Phase 4.)
  The former Phase 4 lucrativeness rent input used the cheapest employee housing site (resort_owned or master_lease) rent,
  else the baseline (studio FMR), per config; career output echoes which was used.
* config/definitions.yaml gains: `routing: {provider, base_url, api_key_env, isochrone_minutes: [15, 30, 45]}`,
  `transit: {search_radius_km, simplify_tolerance_m, target_season: {start, end}, max_validity_years, winter_window: {start, end}, winter_min_overlap_days}`, `rent: {baseline_preference: [fmr_studio, acs_median]}` with sensible defaults
  for provider settings and nulls for anything that is a judgment call for Alexandre.

### Pipeline notes (additions to the contract)
* **HousingSite** also takes `address: Sourced<string> | null`; `precision: building` requires it (the publicly listed address and its source). Site ids are unique across all housing files; `resort_id` must exist in the build.
* **HousingSite** also takes `town: string | null` (ROADMAP item 8c), the town the site is in when a source names it.
  Detail housing always carries `town`: the raw value, else the town of the listed `address` when it reads "street,
  town, ST 12345" (`housing.town_from_address`; "street, town ST 12345" and a trailing "USA" work too), else null.
  Every file in `data/raw/housing/` is loaded (`employee_housing_2026_10.yaml` included), after the editorial files,
  so a site may cite a place source declared in `data/raw/editorial`; sites of a Tier B resort give a build warning
  and are not shown, since detail files are Tier A only.
* **HousingSite** `type: unspecified` (coordinator, 2026-10-14) marks a site the resort lists for its staff without
  saying whether it owns or leases it; it is never assumed `resort_owned`. It counts as employee housing
  (`EMPLOYEE_HOUSING`: resort owned, master lease, unspecified) and draws in pink (`#d6609f`) on the detail map.
* **HousingSite** also takes `listing_source_id: slug | null`, the source page that lists the site; it must be a
  defined source. The panel shows it as the site's main source link, beside the location source.
* **County lookup:** point in polygon of the base area against `data/raw/geo/us-counties.geojson` (us-atlas counties-10m via `npm run build:counties`, committed). ZIP level baselines need ZCTA outlines and are not wired yet.
* **Rent sources:** `hud-fmr-fy<year>` and `census-acs5-<year>-b25064`. A site without published rent shows the first value in `rent.baseline_preference` as `{low: v, high: v}`, confidence `estimated`, with a note naming the baseline.
* **Lucrativeness rent:** see Phase 4 (owner decision 2026-10-14): `rent.lucrativeness_rent` steps `median_rent`, `zillow_zori`, `acs_median`, `fmr_one_br`; also available: `employee_housing` (mean of employee housing rent mids), `reported_area`, `baseline`, `fmr_studio`, `fmr_two_br`. Commutes: no walking speed or distance model is used (owner decision 6); each Tier A resort carries the researched `access` rating instead.
* **Transit extract** (`data/raw/transit/<agency>.json`): `{ version, agency, source, feed, search_radius_km, simplify_tolerance_m, routes, stops, patterns }`, written one route, stop or pattern per line; ids are prefixed `<agency>:`; `feed: { mdb_source_id, url, mirror_url, license, agency_ids, version, valid_from, valid_to, validity_basis: feed_info|calendar|feed_info+calendar, as_of }` where the validity comes from feed_info.txt, else calendar.txt and calendar_dates.txt, and `as_of = valid_from`; `source` is `gtfs-<agency>` with the agency's feed URL and a note naming the catalog entry, the mirror, the validity and the license; route lines are Douglas Peucker simplified to `transit.simplify_tolerance_m` and deduplicated; `agency_ids` keeps only those GTFS agencies of a shared feed; GTFS Flex rows without a `stop_id` are ignored. **Feed currency:** current when the validity overlaps `transit.target_season` and spans at most `transit.max_validity_years` (longer is an open ended placeholder), or when `as_of` is within `staleness.max_age_months` of the build date; `python -m pipeline.transit extract` skips other feeds and the build drops an extract that is no longer current (or has no dates) with a warning, so stale transit never renders. **Schedule note:** the build flags each kept feed `covers_winter` (its validity overlaps one yearly `transit.winter_window`, Dec 1 to Mar 31, by at least `transit.winter_min_overlap_days`) and `ended` (`valid_to` before the build date). The detail map legend lists each feed's agencies (linked to `agency_url` in a new tab), "Schedule for <start> to <end>", and one note: "Offseason schedule. Winter ski service can differ; check the agency." when `covers_winter` is false, "Last season's schedule. This winter's service can differ; check the agency." when a winter feed has ended (`map/js/labels.js`); hovering a route shows "Route: <name>, <agency>. Schedule for ..."; `patterns: [{ route_id, stop_ids, offsets_min, trip_count }]` hold median scheduled minutes from the first kept stop. Bus commute = walk to a stop within `transit.max_walk_km` + scheduled ride + walk from a stop within `max_walk_km` of the base, at `transit.walk_speed_kmh`, no waiting time; null until Alexandre sets both.
* **Routing:** source id `routing-<provider>`; the build reads only the cache `data/raw/routing/<provider>/<sha1>.json` written by `python -m pipeline.routing run`. Drive commute `source_id` is `routing-<provider>`; bus commute `source_id` is `gtfs-<agency>`.
* **Lifts:** extract features carry `{ id, name, ref, lift_type, ski_area_ids }`; only `status: operating` lifts. Detail lifts carry exactly `{ name, lift_type, source_id }`. `name` may be null in OpenStreetMap.
* **Detail files** are written compact; files for resorts that no longer qualify are removed. `housing_summary.cheapest_rent_monthly_low` uses published or reported site rents (never the baseline); `has_transit` is true when the detail file has transit.

## Official trail maps (page links and embedded files)

Official trail maps are copyrighted art owned by the resorts or their artists (James Niehues and others). Owner
decision 2026-10-14 (ROADMAP Section 10): the trail map dialog shows the resort's own map file, loaded straight from
the resort's servers (hotlinked) and credited "Trail map © <resort>" with a link to the resort's page. The site never
downloads, copies, rehosts or modifies the art, and the repository never holds an image of it.
* Raw input `data/raw/trailmaps_official/<batch>.yaml` (see its README): `{ batch, researched_date, sources: [Source],
  trailmaps: [{ resort_id, url, format: pdf|jpg|png|page, season ("2025/26" or null), source_id, as_of, confidence,
  note?, image_url?, image_format?: jpg|png|webp|pdf, image_source_id?, image_as_of?, image_confidence?,
  image_note? }] }`, validated by `pipeline/schemas/trailmaps_official.py` and loaded by `pipeline/trailmaps_official.py`.
* Rules: https only; the url's domain (last two labels) equals its source's, which must be the resort's or its
  operator's own site; pdf, jpg and png need that file extension and page must not be a file; season only when the
  source names it; one record per resort. URLs found by web search follow the search result rule (source note "From a
  search result for this page; page not opened", confidence `estimated`).
* Map file rules (`image_*`): the five fields `image_url`, `image_format`, `image_source_id`, `image_as_of`,
  `image_confidence` come together; `image_note` needs them. `image_url` is https and ends in the format's extension
  (`.jpg`/`.jpeg`, `.png`, `.webp`, `.pdf`; query strings allowed), or an image CDN names the format in `fmt=` (scene7
  style). Its host is the resort's own site (the registered domain of the page `url` or of its sources) or an operator
  image CDN in config `trailmap.official_image_domains` (an entry covers its subdomains), and it matches its own
  source's site the same way. Only file URLs seen in a search result or on a page; never one built from a pattern.
* Build output: `Resort.official_trailmap` (the record without `resort_id`, every `image_*` key present and null when
  there is no file); its sources join `resort.sources`. Route stubs prerender the page link. The panel shows "Official
  trail map from <resort name>" with the format and season, and "Open the trail map" when there is a file or an
  OpenSkiMap drawing. The file the dialog shows is `image_url`, else `url` when its format is pdf, jpg or png
  (`officialMapFile` in `map/js/panel.js`). The link checker (`pipeline/link_check.py`) checks `image_url` monthly too.
* 2026-10-14 files: 42 web searches (operator CMS paths such as `/-/aemasset/sitecore/` and `/-/media/`, legend
  wording found only inside map PDFs, two to ten domains per query). Search results never return image file URLs, so
  every file found is a PDF: Vail, Park City, Okemo, Stevens Pass, Wilmot, Mount Sunapee, Hidden Valley (PA), Liberty,
  Afton Alps (Vail Resorts), Crystal Mountain, Sugarbush, Snowshoe, Palisades Tahoe Alpine (back side), June Mountain
  (Alterra), Grand Targhee, Cranmore and Mount Bohemia. With the 7 records whose `url` is a PDF, 24 Tier A resorts
  embed their official map. Older files carry an `image_note`. No file uses `official_image_domains` yet.
* 2026-10-13 batch: 96 Tier A resorts from 19 searches (multi domain queries; the Vail Resorts pattern
  `/the-mountain/about-the-mountain/trail-map(s).aspx` was guessed once and every URL was seen in a result). Still
  without a link: Alpine Valley (OH), Alta (winter), Black Mountain (ME), Burke, Hermitage Club, Hyland Hills, Mad River
  Glen, Montana Snowbowl, Saskadena Six, Silverton, Yellowstone Club, and the training and indoor facilities.

## Webcams and jobs (links only)

Owner requests 2026-10-13: link every resort's live cams, and its jobs: ski school, coaching and club (race,
freeride, freestyle and other disciplines), management, parent company and job boards, with live open role counts
where a public board exists. Cams, streams and job pages are linked in a new tab (`target="_blank"`,
`rel="noopener noreferrer"`) and never embedded, proxied or rehosted.

### Raw inputs (research batches; formats are fixed)
* `data/raw/webcams/<batch>.yaml`: `{ batch, researched_date, sources: [Source], records: [{ resort_id,
  cams_page: { url, source_id, as_of, confidence, note? } | null, cams?: [{ label, url, kind: page|image|stream,
  source_id, as_of?, confidence?, note? }] }] }`, validated by `pipeline/schemas/webcams.py`, loaded by
  `pipeline/webcams.py`. A record needs a `cams_page` or at least one cam; a cam without `as_of` and `confidence`
  takes the cams page's (and fails when there is no cams page).
* `data/raw/jobs/<batch>.yaml`: `{ batch, researched_date, sources, employers: [Employer], records: [{ resort_id,
  links: [JobLink] }] }`, validated by `pipeline/schemas/jobs.py`, loaded by `pipeline/jobs.py`.
  ```
  Employer { id, name, kind: parent_company|resort_operator|club|ski_school|other, careers_url,
             ats: { vendor: greenhouse|lever|smartrecruiters|workable|ashby|workday|icims|ultipro|paylocity|adp|paycom|
                            jazzhr|bamboohr|other|unknown, board?: token }, source_id, as_of, confidence, note? }
  JobLink { employer_id, role_group: ski_school|coaching|management|corporate|all,
            disciplines?: [race|freeride|freestyle|park|nordic|adaptive|snowboard|moguls], label, url,
            source_id, as_of, confidence, note? }
  ```
* Hiring windows (ROADMAP item 6): a jobs batch may also hold `hiring_windows: [{ resort_id, text, source_id, as_of,
  confidence, note? }]`, the source's own sentence on when applications open, job fairs or clinics start, copied word
  for word (max 400 characters, no em or en dashes). One per resort across all files. `data/raw/jobs/hiring_windows.yaml`
  holds the 27 from the pay and training research; their sources live in `data/raw/facts/training.yaml`,
  `data/raw/pay` and `data/raw/jobs`.
* Missing or empty folders are fine (no records). Rules, each a build error: an `http` (or any non https) url; an
  unknown resort, employer or source id; a bad enum value; a resort listed twice in one file; webcams for one resort
  in two files; a cam url or label listed twice, or a cam equal to the cams page; a jobs url listed twice in one role
  group of a resort (one board may serve several groups); an employer declared again with a different name, kind,
  careers url or ats. Jobs records for one resort in several files merge.
* Domains (registered domain, the last two labels): a webcam url (cams page or cam) must equal its own source's
  domain. A jobs link url must be the employer's careers domain, one of its ATS vendor's domains
  (`jobs.ats_domains`, for example myworkdayjobs.com for Workday) or its own source's domain (a page on the employer's
  site while its careers page sits on the ATS). Job aggregators in `jobs.blocked_domains` (Indeed, Glassdoor,
  ZipRecruiter, SimplyHired, Monster) always fail. An `ats.board` on a vendor without a public postings API (a
  Workday tenant) is kept and never fetched.

### Build outputs
* `Resort.webcams` (cams trimmed to `webcams.max_named_cams`, 4) and `Resort.jobs` (`links` ordered ski_school,
  coaching, management, corporate, all, file order within a group; `feed` true when a linked employer has a public
  board). Their sources join `resort.sources`, so stubs embed them; `map-index.json` gains `webcams_available` and
  `jobs_available`, plus `hiring_window` and `job_employers` for the Hiring now filter.
* `Resort.jobs.hiring_window.start_month` and `end_month` (`pipeline/hiring.py`, see "Hiring now filter" below).
* `jobs/employers.json`: `{ version, generated_at, employers: [Employer + { feed, board_url }] }`, every employer once
  (parent companies are not repeated per resort). `board_url` is the public board page when `feed` is true:
  `https://boards.greenhouse.io/{board}`, `https://jobs.lever.co/{board}`, `https://careers.smartrecruiters.com/{board}`,
  `https://apply.workable.com/{board}/`, `https://jobs.ashbyhq.com/{board}`.
* `jobs/postings.json` (written by the feed; the build writes an empty one when it is missing and validates it
  otherwise, never overwriting it):
  ```
  { version, generated_at, fetched_at: ISO datetime | null, keywords: string[],
    boards: [{ employer_id, employer_name, vendor, board, url (board page), source_id, status: ok|error,
               fetched_at (last good fetch) | null, as_of | null, total, matched, error | null }],
    sources: [Source],                     // one per board: the board page, accessed_date = as_of
    postings: [{ id, employer_id, title, location | null, department | null, url, role_group (a guess),
                 resort_ids: string[],     // [] = a role at the employer with no single resort named
                 source_id, as_of, confidence: "published", fetched_at }] }
  ```

### Live postings feed (`pipeline/jobs_feed.py`, `.github/workflows/jobs-feed.yml`)
* `python -m pipeline.jobs_feed fetch` reads `jobs/employers.json` and `resorts.json` and, for employers with
  `feed` that some resort links, calls only these public keyless APIs: Greenhouse
  `boards-api.greenhouse.io/v1/boards/{board}/jobs`, Lever `api.lever.co/v0/postings/{board}?mode=json`,
  SmartRecruiters `api.smartrecruiters.com/v1/companies/{board}/postings` (pages of 100, at most 10), Workable
  `apply.workable.com/api/v1/widget/accounts/{board}`, Ashby `api.ashbyhq.com/posting-api/job-board/{board}` (listed
  jobs only). Workday, iCIMS, UKG, Paylocity, ADP, Indeed, Glassdoor and every other page are never fetched or scraped.
* Kept: a posting whose title, department or team holds a `jobs.feed.keywords` word or phrase (whole words, any case;
  instructor, coach, ski school, snowsports, ride school, supervisor, director, trainer, freeride, race, freestyle,
  adaptive and more), none of `exclude_keywords`, and, for employers in `require_keywords` (a college board such as
  Middlebury's), one of their words in the title, department or location. Posting urls must be https on the vendor's
  or the careers domain; at most `max_postings_per_board` (150) per board.
* Role group guess: the first of `jobs.feed.role_keywords` management, coaching, ski_school with a matching word;
  else corporate for a parent company, else all.
* Resorts: an employer linked from one resort gives every kept posting to it. For an employer linked from several
  resorts, a posting goes to the resorts whose name (also without a trailing "Ski Resort", "Ski Area" or "Resort") or
  `jobs.feed.location_aliases` entry appears in its location, else in its title or department (the employer's own
  name removed first); terms under four letters never match. None named gives `resort_ids: []`.
* Writes only when postings or board states change (timestamps ignored) or the last fetch is `refresh_days` (7) old,
  so the daily job commits only real changes. A failed board keeps its last postings and `fetched_at` for
  `max_age_days` (14), then drops them.
* `jobs-feed.yml`: daily cron and `workflow_dispatch`, `permissions: contents: write` (plus `pages: write` to request
  a Pages build after a push), default `GITHUB_TOKEN`; commits only `data/build/jobs/postings.json`. Scheduled runs
  start only once the workflow is on the default branch. Tests use recorded responses in
  `tests/pipeline/fixtures/jobs_feed/` (synthetic, in each vendor's documented shape); the sandbox cannot reach the APIs.

### Link checker (`pipeline/link_check.py`, `.github/workflows/link-check.yml`)
Monthly (cron on the 1st and `workflow_dispatch`): every `official_trailmap`, cams page, cam and jobs link url in
`resorts.json` plus every careers page in `jobs/employers.json`, once each. HEAD, then GET when HEAD is refused; one
retry for timeouts and server errors. Broken: HTTP 404 or 410, no DNS answer, an invalid TLS certificate. Could not
verify: other error statuses (often bot protection) and timeouts, listed in a folded section. The workflow
(`contents: read`, `issues: write`) opens or updates one issue labelled `link-check` while links are broken and
closes it when none are; it never commits.

### UI (`map/js/livelinks.js`; stubs in `pipeline/routes.py` `render_webcams`, `render_jobs`)
* Resort panel, under the trail map links: "Live cams", then "Open live cams" (the cams page) with its badge,
  site name, "opens in a new tab" and "Last verified", then up to four named cams with badge and kind (Web page,
  Still image, Live stream).
* "Jobs" section after Career: "Hiring window: <text>" with its badge, date and source when the resort has one, then
  "Links open each employer's own site in a new tab.", then groups in this order: Ski
  school (`ski_school`), Coaching and clubs (`coaching`, disciplines as small tags), Management (`management`),
  Parent company (`corporate`), Job boards (`all`). Each link has its badge and "Last verified <date>", plus its
  source when the source is another page. Tier A resorts without links say "Jobs links are being collected." (after
  a hiring window when there is one); Tier B resorts without links or window show no section.
* Open roles (panel only, never prerendered since they change daily): when `jobs.feed` is true the panel loads
  `jobs/postings.json` once (`loadPostings`, lazy, Zod `PostingsFile`) and, for postings of the resort's linked
  employers, shows "N open roles" (Published badge) for postings naming this resort with up to five titles linked out
  (ski school first), "N more roles on the job boards below.", "Plus N roles at <employer> with no single resort
  named." for employer wide postings, and "Updated <earliest board as_of> from <employer> job board." With an empty
  feed, no match or no file, the section shows the links only.
* Cluster panel: a "Hiring" section after the resort list, when postings exist for its resorts: each employer (link
  to its board) with a Published badge and "N open roles", then "Updated <date>.". The resort list adds "Live cams".
* Zod: `Webcams`, `Jobs`, `PostingsFile` in `schemas.js`; a bad item drops only that item, a bad block only the block.

### Hiring now filter (ROADMAP item 13; `pipeline/hiring.py`, `map/js/hiring.js`, `map/js/app.js`)
* Window months (`parse_window`): the build reads `start_month` and `end_month` from a hiring window's text only when
  the text is explicit about both. The start is the date a posting or the applications opened ("Instructor posting
  opened July 1, 2026", "Posted July 1, 2026", "applications open August 15"), or else a statement that hiring is open
  ("Hiring is rolling", "Posting open and still accepting applications", "Now hiring"), which counts from the month of
  the window's `as_of`. The end is an application deadline ("applications are accepted until December 15",
  "accepting applications until", "apply by")
  or the job's own start date ("target start of", "estimated start", "start date is", "positions begin in").
  A date without a year takes it from the other end (an end falls on or after the start). Unparsed (both null):
  season or employment dates, arrival dates, words in place of a month ("early fall", "once the snow flies"), hedged
  dates ("roughly November 23"), two different starts or ends, no year anywhere, an end before the start, and a span
  over `jobs.hiring_windows.max_months` (9). Month names must be capitalized ("may" is never a month). The text, its
  source, `as_of` and confidence stay as they are. On 2026-10-13: 9 of 27 windows parse (Vail, Hunter, Mount Snow,
  Stowe, Liberty, Arapahoe Basin, Sunday River, Sugarloaf, Copper). Tests: `tests/pipeline/test_hiring.py`.
* A resort is hiring now when (a) `jobs/postings.json` has an open role at one of its `job_employers` (a board in
  `boards`, an https url) that names this resort or no single resort, or (b) its `hiring_window` months cover the
  viewer's current month (both ends included).
* Filter: the first checkbox of "Show only" in the layers panel (group label "Filter resorts", after the Type radio
  group, before the pass checkboxes; see "Console look"). It reads "Hiring now" until `hiring.js` has counted, then
  "Hiring now (N)" (`markHiring` reports N to app.js, which labels the option and the header pill). It switches on
  and off and combines with the other filters using AND; it filters the map (an `in` filter on resort ids over the
  resort and facility layers), the 2D picture before the map starts, and the list view, and is announced ("Showing N
  resorts hiring now."). At N = 0 it stays visible with `aria-disabled="true"` and the title "No resort lists an open
  role or a hiring window for this month."; a click only announces that (or turns an active Hiring now off). The
  header's HIRING NOW pill shows N and switches the same filter (`aria-pressed`); the map rings each hiring resort in
  green, with a pulse unless the viewer prefers reduced motion.
* Cost: nothing on the startup path but the two index values and the controls. `hiring.js` (about 1.5 KB gzipped, no
  Zod) loads once the window `load` event and the first paint have passed, at idle, or on the filter's first use. It
  counts the windows from the slim index at once, then, when some row has `job_employers`, reads `jobs/postings.json`
  (checked by hand; the panel's `loadPostings` reuses the same request) and adds the open roles, updating a selected
  filter. The feed is empty today, so the count is the windows alone. Keeping the count off the startup path matters:
  1.1 KB more gzipped JavaScript on it moved the simulated Lighthouse LCP on `/map/` from 1.96 s to 2.12 s (one more
  slow start round trip), with the score unchanged at 98.

## Pass membership (ROADMAP item 12; `pipeline/passes.py`, `map/js/app.js`, `map/js/panel.js`)

Instructors often pick a mountain by its pass benefits, so each resort records which multi resort pass families it is
on for a season, with the access level of the family's top pass (the Epic Pass, the Ikon Pass, the Mountain Collective
pass, the Indy Pass) where the pass company states it.

### Raw input: `data/raw/passes/<season>.yaml`
```
{ batch, researched_date, sources: [Source],
  memberships: [{ resort_id, pass: epic|ikon|mountain_collective|indy, access: unlimited|limited|unspecified,
                  days?: int 1 to 366, blackout?: bool, season: "2026/27", source_id, as_of, confidence, note? }] }
```
validated by `pipeline/schemas/passes.py` and loaded by `pipeline/passes.py` (through `links.load_batches`).
* `access`: `unlimited`; `limited` (with `days` when the page states a number; days shared between mountains go in the
  note, for example "7 days combined with Pico"); `unspecified` when the page lists the resort without days.
  `blackout` only when the page says whether blackout dates apply to that pass.
* Rules, each a build error: an unknown resort; the same resort, pass and season twice (in one file or across
  files); a bad enum value; `days` without `access: limited`, or under 1; a season that is not two consecutive years
  ("2026/27"); an undefined source; a source whose registered domain is not the pass company's own
  (`passes.domains` in `config/definitions.yaml`: epicpass.com and vailresorts.com for Epic, ikonpass.com for Ikon
  (lifted.ikonpass.com included), mountaincollective.com, indyskipass.com) or a `passes.supplemental_domains`
  reference (wikipedia.org). An owner's portfolio page never proves a pass: ownership is never membership.
* Season (`passes.season`, "2026/27"): per resort and pass the record for that season wins. Without one, and with
  `passes.fallback_previous_season: true`, the record for the season one year back ("2025/26") is used and marked
  `season_fallback: true`; never an older season, never a later one. The build warns with the count ("data/raw/passes:
  36 memberships use the 2025/26 list (no 2026/27 record; passes.fallback_previous_season)"), which is not an error.
  Every other record is validated and left out with a warning, so next season's batch can sit next to this one until
  `passes.season` moves. `season_fallback` is set by the build only; a raw record that sets it fails. A record's
  `season` is the season its source shows, never a guess.
* 2026-10-13 batch (`data/raw/passes/2026_27.yaml`): the pass sites are blocked from the research environment, so
  every value comes from a web search result that named the page (confidence `estimated`, note "From a search result
  for this page; page not opened"); 30 searches. Wikipedia has no Epic or Ikon list (both titles redirect to the
  owners' articles), so it is cited only for which mountains make up a destination (The Summit at Snoqualmie,
  Palisades Tahoe). Coverage: Epic 37 resorts (the 36 Vail Resorts US resorts listed on the epicpass.com region
  pages, plus Telluride, 7 days); Ikon 49 (18 unlimited, 26 with 7 days, 5 listed without days); Mountain
  Collective 16 (its 13 US destinations, Aspen Snowmass as four mountains, 2 days each). The Epic unlimited list was
  seen under a 2025/26 heading of the Epic Pass page, so those 36 records carry `season: 2025/26` and reach the build
  as previous season fallbacks; Telluride's 2026/27 page gives it a 2026/27 record. Left out: Indy Pass (about 200
  destinations, beyond the search budget), the Three Summits record (identity unclear), Windham and Jay Peak (no
  pass page found).

### Build output
* `Resort.passes` (the memberships without `resort_id`, in the order epic, ikon, mountain_collective, indy; `[]` for
  none); their sources join `resort.sources`, so stubs embed them. `map-index.json` gains `passes` (pass ids or null).
  About 0.3 KB more gzipped on the index.

### UI
* Checkboxes: "Epic" and "Ikon" (titles "Resorts on the Epic Pass", "Resorts on the Ikon Pass") follow Hiring now
  under "Show only" in the "Filter resorts" group of the layers panel. Type is a radio group (All, Ski areas, Indoor
  and training: one facility type, All by default); Hiring now, Epic and Ikon switch on and off; "Show every resort"
  (shown while any filter is on) clears them all. Active parts combine with AND; Epic and Ikon together keep resorts
  on either pass (`data.js` `matchesFilter`). The rule
  applies to the WebGL map (one `in` filter on resort ids plus the facility layer visibility), the 2D picture before
  the map starts (`preview.js` `keep`) and the list view. Announcements: "Showing 37 resorts on the Epic Pass.",
  "Showing 6 resorts on the Epic Pass or Ikon Pass.", "Showing 2 ski areas hiring now on the Ikon Pass.",
  "Showing every resort." A pass checkbox with no resort on its pass in the loaded index stays visible with
  `aria-disabled="true"` and the title "No resort lists the Epic Pass yet."
* Resort panel and stub (`panel.js` `passesFact`, `routes.py` `render_passes`): a "Passes" fact under Owner, one line
  per membership, "Epic Pass: unlimited", "Ikon Pass: 7 days", "Ikon Pass: limited days" (no number stated) or "Ikon
  Pass: days not stated", with the confidence badge, the note, and "2026/27 season · No blackout dates · Last
  verified <date> · <source>". A previous season fallback names its list: "Epic Pass: unlimited (2025/26 list)",
  with "2025/26 season" in its meta line. The chips count fallbacks like any membership. No line for a resort
  without a membership. Pass names are proper nouns ("Epic Pass",
  "Ikon Pass", "Mountain Collective", "Indy Pass").
* Zod: `PassMembership` in `schemas.js`; a bad membership drops only itself. Tests: `tests/pipeline/test_passes.py`,
  `passChecks` in `tests/map/browser_test.mjs` (fixtures: Epic at Alpha Peak, Bravo Bowl (a 2025/26 fallback) and
  Mike Snow Dome, Ikon at Charlie Hill, Foxtrot Basin and Kilo Notch, all synthetic).

## Housing search (links only; ROADMAP item 8c; `pipeline/housing_search.py`, `map/js/housinglinks.js`)

Owner request 2026-10-14: a resort shows its Furnished Finder listings, the employee housing that is publicly listed,
and the apps and sites that work best for finding housing there. Decision: "best" means named by the most
independent sources. Furnished Finder and every tool are linked in a new tab (`target="_blank"`,
`rel="noopener noreferrer"`); listings are never copied, scraped or embedded.

### Raw input: `data/raw/housing_search/<batch>.yaml`
```
{ batch, researched_date, sources: [Source],
  furnished_finder: [{ resort_id, town, url, source_id, as_of, confidence, note? }],
  tools: [{ scope: cluster|resort, scope_id, name, kind, url, why, source_ids: [id, ...], as_of, confidence, note? }] }
kind: employer_portal | housing_authority | listing_site | facebook_group | craigslist | property_manager | app | other
```
Validated by `pipeline/schemas/housing_search.py` (`HousingSearchBatch`) and loaded by `load_housing_search`. Each
of these is a build error: a url that is not https; an unknown resort id (`furnished_finder.resort_id`, a resort
`scope_id`), cluster id (a cluster `scope_id`) or source id; a bad `kind`, `scope` or `confidence`; a Furnished
Finder url outside `housing_search.furnished_finder_domains` (furnishedfinder.com); a resort with two Furnished
Finder links (in one file or across files); one url listed twice for one scope; a source id listed twice in one
tool; an empty `source_ids`; an em or en dash in `town`, `name` or `why`. `why` is one public sentence (400
characters at most) from the cited sources.

### Ranking and build output
* `Resort.housing_search` (`resort_housing_search`): the resort's Furnished Finder link and its tools. Tools scoped
  to the resort come first, then tools scoped to its cluster; inside each group the tool named by more independent
  sources comes first, then by name (case blind). Independent sources are publishers: the distinct registered
  domains (last two labels) of the tool's source urls, so two Reddit threads or two pages of one town site count
  once. That count is `named_by`. A cluster tool whose url (host without "www.", path without a trailing slash,
  query kept) is already in the resort group is dropped. At most `housing_search.max_tools` (8) tools are kept.
  Every resort of a cluster, Tier B included, gets the cluster's tools. Null without a link and a tool.
* The link and tool sources join `resort.sources`, so stubs embed them. Size on 2026-10-14: 114 resorts, about
  140 KB more in `resorts.json` (about 5 percent); nothing reaches `map-index.json`.

### UI (`map/js/panel.js` Housing and commute, `map/js/housinglinks.js`; stub `pipeline/routes.py` `render_housing`)
* The resort panel's "Housing and commute" section holds, in order: "Getting to the mountain" (access), "Furnished
  rentals", "Employee housing", "Where people find housing here", then "Rent near the resort" (the reported rent
  and the county baseline, once the detail file is in).
* Furnished rentals: "Furnished Finder listings near <town>" with its badge, "furnishedfinder.com · opens in a new
  tab · Last verified <date>" and the note. Shown only with a sourced https url.
* Employee housing: the detail file's sites, employee housing first (resort owned, master lease, unspecified, then
  market) and by rent within a type. Each site button shows the type badge (Resort owned, Master lease, Listed by
  the resort, Market), the town and the precision note ("Listed address, shown as a pin" or "Neighborhood area, shown
  as a circle"); below it the site's badge with "Source: <listing page> · Location: <location source> · Last verified
  <date>" (the location source alone when there is no listing page, once when both are the same), the address, the
  rent only when the site has one (its own, a reports aggregate, or the county baseline estimate with
  its note), occupancy, deposit, eligibility and the commute table. "Show on the detail map" starts the WebGL map
  when needed (it returns to the map from the list view), switches the Housing layer on (its checkbox too), fits the
  map to the housing pins (`mapCtl.fitHousing`, `detailmap.housingBounds`, max zoom 14) and announces "Showing N
  housing sites on the detail map." with "Pins mark listed addresses.", "Circles mark neighborhood areas." or both,
  for the sites it shows. Phones drop the
  sheet to its peek and move focus to the map. Building pins are solid dots with a white ring; neighborhood areas
  are soft, blurred circles. Without sites: "No employee housing listed publicly yet."
* Where people find housing here: an ordered list in rank order; each tool has its link, a kind tag (Employer portal,
  Housing authority, Listing site, Facebook group, Craigslist, Property manager, App, Other), its badge, the sources'
  `why` sentence, the note, and "Named by N sources: <source links> · Last verified <date>" ("Named by 1 source
  across 2 pages" when one publisher has several pages). Tier A resorts without tools say "Housing search tools are
  being collected."; Tier B resorts without tools show nothing.
* `housinglinks.js` (about 1.6 KB gzipped, no Zod) loads on the first resort view, so `/map/` carries none of it.
  The section's small text (meta lines, notes, kind tags, type badges) is 12 px (`--text-xs`): at 11.5 px the long
  source lines of Jackson Hole's listed sites took Lighthouse's legible text share under 60 percent (Best Practices
  96). Lighthouse on 2026-10-14 (3 runs, real data with 114 resorts linked and 66 sites): `/map/` median 98 [96 to
  98], Vail 96 [96], Jackson Hole 96 [96], Accessibility, Best Practices and SEO 100.
* Prerendered stubs: a "Housing" section after Jobs with "Furnished rentals" (the link and its badge) and "Where
  people find housing here" (the tool names as plain links in rank order, with their kind). No section without
  links.
* Zod: `HousingSearch` in `schemas.js` (a bad tool drops only that tool, a bad link only the link; an unknown kind
  renders humanized) and `town` on `HousingSite`. Tests: `tests/pipeline/test_housing_search.py`,
  `tests/pipeline/test_routes_housing.py`, `housingChecks` in `tests/map/browser_test.mjs` (synthetic fixtures:
  Alpha Peak with a link and three tools, Bravo Bowl and Charlie Hill with the cluster's two tools).

## Trail map (OpenSkiMap runs)

### Raw input
* `https://tiles.openskimap.org/geojson/runs.geojson` (about 835 MB, one feature per line) downloads to
  `data/raw/openskimap/runs.full.geojson` (gitignored) with `python pipeline/build.py --fetch`. `pipeline/runs.py` streams
  it line by line, keeps runs whose `uses` include `downhill` or `snow_park`, whose `status` is not abandoned, disused,
  proposed, planned or construction, and whose `skiAreas[].properties.id` include an OpenSkiMap area matched to a Tier A
  resort (the same area ids the lifts use). Elevations are dropped, geometry is simplified with Douglas Peucker at
  `trailmap.simplify_tolerance_m` (6 m) and rounded to 5 decimals.
* Committed extract `data/raw/openskimap/us_runs_tier_a.geojson`, properties
  `{ id, name, difficulty, difficulty_convention, uses, grooming, lit, ski_area_ids }`. A newer committed extract is never
  overwritten by an older download.

### Build output: `data/build/trailmap/<resort_id>.json` (lazy loaded by the trail map popup)
```
{ version, generated_at, resort_id, source_id: "openskimap-runs",
  runs: FeatureCollection<LineString | MultiLineString | Polygon | MultiPolygon>,
        properties { name: string | null,
                     difficulty: novice | easy | intermediate | advanced | expert | freeride | extreme | null,
                     difficulty_us: green | blue | black | double_black | terrain_park | null,
                     grooming?: string, lit?: boolean },        // omitted when null (treat missing as null)
  lifts: FeatureCollection<LineString> props { name, lift_type, source_id: "openskimap-lifts" },   // same as detail files
  bounds: [west, south, east, north] }                           // over runs and lifts
```
* Written for every Tier A resort with at least one run; `resorts.json` Resort gains `trailmap_available: boolean`.
* Polygon runs are area runs (open bowls, glades, beginner areas); draw them as filled outlines.
* `difficulty` is OpenSkiMap's value as given. `difficulty_us` maps it to North American trail signs for the
  `north_america` difficulty convention, following the OSM `piste:difficulty` key that OpenSkiMap passes through
  (https://wiki.openstreetmap.org/wiki/Key:piste:difficulty, https://github.com/russellporter/openskidata-format):

  | OpenSkiMap `difficulty` | `difficulty_us` |
  |---|---|
  | novice, easy | green |
  | intermediate | blue |
  | advanced | black |
  | expert, extreme | double_black |
  | freeride | null (off piste, no sign colour) |
  | null | null |
  | any run whose `uses` include `snow_park` | terrain_park |

  Runs under another convention (europe, japan) get `difficulty_us: null`.
* Source `openskimap-runs`: OpenSkiMap runs, data © OpenStreetMap contributors (ODbL). The popup shows that attribution.

## Resort facts overlay

`data/raw/facts/<batch>.yaml` (format in `data/raw/facts/README.md`): `{ batch, researched_date, sources, records }`,
one record per resort (Tier A or Tier B) across all files, with optional Sourced `open_typical`, `close_typical`,
`training_start` ("MM-DD"), `length_days` (1 to 366), `annual_snowfall_in`, `acres`, `vertical_ft`, `lifts` (positive).
The build merges each field into `Resort.season`, `Resort.snow.annual_avg_in` and `Resort.size`: the higher confidence
wins (published > reported > estimated); on a tie the value already on the resort (Tier A YAML, or OpenSkiMap for lifts
and vertical) wins. `season.length_days` then drives `resorts.geojson` `season_length_days` and the lucrativeness season
months; `snow.annual_avg_in` drives `snowfall_annual_in` with `snowfall_annual_source: published` (the published path,
whatever the value's confidence).

## Phase 6 editorial

### Raw input: `data/raw/editorial/<batch>.yaml`
```
{ batch, researched_date, sources: [Source], entries: [EditorialEntry] }
EditorialEntry { target: "cluster" | "resort", id,            // an existing cluster id or resort id
                 overview_md, culture_md (resort only, else null), alexandre_take_md (written by Alexandre only),
                 status: "draft" | "reviewed", source_ids: string[] (1 or more, all defined),
                 reviewed_by?: string, reviewed_date?: ISO date }   // set by the approve command
```
* One entry per (target, id) across all files. A `reviewed_by` or `reviewed_date` on a draft is an error.
* Style lint on every text field (Alexandre's rules): an em dash or en dash fails the build; a hyphen inside a word
  ("full-time") and negative definitions ("is not", "isn't", "are not", "aren't") are warnings listed in
  `docs/map/editorial-review.md`. URLs and Markdown link targets are ignored by the lint.

### Build output
* Only `reviewed` entries reach `/data/build`. Drafts never do.
* `Resort.editorial`: `{ overview_md, culture_md, alexandre_take_md, status: "reviewed", source_ids, reviewed_date }`
  or `null`. Its `source_ids` are also in `resort.sources`.
* `clusters.geojson` properties `overview_md` and `alexandre_take_md` come from the reviewed cluster entry (else null);
  its `source_ids` are added to the cluster `sources`.
* `docs/map/editorial-review.md` (generated each build) lists every draft with its text, source links and lint warnings,
  plus the Phase 6 gate: Tier A clusters with and without a reviewed overview.
* Mark an entry reviewed by setting `status: reviewed` in the YAML, or
  `python -m pipeline.editorial approve <cluster|resort> <id> [--by NAME]` (or `approve-all --by NAME`), which flips the status in place and records
  `reviewed_by` and `reviewed_date`.

## Phase 7 crowdsourcing and freshness

### Reports (raw, never published)
`data/raw/reports/queue/<id>.yaml` (pending) and `data/raw/reports/moderated/<id>.yaml` (approved or rejected), one
report per file. Both folders are gitignored: individual reports never reach git or `/data/build`.
```
Report { id, resort_id, kind: pay | rent | housing, season ("2025-26"),
         cert_level (pay only, same values as PayRecord), housing_site_id (housing only),
         values: { hourly_usd } (pay) | { monthly_usd } (rent, housing),
         proof_url?: string, submitted_at: ISO datetime,
         status: pending | approved | rejected, moderation_note?: string, moderated_at?: ISO datetime }
```
* `rent` is a rent the reporter paid near the resort; `housing` is a rent at a listed housing site.
* Moderation: `python -m pipeline.reports list`, `approve <id> [--note TEXT]`, `reject <id> --note TEXT`,
  `import <file.json|file.csv>` (see `data/raw/reports/README.md` for the columns), `aggregate`.

### Aggregation (`python -m pipeline.reports aggregate [--replace]`)
* Group approved reports by (resort_id, season, kind, cert_level for pay, housing_site_id for housing).
  A group with `reports.aggregation_min` (3) or more reports gives a range `{low: min, high: max, count}`.
  Groups with 2 or fewer give nothing. Pending and rejected reports never count.
* Only the `aggregate` command writes `data/raw/reports/aggregates.json`, and it refuses while any moderated report is
  invalid. It merges with the committed file, keeping the group with more reports, because a checkout may hold only some
  of the (gitignored) moderated reports; `--replace` rebuilds the file from this checkout alone. The build reads the
  committed file merged in memory with this checkout's approved reports and never writes it.
* Loading drops any aggregate with fewer than `reports.aggregation_min` reports (so raising the minimum hides older
  small groups). Housing aggregates attach by (resort_id, housing_site_id); an approved housing report whose site is not
  a site of its resort fails the build. Only approved reports are checked against the build's resort ids.
* Output `data/raw/reports/aggregates.json` (committed; aggregates only, no individual values):
  `{ version, generated_at, source: Source, pay: [PayRecord], rent: [RentAggregate], housing: [RentAggregate] }`
  where pay rows are `confidence: reported` with `aggregate_count`, and
  `RentAggregate { resort_id, season, housing_site_id (housing only), low, high, count, verified_date }`.
  Source id `instructorgrindset-reports`.
* Pay: aggregates join the Phase 4 pay selection (latest season, then Published > Reported > Estimated).
* Rent, per field with Published > Reported > Estimated:
  housing site `rent_monthly` = the best of the site's own rent and its latest season `housing` aggregate by
  confidence (a published site rent beats the aggregate; the aggregate, `reported` with the count in `note`, beats an
  estimated site rent), else the county baseline (`estimated`). A raw housing file may not declare a `reported` rent.
  Detail files gain `rent_reported: Sourced<{low, high}> | null` (latest season `rent` aggregate for the resort,
  `note` gives the count). The area `rent_reported` is shown in detail files; it is not in the default lucrativeness
  rent order (owner decision 2026-10-14), which uses the median of the sourced median rents, then Zillow ZORI, then ACS, then FMR.

### Staleness (`python -m pipeline.staleness`)
Writes `docs/map/staleness.md`: every sourced value in `resorts.json` and `detail/*.json` whose `as_of` or
`verified_date` is older than `staleness.max_age_months`, grouped by resort and field. Phase 4 already flags
`career.stale_fields` and `pay[].stale` in the build output.

### Submissions endpoint
`config/definitions.yaml` `submissions: { endpoint }` is exposed in `definitions.json`. Owner decision 11: the endpoint is
the site's existing Formspree form `https://formspree.io/f/xyzbdqgq` (the one /contact uses; it emails the owner). The map
form POSTs JSON with `Accept: application/json`, a `_subject` such as "Career Map report: <resort name>", and flat fields
`resort_id, kind, season, cert_level, housing_site_id, hourly_usd, monthly_usd, proof_url`. `python -m pipeline.reports
import` reads Formspree's CSV export (see `data/raw/reports/README.md`).

## Frontend (`/map`)

### Load order (Phase 8 performance)
1. The page head preloads `map-index.json`, `clusters.geojson` and `states.json`. The map control CSS loads without
   blocking the paint. `/map/index.html` carries the national heading and intro as its prerendered block, so the
   first paint already shows real text. The modules on this path
   (`app`, `config`, `data`, `router`, `panel`, `livelinks`, `ui`, `labels`, `listview`, `sheet`, `meta`, `preview`, `camera`, `controls`) carry no
   Zod and no charts. The first paint files are checked by hand in `data.js`, mirroring `schemas.js`. `app.js` runs
   its startup as three short tasks (index the data, render the panel, draw the picture) so no single task blocks
   the page for long.
2. The panel renders from the slim index; the list view builds the first time it opens. `preview.js` draws the first
   map frame as an inline SVG (clusters and resort dots, Alaska at its true place, with the WebGL map's framing and colors)
   with a "Click to explore the map" button ("Tap to explore the map" on touch screens). It is a picture only.
3. Owner decision 9 (2026-10-13): the WebGL map starts on the first click or tap over the picture, or Enter or Space
   on the button. Then MapLibre runs (it was preloaded), `map.js` builds the map (workers prewarmed first), the
   picture and button go as soon as the data layers have drawn (the style plus the states, clusters and resorts
   sources; `mapCtl.whenReady`, mark `careermap:map`), and focus moves to the map canvas. MapLibre's own `load` also
   waits for every imagery tile, so it never gates readiness: tiles that hang or fail leave the plain dark base. Every view waits for the
   click, deep links included; in app navigation before the click redraws the picture for the new view.
4. Background work waits for the window `load` event and the first contentful paint, then for idle time: `map.js`
   and a preload of `maplibre-gl.js` (271 KB gzipped); `sources.json` and `definitions.json` (`index.loadSources()`)
   when the panel shows a source link it cannot resolve yet; and a link prefetch of `resorts.json` and `sources.json`,
   which only fills the HTTP cache. `resorts.json` (2.6 MB, 250 KB gzipped) is parsed only when a view needs full
   records (`index.loadDetails()`: a resort without an embedded record, a comparison, the trail map) or once the WebGL
   map is ready. After the WebGL map is ready: the color mode control (`recolor.js`, `heat.js`), then
   `us-states.geojson`. Source links rendered earlier show a placeholder that fills in when `sources.json` lands.
   The basemap underlay (`underlay.js`: `underlay.json`, then the images the picture needs, decoded off the main
   thread: the 41 KB quarter size copies at the national view) starts once the MapLibre preload has finished, so a
   quick click keeps the bandwidth. Resort pictures skip it; a click before it loads brings it with the WebGL map.
* Full resort records are validated with Zod one at a time, the first time a view needs them (`index.full(id)`).
  A resort page validates the record its stub embeds, so it needs Zod and `definitions.json` but no `resorts.json`.
  While that loads, the stub's prerendered summary stays on screen (`aria-busy` on the panel).
* Lazy modules: `hiring.js` (the Hiring now count, at idle), `schemas.js` and Zod, `charts.js` and Chart.js (climate charts only; on a resort page Chart.js loads
  when the climate section nears the screen), `detailcontrols.js` (resort
  detail map only), `housinglinks.js` (the first resort view), `recolor.js` and `heat.js`. Map pages load no web font:
  they use the system monospace stack (see "Console look"). The snowfall
  rasters and isolines (`heat/snowfall.json`, its PNGs and GeoJSON, about 400 KB, 300 KB gzipped) load only when the
  Snowfall mode is selected, and the season rasters (`heat/season.json` and its PNGs, about 165 KB) only with Season length.
  `jobs/postings.json` loads once, after the first paint at idle for the Hiring now count (when some resort has
  `job_employers`), and Zod validates it only for a resort whose `jobs.feed` is true or a cluster with `jobs_available`
  resorts; `livelinks.js` (about 3 KB gzipped, no Zod) loads with `panel.js`. `search.js` loads on the first focus of
  the search box.
* **Budget method** (`tests/map/audit.mjs`): Playwright with Chromium under SwiftShader, CDP network throttling
  (150 ms RTT, 1.6 Mbps down, 750 Kbps up), 4x CPU slowdown, cache off, files gzipped. Times are the page's own
  performance marks from navigation start. *Interactive* is the later of two marks: the panel usable
  (`careermap:panel`, or `careermap:panel-full` when a resort record had to load) and the first map frame
  (`careermap:preview`, the 2D picture). The audit then clicks the start button and reports click to WebGL first frame
  (`careermap:first-render`) and `load` (`careermap:map`). Measured 2026-10-13 at a load average of 5: interactive
  1.7 to 1.8 s on `/map/` and the Vail page (desktop and phone); click to WebGL load 6.1 to 6.9 s when the click comes
  right after the first paint, because MapLibre (271 KB) only starts downloading after the window `load` event and the
  first paint. Under SwiftShader the WebGL setup alone takes 1.5 to 2.5 s.
* Lighthouse (mobile defaults, simulated throttling, same SwiftShader Chromium): see "Performance budget" below for
  the current numbers. Accessibility, Best Practices and SEO 100. Before the click to start (owner decision 9) and
  the deferred MapLibre download, Performance was about 65, held down by 5 to 7 s of blocking time from MapLibre.
* Basemap underlay (`config.basemap.underlay`, `map/js/underlay.js`, owner report 2026-10-14 "where is my united
  states satellite underlay?"): the USGS tiles load only after the click, so the 2D picture before it had no imagery
  at all, and they come from an outside host, which a host's content security policy (such as the private claude.ai
  preview) can block: MapLibre fetches tiles, so under `connect-src 'self'` every one is refused (checked with the
  preview bundle on a subpath). The underlay is served with the site, so it always shows. Source: Natural Earth I with Shaded
  Relief, Water and Drainages, 1:10m large size (`NE1_HR_LC_SR_W`, 1 arc minute, public domain; source record
  `natural-earth-ne1-hr-lc-sr-w` in `data/raw/sources/basemap.yaml`).
  `pipeline/tools/make_underlay.py` is a one time asset script (the only code that needs Pillow, kept out of
  `requirements.txt`; its 323 MB download goes to the ignored `data/raw/basemap/download/`). It crops two boxes,
  resamples them to the `pipeline/heat.py` grid (columns even in longitude, rows even in Web Mercator y, area
  filtered), mutes them with MapLibre's raster math (saturation -0.3, contrast 0.05, brightness max 0.55) plus a gamma
  of 1.4 for a darker satellite look, fades the edges into the map background and writes WebP:
  * `images/basemap/underlay-lower48.webp`: -131 to -61, 17 to 55 (past the national view, so the fade stays off the
    states), 58 px per degree, 4060 x 2835, 363 KB.
  * `images/basemap/underlay-alaska.webp`: -180 to -128, 49.5 to 73, 36 px per degree, 1872 x 1864, 117 KB. It holds
    the heat maps' Alaska region (-180 to -129.5, 51.2 to 71.6) with room for a 1.5 degree fade that stays off the
    Arctic coast, the Aleutians and the panhandle. Its edges fade to transparent (RGBA, `"fade": "alpha"` in the
    sidecar), so where it overlaps the lower 48 image off British Columbia the two blend with no seam; the lower 48
    image fades into the map background. `--only alaska` regenerates it alone and keeps the lower 48 entry.
  * `underlay-lower48-small.webp` (1015 x 709, 29 KB) and `underlay-alaska-small.webp` (468 x 466, 12 KB): quarter
    size copies, same corners, for national zooms.
  * `images/basemap/underlay.json`: corners (MapLibre order), sizes, hashes, the tone and the source.
  Each image stays within 4096 px a side, since MapLibre uploads an image source as one WebGL texture and many phones
  cap textures there. `tests/pipeline/test_underlay.py` checks the math against the shader, the sidecar against the
  images, the boxes against `nationalBounds` and the heat regions, and the credits.
* WebGL map: image sources `underlay-lower48-small` and `underlay-alaska-small` are the first layers above the
  background, at every zoom, each at its true place (Alaska above the lower 48). The full images (`underlay-lower48`,
  `underlay-alaska`) join above the small ones once the map reaches zoom 4.5, so a phone that stays national never
  decodes or uploads them (46 MB as a texture). The tone is baked in, so the layers use a neutral paint. When the
  picture has loaded the underlay before the click (the usual case) the small copies are part of the first style, so
  the switch from the picture shows no gap. Else (a quick click, or a resort page) they join once the data layers are
  drawn, so their decode and texture upload never hold the first frame back. The underlay never gates readiness.
* 2D picture: `addPreviewUnderlay` draws each image as an SVG `feImage` on a rect clipped to the view, in the
  picture's Web Mercator framing, under the clusters and dots, each at its true place as on the WebGL map (an image off
  the picture is skipped; at the national view Alaska's shows only as an edge in the upper left). An SVG `<image>` (directly, in a pattern, or faded in from opacity 0) is a Largest Contentful Paint candidate and
  would move LCP to the late underlay paint; a filter primitive is never one (checked in Chromium 141), so LCP stays
  on the intro text. Each image is the smallest copy that covers its size on screen (the quarter size copy up to 2x
  upscaled), so the national picture draws the 41 KB copies and a state picture the full images. There is no fade:
  each frame of one would raster the filter again. Inserting it costs no main thread time (a 4 ms timer heartbeat
  stays under 8 ms on a 390 px phone at 2x); under SwiftShader one compositor frame takes about 120 ms. The picture
  has no state outlines, matching the WebGL map until `us-states.geojson` arrives.
  The picture draws the underlay up to zoom 9 (national, state and cluster views). A resort picture skips it and its
  download: at 58 px per degree it would be upscaled about 30 times there, and the USGS imagery takes over on the
  WebGL map. Measured 2026-10-14 (3 Lighthouse runs interleaved with the tree before, load 0.6 to 2.5): `/map/`
  98, 98, 98 (before 98, 98, 98), Vail 96, 96, 96 (before 97, 96, 96), LCP 2.12 s and 2.45 s (before 2.11 s and
  2.45 s), the intro text in both, TBT 0. At a load of 3 to 10 the medians were 96 and 95 (before 97 and 96).
  Throttled click to WebGL load: 5.4 to 5.7 s (before 5.8 s).
* Satellite imagery (`config.basemap.imagery`): USGS The National Map `USGSImageryOnly` tiles (public domain, 256 px,
  max zoom 16) over the underlay from zoom 6.5 (layer and source `minzoom`), faded in by 7.5 (`raster-opacity`), so
  national and state views request no tile and cluster and resort views get the detail. Where tiles fail or are
  blocked, the underlay shows through. Muted (`raster-saturation` -0.35, `raster-brightness-max` 0.7,
  `raster-contrast` -0.1). State fills turn translucent over both (`basemap.state_fill_opacity`, zoom stops: 30% to
  zoom 6.5, 55% from 7.5) so the underlay reads at national and state zooms while outlines, heat colors and markers
  stay readable. The MAP / SATELLITE toggle sets the raster paint of the underlay and the imagery (see "Console
  look"); heat rasters draw above the fills. Attribution: "Relief: Natural Earth" is in `basemap.attribution`; "Imagery: USGS The National Map" comes from
  the source, so MapLibre lists it only from the imagery's minzoom. The tiles load only after the click to start, so
  the first load and Lighthouse never fetch them. The tests answer tile requests with a synthetic local tile
  (`tests/map/fixtures/imagery-tile.png`), since the sandbox cannot reach the USGS service; the underlay checks in
  `tests/map/browser_test.mjs` make every tile fail instead.

### Performance budget
* **Target** (Phase 8, queue item 18a): Lighthouse Performance (mobile defaults) with a median of 95 or more over 5
  runs on `/map/` and no run under 90; resort and cluster pages 90 or more.
* **Method:** the gzip server and SwiftShader Chromium of `tests/map/audit.mjs`, 5 runs per page, median and worst
  run. Check the load average first: on this 4 core machine other jobs above a load of 2 move scores by 10 or more.
* **How Lighthouse scores these pages** (simulated throttling): it rebuilds the load from the observed trace. Every
  request that ended before the observed first paint counts as needed for FCP and LCP. Total Blocking Time (30% of
  the score) takes every main thread task after FCP, multiplied by 4 (by 2 when the task did layout), and counts the
  part of each task above 50 ms. So nothing optional may start before the first paint, and a task after it should
  stay under about 12 ms (25 ms with layout).
* **Causes of the old variance** (2026-10-13: 58 to 99 on `/map/`, 56 to 97 on Vail):
  1. Under SwiftShader the first frame waits 0.5 to 2.5 s on the GPU process, long after the page is ready. Loads
     that started at the window `load` event (`resorts.json`, `sources.json`, `maplibre-gl.js`, the web font style
     sheet, about 600 KB) often finished before that paint, and Lighthouse then put LCP at 5.6 to 6.1 s instead of 2.0 s.
  2. The first draw on a 2D canvas waits for a GPU channel while the GPU process is busy with that first frame: 100 to
     360 ms inside the startup task, the largest share of blocking time.
  3. `resorts.json` (2.6 MB) was fetched and parsed at idle: tasks of 70 to 300 ms.
  4. The hidden list view (about 2,000 nodes) and a forced layout lengthened the startup task.
* **Changes:**
  1. Background work waits for the window `load` event and the first contentful paint (`whenPainted` in `data.js`,
     the font loader in the page head). Job postings (`loadPostings`, which bring Zod onto cluster pages) and the
     climate charts wait for the first paint too. The chart figures and the data table render then; Chart.js loads
     and draws the charts, one per task, only when the climate section comes within 600 px of the panel's visible
     part (`nearView` in `charts.js`, an IntersectionObserver on `.panel-scroll`).
  2. The first map frame is an inline SVG (`preview.js`); the compositor draws it off the main thread.
  3. Startup runs as three short tasks. The list view builds the first time it opens. The first render skips a
     forced layout.
  4. Idle time only prefetches `resorts.json`. `sources.json` loads when the panel shows a source link. Full records
     load when a view needs them or once the WebGL map is ready.
* **Result** (5 interleaved runs per page against a copy of the tree before the change, load average 1.3 to 4):

  | Page | Before: median [range] | After: median [range] | LCP before, after | TBT before, after |
  |---|---|---|---|---|
  | `/map/` | 92 [91 to 97] | 98 [98 to 99] | 2.00 s, 1.97 s | 293 ms, 10 ms |
  | Vail | 90 [68 to 93] | 97 [96 to 97] | 2.72 s, 2.45 s | 205 ms, 28 ms |
  | I 70 Corridor (cluster) | 92 [90 to 95] | 98 [98 to 99] | 2.70 s, 1.97 s | 184 ms, 41 ms |
  | Aspen Mountain (climate charts) | 78 [71 to 79] | 97 [96 to 97] | 3.22 s, 2.28 s | 501 ms, 66 ms |
  | Alta (climate charts) | 92 [85 to 94] | 97 [95 to 97] | 2.30 s, 2.29 s | 259 ms, 59 ms |

  The Aspen and Alta "after" runs include the charts drawn on scroll (5 more interleaved runs, load 0.7 to 3.7;
  Alta's "before" is the tree with every other change, where charts drew right after the first paint: Aspen was 90
  [75 to 95] there).

  Under a load of 9 to 14 the gap widens: `/map/` 67 before, 98 after (95 to 98). CLS stays at 0.003.
* **What is left** on resort pages: the stub's record and `detail/<id>.json` are validated with Zod during the first
  render (`detail/vail.json` is 166 KB, mostly transit), and the web font swap relays out the long panel (30 to
  60 ms). On the 11 resorts with climate charts, Chart.js (69 KB), its first canvas (about 130 ms) and the chart
  animations now cost their time when the reader scrolls to the section.

### Console look (ROADMAP item 21; `map/css/map.css`, `map/js/controls.js`, `map/js/app.js`, `map/js/map.js`)
World Monitor (worldmonitor.app, AGPL 3.0) is a visual reference only: no code, CSS rules, icons or images are copied;
only plain color values are reused. Every rule is scoped to map pages (`body.map-page`, and `body.credits-page` on
`/map/credits/`, which links `map.css` for its tokens); `/assets/global.css` and `/assets/sidebar.css` are unchanged,
and the site nav (`/assets/nav.js`) keeps working with a squared hamburger.
* Tokens (`map.css`): background `#0a0a0a`, secondary `#111`, surface `#141414`, hover `#1e1e1e`, border `#2a2a2a`,
  strong border `#444`, text `#e8e8e8`, secondary text `#ccc`, dim `#959595` (5.2:1 or more on every surface, so it can
  carry small text), green `#44ff88` for active states and focus rings, amber `#ffaa00` (Tier A, Estimated), blue
  `#3388ff` (Tier B; `#6aa6ff` for Reported text). One font for everything: 'SF Mono', Monaco, 'Cascadia Code', 'Fira
  Code', 'DejaVu Sans Mono', 'Liberation Mono', monospace. Uppercase letter spaced labels for headings, chips, badges
  and buttons; square 1px bordered controls (radius 2 to 3 px). The Inter loader is gone from map pages.
* Header bar (`.mh-bar`): the wordmark (the page `h1`, uppercase by CSS), the region jump menu, the HIRING NOW pill,
  the LIVE indicator, the search box, Link and List view. The region menu (`createRegionMenu`) is a disclosure button
  with real links to National and every state with resorts (built on first open; Down opens it on the current
  region, Up and Down move, Escape closes it). LIVE shows only when `jobs/postings.json` has a real `fetched_at`
  (`showFeedTime`): "Live jobs 15 Oct 17:19 UTC", read as "Live jobs feed, updated ...". Link copies the current URL
  (Clipboard API, then a textarea fallback) and says "Link to this view copied." in the polite status region. Mid
  widths drop the LIVE time, then the pill, then LIVE.
* Sub bar (`.mh-sub`): the view kind and name ("National view · United States", "Resort · Vail"), today's date
  ("Fri, 16 Oct 2026", the viewer's clock) and the MAP / SATELLITE toggle (two `aria-pressed` buttons).
* Basemap tone (`config.basemap.tone`): MAP (the default) darkens the Natural Earth underlay and the USGS imagery into
  a near black monochrome with MapLibre raster paint: underlay saturation -1, contrast 0.5, brightness 0 to 0.75
  (water and the image's faded edges fall to black, the base color, so the image edges melt into it; land keeps its
  relief between about `#141414` and `#2a2a2a`), imagery saturation -1, contrast 0.2, brightness 0 to 0.38. SATELLITE restores the earlier
  full color paint and base `#0a0e14`. `mapCtl.setTone` repaints the background, every `underlay-*` layer and the
  imagery; the 2D picture applies the same math as one SVG `feColorMatrix` after its `feImage` (`preview.js`
  `toneMatrix`, checked against the shader in the browser test). The choice is kept in `localStorage`
  (`careermap:basemap`, every read and write in try/catch; blocked storage falls back to MAP). State lines are thin
  teal (`#1f6b58`), the selected state green.
* Layers panel (`#layers-panel`, 232 px, `config.layersPanel` and `--lp-w`): one floating panel at the top left of the
  map. COLOR BY (a segmented row of the four modes, green when active; after the WebGL map is ready), then LAYERS (a
  `details` box holding the filters: the Type radio group and the Show only checkboxes, native inputs styled round and
  square), then, on resort views with detail data, RESORT DETAIL LAYERS in the same style. From a 1000 px stage the
  map's left padding keeps the panel beside the map (the national view fits between it and the side panel); narrower
  desktops fold LAYERS and start the map below the folded panel. List view keeps the panel (the list is padded to
  clear it); Color by and the detail layers hide there.
* Markers (`map.js`): `resorts-glow`, a fully blurred circle in the tier color under every dot (ski areas and
  facilities, filtered like them, hidden in heat modes); the dots keep their hit targets and keyboard access.
  `resorts-hiring` draws a green ring around each resort hiring now; a MapLibre marker per ring carries a CSS pulse
  (`.hiring-pulse-ring`, transform and opacity only), hidden from assistive technology and the pointer, kept to the
  resorts the filters and the focused state or cluster show, and never created under `prefers-reduced-motion`.
* Legend strip (`#map-legend`, static in the template): LEGEND with Tier A, Tier B, Indoor, Training center, Cluster
  and Hiring now, centered along the bottom of the open map area; on phones it sits at the top of the map and
  scrolls sideways (focusable, so a keyboard can scroll it). The heat legend card stays for heat modes.
* Controls: one column on the right edge of the open map area (`top-right`, offset by the side panel): zoom in, zoom
  out, home (back to the national view; at the national view it refits a panned map), the trail map button on resort
  views, then the compact attribution. All buttons are 32 px squares. On phones the column sits at the bottom right
  of the visible map, just above the sheet (`--sheet-visible`, set by `sheet.js` when the sheet snaps), clear of New
  England at the national view.
* Phones (under 760 px): the header keeps the wordmark (hidden under 380 px), the search box and LAYERS; List view
  moves to the sub bar, and the region menu and Link move to the top of the layers sheet (`placeForViewport` moves
  the same elements, so ids and handlers stay). LAYERS opens the layers panel as a sheet along the bottom (78% high at
  most, above the panel sheet), with a Close button; Escape closes it and focus returns to LAYERS; picking a region
  closes it. Nothing overflows at 320 px.
* Cost: `controls.js` (about 2 KB gzipped) joins the startup modules (preloaded). The picture adds one blur filter
  on its dot group and one color matrix per underlay image; neither is a Largest Contentful Paint candidate.

### Color modes: heat maps
Lucrativeness, Season length and Snowfall draw a heat map that fills the map: the lower 48 clipped to the state
outlines, and Alaska at its true place. Lucrativeness turns on by itself as soon as any
resort has a computed `lucrativeness_mid` (pay, owner decision 2026-10-14). Values come from the slim index
(`lucrativeness_mid`, `season_length_days`, `snowfall_annual_in`). Every
setting lives in `config/definitions.yaml` `heat` and reaches the UI through `definitions.json`.
* **Classes** (`heat.modes.<mode>`, `scale: classed`): Snowfall and Season length use hard edged classes, so a step of
  one class is easy to read. Snowfall (owner request 2026-10-13: the most contrast in 180 to 900 in, a 50 in step easy
  to read, grey for little or no snow): under 24 in is the neutral grey `#666a70` (3.5:1 on the dark base), 24 to 180 in
  three muted blues, then 15 classes from 180 to 200, 200 to 250 and every 50 in to 850 and up. Their hues sweep cyan,
  green, yellow, orange, pink and lavender while the lightness alternates between neighbours, so every neighbouring pair
  from 180 in differs by 11 or more in OKLab (x 100), also under simulated protanopia and deuteranopia, and every class
  from 180 in keeps 5:1 or more against the dark base `#0a0e14`; inside the lower 48 the raster is opaque, so the muted
  imagery never mixes into the colors. Season length: classes at 90, 100, 110, 120, 130, 150 and 180 days, multi hue
  with rising lightness up to 180 and orange above. The tests check these numbers (`tests/pipeline/test_heat.py`).
* **Ramp** (`heat.ramp`): viridis, its upper 80 percent (`#414487` to `#fde725`, nine stops), for modes without classes
  (Lucrativeness). OKLab lightness rises at every stop (config validation fails otherwise).
* **Field** (`heat.field`, `map/js/heat.js` and its Python twin `pipeline/heat.py`): adaptive modified Shepard
  interpolation. Resorts closer than `decluster_km` (20) merge first (mean place, mean value), so a cluster of hills
  counts once and twelve resorts of one value give the same color as one. At a point, take the `neighbors` (8) nearest
  and the distance R to the next one: w = ((R − d) / (R × max(d, `min_distance_km`)))², value = Σ w v / Σ w. A resort's
  weight reaches zero just as it leaves the set, so the surface is continuous everywhere and blends between resorts with
  no gaps. Distances use km per degree at the pixel latitude. The field is solved on every `grid_step_px` (4) pixel and
  blended bilinearly between, rows evenly spaced in Web Mercator.
* **Opacity and fade**: from the distance to the nearest point with data: `max_alpha` (1) within `full_opacity_km`, then
  a smooth fade to `floor_alpha` at `fade_end_km`; with `fade_to: neutral` the color blends into `heat.neutral` by the
  same share. Season length uses `heat.season` (250 km, 450 km, 0.6, neutral), so far from resorts the map turns flat
  grey instead of a dark smudge, and states with no resort data stay clear beyond `no_data_reach_km` (120 km) past their
  border (Kansas, Nebraska, Oklahoma, Texas and the Deep South). Lucrativeness uses `heat.field`. Density never raises
  opacity or value.
* **Snowfall** draws the precomputed rasters (`heat/snowfall.json`, see Build outputs), built from two station sets and
  the terrain (ROADMAP item 14):
  * Stations: the NOAA NCEI U.S. Climate Normals 1991 to 2020 (`ANN-SNOW-NORMAL`, 5,568 usable stations, mostly in
    valleys and towns) and the NRCS SNOTEL mountain stations (831 of 873 with 8 or more complete water years; each
    station's median water year total 2011 to 2025 of derived new snow, `data/raw/climate/snotel_snowfall/`).
    The SNOTEL figure is checked against published resort snowfall and against nearby NOAA stations in
    `docs/map/climate-validation.md` (median 28 percent under the resorts' own figures, 4 percent under the
    measured NOAA normals nearby).
  * Elevation aware field (`heat.snowfall.elevation`, `gids_fit` and `solve_elevation_field`): gradient plus inverse
    distance after Nalder and Wein (1998). At each node (every `grid_step_px` pixel) the `neighbors` (10) nearest
    stations give the adaptive modified Shepard weights; the local gradient of u = ln(v + `log_offset_in`) with height
    is the slope of a weighted least squares fit (square root weights), shrunk by `spread_prior_m` (150 m) when the
    neighbours span little height and clamped to 0 .. `max_gradient_per_km` (2 per km); every station's value is moved
    to the pixel's height along that gradient and the moved values are averaged, v = exp(a + beta z) − c. With no
    gradient (flat country, or neighbours at one height) this is the plain Shepard value. The six node parameters
    blend bilinearly to every pixel, which reads the model at its own ground height: the terrain extract
    (`data/raw/terrain/`, 1/16 degree mean heights) after `terrain_smooth_cells` (2) box blurs, bilinear to the pixel
    center. The height is clamped to the neighbours' height range (`max_extrapolation_m` 0) and the value to their
    value range, so the field never goes above the highest or below the lowest station near it. Stations take the
    smoothed ground height at their own place, so each station reads back its own value and the gradient compares
    like with like. Without the terrain extract the field is plain station interpolation (`solve_field`).
  * Resorts: with `resort_points` each resort's published `snow.annual_avg_in` joins the stations at its ground
    height, so its snow follows the terrain instead of a ring; the field with the resorts is kept only where it is
    higher than the stations' field (`raise_field`). Then a pocket within `pocket_radius_km` (10 km; 25 km before item
    14) lifts the resort's own pixels to its figure: v = b + g(d) × max(0, T − b), g(d) = (1 − (d / r)²)², the
    highest pocket wins. T is the resort's value at or above its ground height and, with `pocket_terrain`, below it
    (value + c) exp(beta (z − z_resort)) − c, so a pocket fills the slopes and fades into the valleys. Neither step
    ever lowers the field (tests in `tests/pipeline/test_heat.py`).
  * Its attribution "Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL" (or "Snowfall normals: NOAA NCEI 1991 to
    2020" without SNOTEL stations) joins the map attribution control while the mode is on (an empty GeoJSON source
    carries it, since image sources have no attribution). The terrain source is credited on /map/credits/. ERA5 and
    Open Meteo are never used for this background (their mountain undercount is in `docs/map/climate-validation.md`).
* **Isolines** (Snowfall only, `heat/snowfall-isolines-<region>.geojson`, loaded with the mode after its manifest; the
  rasters draw without waiting): one GeoJSON source `heat-iso` with three layers over the heat surface and under the
  state lines (labels under the resorts): `heat-iso-minor` (thin, from zoom 4.5), `heat-iso-major` (every 100 in, bolder,
  from the national view) in the dark base color, and `heat-iso-label` ("300 in" images placed along the lines, upright,
  from zoom 6.5, mountain range zoom). Labels are canvas drawn images (`map.addImage`), since the style has no glyph
  server. Alaska's lines draw at its true place with the rest.
* **Season length** draws the precomputed rasters (`heat/season.json`) of the resorts' sourced season lengths; it has
  no NOAA credit, since every value is a resort's own sourced fact.
* **Lucrativeness** draws the precomputed rasters (`heat/lucrativeness.json`) of the resorts' computed mids, on the
  `heat.ramp` with the legend ticks of `legendScale`; without the manifest the browser draws the same surface.
* **Browser fallback**: when a manifest is absent or invalid, `buildHeat` in `map/js/heat.js` draws the same classes
  from the slim index in the browser (the Season length fade and the no data state rule included, with a node chamfer
  distance like the pipeline), at `heat.field.px_per_degree` (12). Measured 2026-10-13 under SwiftShader on a loaded
  4 core sandbox, real data, click to surface ready: precomputed season rasters 39 to 61 ms (109 to 383 ms at 4x CPU
  slowdown) against 215 to 387 ms (0.9 to 2.7 s) for the old browser path and 0.5 to 0.8 s (1.2 to 1.6 s) for the new
  fallback. Click to drawn layer: 0.7 to 2.1 s (1.7 to 2.9 s) against 2.0 to 4.0 s (2.5 to 3.4 s) before; the first
  WebGL frame is SwiftShader bound.
* Interpolated values never show as numbers. Tooltips and the panel show only the resorts' sourced values; resorts with
  a null value show as grey dots on top of the surface and add nothing to it.
* **Legend** (a card at the bottom of the left column, under the layers panel, on wide screens; bottom right of the map
  on narrow desktops; under the legend strip on phones): title and units, the bar,
  numeric ticks placed on it, "Resort values: min to max" (sourced values; only for modes without a fixed domain), the
  dot key and the resort count, then "About these colors" (folded, so the card stays short; a click opens it). Classed bars are
  hard edged class boxes (`classBoxes` in `heat.js`): each box is as wide as its class spans in value; open end classes
  take one typical class span (the most common span between breaks), or reach the fixed domain; classes that end at or
  below `legend_compress_below` take half a typical class each. Snowfall: 0 to 180 in takes 12 percent of the bar and
  180 to 900 in 88 percent; ticks 0, 180, 300 and every 100 to 800. Season length: ticks 90, 110, 130, 150 and 180.
  Snowfall adds "Grey: under 24 in, little or no snow" and "Lines every 50 in from 200, bolder every 100". Notes:
  "Colors between resorts are smoothed estimates. Tooltips and resort pages show only sourced values." plus the fade
  ("Colors turn grey where the nearest resort with data is more than 250 km away." and "States with no resort data stay
  clear." for Season length), or for the snowfall rasters "Background: NOAA station normals, 1991 to 2020, and NRCS
  SNOTEL mountain stations, shaped by the terrain, with a 10 km pocket of each resort's published snowfall." (the
  SNOTEL and terrain parts only when the manifest has `mountain_stations` and `terrain`; the radius from `pockets`)
  and the same smoothing note for stations and resorts.
* Images are MapLibre image sources with linear resampling under the state lines, cluster outlines and resort dots;
  cluster tints are off in these modes. Each mode's surface is computed or fetched once (cached).
* A mode with no values is disabled and says why ("No resort has snowfall data yet."). Nothing in a heat mode
  animates; the only animation on the map is the Hiring now pulse, left out under `prefers-reduced-motion`.

### Alaska at its true place, and the pan limit
Owner feedback 2026-10-14: Alaska draws only at its real place and scale (no inset), in the WebGL map and in the 2D
first frame alike: its outline, clusters, resorts, heat rasters, snowfall isolines and Natural Earth underlay.
* National view: `config.nationalBounds` frames the contiguous US (-125 to -66.9, 24.4 to 49.5), so the start view
  stays on the lower 48 and Alaska sits up and to the left, at the edge of the screen or just past it depending on
  the viewport. The 2D picture uses the same framing (`camera.js` `viewFrame`, `preview.js` `fitCamera`), so the
  switch to the WebGL map shows no jump. Clicking Alaska in the state list, or an Alaska resort, flies there as for
  any state.
* Pan limit (`config.pan`, `camera.js` `panCenter`, passed to MapLibre as `transformConstrain`). The middle of the
  open map area, the container minus the view's padding (the side panel, the layers panel on wide screens, the zoom
  column and the legend strip on desktop; the legend strip and the sheet on phones; `map.js` keeps the padding of the
  last fit, `mapCtl.openPadding`), stays inside `pan.center`: -190 to -60, 18 to 72. That reaches past the
  antimeridian (the far Aleutians at 172.9 E come to the middle), past the Arctic coast north of the Brooks Range,
  past Maine and south of the Florida Keys, so a click, hold and drag brings Alaska to the middle at the national zoom
  and lower, and the map can never be dragged away. The screen also stays between the poles. This limit replaces
  `maxBounds`, which holds the whole container: the phone's national view reaches far south behind its sheet (about
  52 S), so bounds that fit it would let a desktop drag far into the Pacific.
* World copies are on (`renderWorldCopies: true`), so Alaska's islands west of 180 degrees draw beside it. Zooming
  out stops at `pan.minZoom` (1.2) or sooner, where the screen would span more than `pan.maxWorldShare` (0.62) of the
  world, so the map never sits beside its own copy (`camera.js` `panMinZoom`, updated on resize). At those zooms
  Alaska and the lower 48 show together: about 2.0 on a 1280 px desktop, 1.2 on a 390 px phone.
* Hawaii, DC and the territories (no ski areas) never draw.
* Cost: the inset's code and layers are gone; the national picture still draws two quarter size underlay copies
  (Alaska's edge shows in its upper left). Measured 2026-10-14, 3 Lighthouse runs: `/map/` 98, 98, 98 (LCP 2.11 to
  2.12 s, the intro text), Vail 96, 96, 95, Jackson Hole 97, 97, 97; throttled interactive 1.6 s on desktop and phone.
* Tests: `tests/pipeline/test_map_pan.py` runs `panCenter` and `panMinZoom` in Node (Alaska, the far Aleutians and
  the Arctic coast reach the middle; the national views are never clamped; drags stop at each edge; wrapped
  longitudes; the poles; zoom out over both). The browser test checks, on the real build at desktop and phone sizes,
  that no inset layer or source exists, that a mouse drag brings Anchorage to the middle at the national zoom, that
  every Alaska resort and Alaska's outline render at their real coordinates, that a pan far past Alaska stops at the
  box, that zooming out shows Alaska and the lower 48 together, and that the state list and a resort click open
  Alaska at its true place.

### Panel changes from the 2026-10-13 owner decisions
* Professionalism: removed from the public site (owner decision, 2026-10-13). The panel, the list and compare views,
  the route stubs and their meta descriptions show no rating.
* Lucrativeness (decision 4, wording, mid and method of 2026-10-14): the block shows "Estimated net income", `mid`
  with its estimated badge, "Range: $low to $high" (`.lucr-range`) and the pay and season length sources
  (`.lucr-sources`). No assumptions list, no formula and no rent. The rent line (`.rent-line`) renders
  `lucrativeness.rent` first in the Housing section's "Rent near the resort": "Median rent $2,275 a month for one
  bedroom in Vail, CO (median of 2 sources: Rentometer, Zumper), 2026", "single source" for one figure, "in the
  Bozeman metro" or "in the Municipality of Anchorage" for those places; the fallbacks read "Zillow typical rent (ZORI)
  $2,450 a month (Summit County, CO, Aug 2026)" and "Census ACS median gross rent $1,950 a month (Summit County)". Then
  its confidence badge, Needs update when `stale`, "Last verified <as_of>" and every source linked.
* Access (decision 6): Housing and commute opens with "Getting to the mountain": the rating label (Convenient,
  Transit friendly, Mixed, Car centric), its badge, the one sentence `summary_md` and the source with its date
  (`resort.access`, or the detail file's `access` when the resort has none).
* Reports (decision 11): the form POSTs JSON to `definitions.submissions.endpoint` (the site's Formspree form) with
  `Accept: application/json`. Fields are flat and match the `python -m pipeline.reports import` columns:
  `_subject` ("Career Map report: <resort name>"), `resort_id`, `kind`, `season`, `cert_level` (pay),
  `housing_site_id` (housing), `hourly_usd` or `monthly_usd`, `proof_url` (optional), `submitted_at`. Success says
  "Thanks. Your report goes to review before anything appears on the map."; a failure shows an error and a
  "Try again" button that resends the same report. Submitted values never render.

### Trail map dialog (`map/js/trailmap.js`, `map/js/officialmap.js`)
* Titled "<resort> trail map". Opened from "Open the trail map" in the resort panel and from a map control (mountain
  icon under the zoom and home buttons on the right edge) on resort views whose record has `trailmap_available` or an
  official map file.
  `trailmap.js` and `officialmap.js` load on first open; nothing of the official map loads before the dialog opens.
* Two tabs (WAI-ARIA tabs, automatic activation; Left, Right, Home and End move between them): **Official map**
  (when there is a file; selected on open) and **Interactive** (when `trailmap_available`). With one view the tab list
  is hidden. The Interactive tab loads `trailmap/<id>.json` (validated with `TrailMapFile` and per feature
  `RunFeature` and `LiftFeature` in `schemas.js`) and MapLibre the first time it shows.
* Official map, image: an `<img loading="lazy" referrerpolicy="no-referrer" decoding="async">` straight from the
  resort's server in a zoomable, pannable viewer: mouse wheel (about the pointer), pinch, drag, double click, plus,
  minus and Reset buttons, and on the focused map the arrow keys (Shift for bigger steps), plus, minus and 0. It starts
  fitted to the frame; zoom runs from the fit to six times the fit (at least 2x the file's pixels).
* Official map, PDF: an `<object type="application/pdf">` with a "View full size on <resort>" fallback link inside.
  Browsers without a PDF viewer (`navigator.pdfViewerEnabled === false`, most phones) get no object.
* Under the map: "Trail map © <resort>", a link to the resort's trail map page (when it differs from the file),
  "View full size on <resort>" (the file, new tab), the season and the `image_note`.
* Fallback: an image `error`, an object `error` (an HTTP error) or no object `load` within 15 s, or no PDF viewer,
  shows a short note with a link to the file ("The official trail map could not load from <resort>." or "This browser
  opens PDF trail maps in a new tab.") and selects the Interactive tab when there is one. A PDF that the resort's
  server refuses to frame or forces to download may show nothing without an error; the link and the Interactive tab
  stay one click away.
* Checks (2026-10-14): `tests/map/browser_test.mjs` serves a generated PNG and PDF for every official map URL (no
  network, no committed image) and covers the viewer, the tabs from the keyboard, the image, PDF and no PDF viewer
  fallbacks and axe on each tab; `tests/map/audit.mjs` runs axe on both tabs for Vail. Lighthouse medians over 3 runs
  (load average 7): `/map/` 98, Vail 96, Jackson Hole 96, credits 100.
* A native modal `<dialog>`: the page behind is inert, Tab cycles inside, Escape, the Close button and a backdrop click
  close it, focus returns to the button that opened it. Reopening the same resort keeps its tab, map and last view;
  another resort replaces it. Phones get a full screen sheet with the legend below the map.
* Its own MapLibre map on a light surface: runs colored by `difficulty_us` (green `#12804a`, blue `#1b5fc1`,
  black `#14171c`, double black the same ink, wider and dashed, terrain park `#d4610a`, unrated grey dotted), area runs
  as filled outlines, lifts in red with a white casing on top, the base area (the resort's `lat`, `lon`) as a dark dot.
  Fitted to `bounds`. No glyph server: names show in a hover label (a tap on phones) and in a run list grouped by
  sign. Legend with the four signs plus terrain park, lift and base area; attribution to OpenSkiMap and
  OpenStreetMap contributors (ODbL), plus the `openskimap-runs` source link.

### Resort search (`map/js/search.js`)
* A combobox in the header (`#map-search`, WAI-ARIA APG list autocomplete: `role="combobox"` input with
  `aria-expanded`, `aria-controls` and `aria-activedescendant`, a `role="listbox"` of `role="option"` results, a polite
  status with the result count). Shown once the slim index has loaded; the module (about 3 KB gzipped, no Zod) loads on
  the first focus or tap of the box and searches only data already in memory: state, cluster and resort names from
  `map-index.json`, `clusters.geojson` and `states.json`.
* Matching: names and queries lose accents, case and punctuation ("Smugglers" finds "Smugglers' Notch Resort"); "Mount"
  and "Mt", "Saint" and "St", "Fort" and "Ft" match each other; a two letter state code finds its state. Rank: exact,
  then prefix (spaces ignored, so "i70" finds "I 70 Corridor"), then word prefix, then every query word starting a
  word, then anywhere; ties go states, clusters, resorts (catch all "Other" clusters last), Tier A first, shorter names
  first. At most 8 results, each with its kind, tier and state ("Tier A resort in Colorado").
* Keys: Down and Up move through the results (wrapping), Enter opens the active result (or the first), Escape closes
  the list and a second Escape clears the box, Tab closes it. A click or tap opens a result.
* A result goes through `router.go` with the map click's state rule (keep the current state when the cluster spans
  it), so the panel, URL and list view update and focus moves to the panel heading. Before the WebGL map starts this
  only redraws the 2D picture; the map starts on that view later. With the map running, it flies there. In the list
  view the current entry scrolls into view.
* Phones: the title wraps to two lines (it gives way while the box has focus) and the results span the screen below
  the top bar, ending above the bottom sheet; a fully open sheet drops to half when the results open.

### Sources and credits page (`/map/credits/`)
A hand written static page in the site layout (sidebar placeholder and fetch snippet from the map shell, no map
JavaScript). It credits OpenSkiMap and OpenStreetMap contributors (ODbL), the Natural Earth underlay (public domain), USGS The National Map imagery, US Census
outlines via `us-atlas`, NOAA NCEI U.S. Climate Normals 1991 to 2020, NRCS SNOTEL, Open Meteo with ERA5 (CC BY 4.0),
the rent sources (section "Rent": published median rents and their publishers; "Rent data: Zillow Observed Rent Index
(ZORI), Zillow Research" linked to https://www.zillow.com/research/data/; Census ACS and HUD Fair Market Rents),
Wikipedia (CC BY SA 4.0) and the vendored libraries with their versions and the license files in `/map/vendor/`; it
explains the confidence levels and describes exactly the fields the report form sends to Formspree. Linked from the
map attribution, the panel footer and the footer of every prerendered stub. `tests/pipeline/test_credits_page.py`
checks the credits against `map/vendor/VERSIONS.json` and the report payload in `panel.js`.

## Raw inputs (`/data/raw`)

* `data/raw/tier_a/*.yaml`: hand researched Tier A records, one file per research batch (see `data/raw/tier_a/README.md`).
* `data/raw/ownership/*.yaml`: verified Vail and Alterra US portfolios with sources. Every listed resort needs a Tier A record (operator compensation).
  Every source an entry cites must be younger than one build cycle (`ownership.max_source_age_days`, 400 days), else
  the build fails (see "Refreshing the ownership sources").
* `data/raw/facts/*.yaml`: season, snowfall and size facts for every resort (Resort facts overlay above).
* `data/raw/webcams/*.yaml`, `data/raw/jobs/*.yaml`: live cams and jobs links (Webcams and jobs above).
* `data/raw/passes/*.yaml`: Epic, Ikon, Mountain Collective and Indy pass membership per season (Pass membership above).
* `data/raw/housing_search/*.yaml`: Furnished Finder links and housing search tools (Housing search above).
* `data/raw/editorial/*.yaml`: Phase 6 drafts and reviewed entries (format above).
* `data/raw/reports/`: Phase 7 queue and moderated reports (gitignored) and `aggregates.json` (committed).
* `data/raw/pay/*.yaml`, `data/raw/career/*.yaml`: Phase 4 pay records and career inputs (formats above).
* `data/raw/rent/`: `median_rent_*.yaml` (median rent figures), `zillow/zori_extract.csv` (committed ZORI extract; the
  full Zillow CSVs and the Census Gazetteer ZCTA file in `zillow/` and `zillow/download/` are gitignored) and the HUD
  and ACS cache (Phase 4 "Lucrativeness rent", Phase 5; owner instructions in `data/raw/rent/README.md`).
* `data/raw/sources/*.yaml`: pipeline owned shared sources (`{ sources: Source[] }`, e.g. `owner-decisions-2026-10-11`) that any research file may cite. Source ids are unique across all of `/data/raw`; the same id may repeat only with an identical url.
* `data/raw/openskimap/`: downloaded OpenSkiMap ski areas (gitignored when large; the filtered US extract is committed).
* `data/raw/transit/`: `feeds.yaml` (GTFS feeds from the Mobility Database catalog), downloaded `<agency>/gtfs.zip` (gitignored) and the committed `<agency>.json` extracts (Phase 5 above; coverage and skipped feeds in `data/raw/transit/README.md`).
* `data/raw/climate/`: cached Open Meteo and SNOTEL responses (gitignored), except the two committed extracts below.
* `data/raw/climate/noaa_normals/` (committed): `ann_snow_normals.csv`, one row per station of the 50 states with the
  `ANN-SNOW-NORMAL` column of the NOAA NCEI U.S. Climate Normals 1991 to 2020 (annual and seasonal, by station):
  `station_id, name, lat, lon, elevation_m, ann_snow_in, meas_flag, comp_flag, years` (5,653 rows, about 380 KB), and
  `source.json` (the source record, confidence `published`, public domain, the archive key, size and ETag, and the flag
  meanings). `python -m pipeline.climate.noaa_normals fetch` writes both from the bulk archive on the NOAA Open Data
  Dissemination mirror `https://noaa-normals-pds.s3.amazonaws.com/` (`normals-annualseasonal/1991-2020/archive/`; the
  same files as ncei.noaa.gov, which the sandbox cannot reach), downloaded to `download/` (gitignored). Requests use the
  pipeline's generic User-Agent and never an email address. The build keeps a station when its completeness flag is in
  `heat.snowfall.accept_completeness` (S, R, P; E, estimated from neighbors, is dropped) and its measurement flag in
  `accept_measurement_flags` (blank or X, a nonzero value rounded to zero; M, V, W, Y and Z are dropped). Zero snowfall
  stations in the South are real data and stay. 5,568 stations fall inside the two regions.
* `data/raw/climate/snotel_snowfall/` (committed): `snotel_annual_snowfall.csv`, one row per NRCS SNOTEL station whose
  record could hold `heat.snowfall.snotel.min_complete_years` water years of the window:
  `station_id` (AWDB triplet), `name, state, lat, lon, elevation_ft, annual_snowfall_in, years_used, complete_years,
  first_water_year, last_water_year, method`, and `source.json` (source `nrcs-snotel-awdb-annual-snowfall`, confidence
  `estimated`, public domain, the derivation in its note). Written by `python -m pipeline.climate.snotel_stations
  fetch` (station list and daily WTEQ, SNWD and TAVG from the AWDB REST API, one station per request, retries and
  backoff from `pipeline/climate/http.py`, cached as gzip JSON in `download/`, gitignored) then `extract`. Derivation:
  daily new snow = daily SWE gain / new snow density, never below the daily depth gain, with every `climate.snotel`
  threshold (the `snotel_swe` method of `pipeline/climate/snotel_snowfall.py`); density from the station's own
  monthly median storm density, else the Anderson (1976) estimate from the station's daily mean air temperature
  (TAVG) in place of ERA5. Per water year (Oct 1 to Sep 30, named by the September year) the days are summed; a
  year is complete when November to April have `climate.min_month_coverage` of their days with data. The annual
  figure is the median of the complete years `first_water_year` to `last_water_year` (2011 to 2025), published only
  with at least `min_complete_years` (8); otherwise `annual_snowfall_in` is empty and `years_used` 0. AWDB has no
  snowfall element or snowfall normal for SNOTEL (its SWE and depth normals describe the pack), so the derived
  figure is used. `python -m pipeline.climate.snotel_stations validate` writes the comparison with published resort
  snowfall into `docs/map/climate-validation.md` (its own section, kept when the climate run rewrites the file).
* `data/raw/terrain/` (committed): `terrain-lower48.png` and `terrain-alaska.png`, 16 bit greyscale PNGs of the mean
  ground height (m, under sea level stored as 0) per 1/16 degree cell over each heat region box (rows from the north
  edge, columns from the west edge, cell centers at half steps), and `source.json` (source `aws-terrain-tiles`, the
  grid of each region and the attribution). `python -m pipeline.terrain fetch` averages the AWS Terrain Tiles
  (Mapzen terrarium PNGs, zoom 6, from USGS 3DEP, SRTM and GMTED2010 and NOAA ETOPO1) into the grid; the tiles stay
  in `download/` (gitignored). About 345 KB and 170 KB. The build only shapes the snowfall field with it; no height
  is displayed.

## Refreshing the ownership sources (yearly, ROADMAP item 17)

Ownership is verified from the companies' own sites or filings each build cycle (CLAUDE.md). One build cycle is
`ownership.max_source_age_days` in `config/definitions.yaml` (400 days: a year plus a month of slack).
`tier_a.ownership_source_age_check` measures each source that an entry of `data/raw/ownership/vail.yaml` or
`alterra.yaml` cites, from its `accessed_date` to the build date (`SOURCE_DATE_EPOCH` when set, else today), and the
build fails with one line per stale source: the file, the source id, its `accessed_date`, its age and the limit.
Tests: `tests/pipeline/test_tier_a.py` (fresh passes, stale fails, the limit pinned in `tests/pipeline/test_config.py`).

1. **Open** the current portfolio pages. Vail Resorts: the latest Form 10-K on SEC EDGAR (company 0000812011,
   Item 1 Business and Item 2 Properties; filed after the fiscal year that ends July 31) and the resort list on
   `https://www.vailresorts.com/`. Alterra Mountain Company: its own destinations page (`alterramtnco.com`, which
   redirects to `alterramtn.co`); until that page is reachable from the research environment, the Wikipedia list of
   resorts and each resort's infobox stay the cited secondary sources, as the header of `alterra.yaml` explains.
2. **Update** `data/raw/ownership/<operator>.yaml`: add resorts the operator bought and remove resorts it sold (US
   only); point each source at the current page (a new 10-K has a new url and title) and set `accessed_date` to the
   day you opened it; set `verified_date` to the same day; keep the notes factual. A new resort needs a Tier A record
   (operator compensation, `missing_tier_a_record: fail`); a sold resort's Tier A record needs its new `owner`.
3. **Rerun** `python pipeline/build.py` (the ownership cross check and the age check), then `python -m pytest -q`, and
   read the ownership section of `docs/map/tier-a-draft.md`. Commit the ownership files with the rebuilt
   `data/build`.

## Pipeline layout

```
pipeline/
  build.py            # entry point: load raw + config, validate, write data/build
  schemas/            # Pydantic models for every entity in this file
  sources.py          # source registry, validation of source_id references
  openskimap.py       # fetch + filter US operating downhill areas (Tier B)
  tier_a.py           # load data/raw/tier_a + ownership (cross check, source age), merge with OpenSkiMap
  clusters.py         # cluster assignment and polygon generation
  states.py           # us-atlas to us-states.geojson
  routes.py           # deep link stubs under /map/, data/build/map-index.json
  share.py            # share cards images/share/*.png for cluster and Tier A resort pages (og:image)
  raster.py           # stdlib antialiased polygon rasterizer into a palette image (share cards)
  glyphs.py           # vector text from pipeline/fonts/*.json: layout, kerning, wrapping (share cards)
  otf.py              # one time OpenType CFF reader that writes pipeline/fonts/*.json
  fonts/              # Inter Bold and Regular outlines (SIL OFL 1.1, OFL.txt)
  career.py           # Phase 4 pay display, staleness, lucrativeness (professionalism model internal only)
  lifts.py            # Phase 5 OpenSkiMap lifts for Tier A resorts
  runs.py             # Trail map: OpenSkiMap runs for Tier A resorts
  housing.py          # Phase 5 housing sites, rent display, detail file assembly
  housing_search.py   # Furnished Finder links and ranked housing search tools -> Resort.housing_search
  rent.py             # Phase 5 HUD FMR and ACS baselines, county lookup
  rent_median.py      # median rent across sourced figures (lucrativeness rent, first step)
  rent_zillow.py      # Zillow ZORI fetch, parse, extract and ZIP, county, metro matching
  rent_used.py        # the lucrativeness rent order -> Resort.career.lucrativeness.rent
  heat_lucrativeness.py  # lucrativeness heat rasters (heat/lucrativeness.json and PNGs)
  transit.py          # Phase 5 GTFS extraction and bus commute model
  routing.py          # Phase 5 Valhalla / OSRM / ORS client with cache
  editorial.py        # Phase 6 editorial loader, approve command, review document
  reports.py          # Phase 7 report import, moderation, aggregation
  staleness.py        # Phase 7 stale value report
  facts.py            # Resort facts overlay loader and merge
  links.py            # shared loader for the link only batches (webcams, jobs)
  webcams.py          # live webcam links -> Resort.webcams
  jobs.py             # jobs links and employers -> Resort.jobs, jobs/employers.json, empty jobs/postings.json
  hiring.py           # hiring window text -> start and end month (the Hiring now filter)
  passes.py           # pass membership (data/raw/passes) -> Resort.passes (the Epic and Ikon chips)
  jobs_feed.py        # daily open postings from public job board APIs -> jobs/postings.json (GitHub Actions)
  link_check.py       # monthly check of trail map, webcam and jobs links (GitHub Actions issue)
  heat.py             # Heat maps: snowfall rasters and isolines (NOAA and SNOTEL stations shaped by the terrain,
                      # plus resort pockets), season rasters
  png.py              # Minimal stdlib PNG writer and reader (heat rasters, terrain grid and tiles, palette cards)
  terrain.py          # Terrain grid for the snowfall field (AWS Terrain Tiles -> data/raw/terrain)
  climate/            # Phase 3 ETL (Open Meteo, SNOTEL, density, aggregation); noaa_normals.py (NOAA station normals);
                      # snotel_stations.py (SNOTEL station annual snowfall for the snowfall heat map)
config/
  definitions.yaml    # thresholds, weights, assumptions (ROADMAP Section 6)
  clusters.yaml       # cluster definitions and membership rules
```
