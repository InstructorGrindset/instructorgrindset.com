# Career Map architecture and data contract

This is the contract between the Python pipeline (`/pipeline`) and the map frontend (`/map`). Change it here first, then in both the Pydantic schemas and the Zod schemas.

## Stack (Phase 0 decision)

The existing site is hand built HTML on GitHub Pages with no build step. The roadmap says to match the existing site, and introducing Astro would mean moving the live site to a GitHub Actions build. The map therefore ships as plain static files:

* `/map/index.html` uses the existing sidebar partial and `/assets/global.css`.
* Native ES modules in `/map/js/`, no bundler.
* Third party libraries are vendored from npm into `/map/vendor/` at pinned versions (MapLibre GL JS, Zod, Chart.js). `scripts/vendor.sh` refreshes them. Nothing loads from a CDN.
* Basemap: state outlines from `us-atlas` compiled to `/data/build/us-states.geojson`. A Protomaps PMTiles basemap plugs in later through `basemap.pmtiles_url` in `/map/js/config.js` once tiles are hosted on R2.
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
  season: { open_typical, close_typical, length_days, training_start }, // each Sourced | null; dates as "MM-DD"
  terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags },  // pcts Sourced<number 0 to 100> | null; tags string[]
  fit: { enthusiast_types: string[], professional_types: string[] },
  clients: { profile_tags: string[], notes_md: string | null },
  career: Career | null,   // Phase 4, see "Phase 4 career" below; null when a resort has no pay records and no career inputs
  editorial: Editorial | null,   // Phase 6; only reviewed entries, see "Phase 6 editorial"; null otherwise
  climate_available: boolean,                      // true when climate/<id>.json exists
  detail_available: boolean,                       // true when detail/<id>.json exists (Phase 5)
  trailmap_available: boolean,                     // true when trailmap/<id>.json exists (Trail map)
  housing_summary: { site_count, cheapest_rent_monthly_low, cheapest_site_id, has_transit } | null,   // Phase 5
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
Point FeatureCollection mirroring `resorts.json` for MapLibre. `properties`: `{ id, name, tier, facility_type, cluster_id, state, acres, vertical_ft, lifts, size_rank, season_length_days, snowfall_annual_in, professionalism_score, lucrativeness_mid }` where `size_rank` is 1 to 5 from acres (null acres gives 1). The last four are for map recolor modes, each number | null: `season_length_days` is `season.length_days.value`, `snowfall_annual_in` is the sum of the climate file's monthly `snowfall_in` (null without a climate file or without any monthly value), `professionalism_score` is `career.professionalism.score`, `lucrativeness_mid` is `career.lucrativeness.mid`.

### `states.json`
```
{ version, generated_at, states: [{ code, name, cluster_ids: string[], resort_count }] }
```
Only states with at least one resort.

### `sources.json`
```
{ version, generated_at, sources: [{ id, url, title, accessed_date, note }] }   // note: string | null (optional qualifier)
```
Includes every source declared in `/data/raw` plus the climate ETL's `data/build/climate/sources.json`, merged by id.

### `definitions.json`
Display relevant subset of `config/definitions.yaml`, so the UI never hardcodes thresholds:
```
{ version, generated_at,
  snow: { powder_day_in, banner_day_in },            // new snow in 24 hours
  density: { dry_max_pct, moist_min_pct },           // dry < dry_max_pct, average between, moist > moist_min_pct
  display: { priority: ["published", "reported", "estimated"] },
  reports: { aggregation_min },                      // approved reports needed before a Reported range shows
  submissions: { endpoint: string | null },          // report form target; the form is hidden while null
  staleness: { max_age_months } }                    // records older than this are flagged
```

### `us-states.geojson`
State outlines (simplified) with `properties: { code, name }`.

### `map-index.json` (Phase 8, written by `pipeline/routes.py`)
Slim national index the map loads first. Compact JSON, one row per resort as an array in `fields` order:
```
{ version, generated_at,                 // copied from resorts.json
  fields: ["id", "name", "tier", "tier_a_status", "facility_type", "state", "cluster_id", "lat", "lon",
           "acres", "size_rank", "climate_available", "detail_available",
           "season_length_days", "snowfall_annual_in", "professionalism_score", "lucrativeness_mid"],
  resorts: [[...], ...] }
```
Every value is copied from `resorts.json` (`acres` is `size.acres.value`) and `resorts.geojson` (`size_rank` and the four
recolor values); without `resorts.geojson`, `size_rank` uses `display.size_rank_acres_breaks` and the recolor values
come from `resorts.json` as in the `resorts.geojson` rules, with `snowfall_annual_in` null. Resorts with an unsafe id or
no coordinates are left out. About 16 KB gzipped against 87 KB for `resorts.json` plus 21 KB for `resorts.geojson`.
The frontend builds its resort points from this file and falls back to `resorts.json` and `resorts.geojson` when it
is absent.

### `sitemap.xml` (written by `pipeline/routes.py`)
Only the `/map/` entries are rewritten; every other entry stays byte for byte. The map entries are the canonical URLs of
`/map/`, every state page, every cluster page and Tier A resort pages. Tier B resort pages are generated, linked and
crawlable (no `noindex`) but left out of the sitemap.

### Resort route stubs embed their record
Each resort page under `/map/` carries `<script type="application/json" id="resort-record">` with
`{ version, generated_at, resort: <its resorts.json record>, sources: [<the sources listed in resort.sources>] }`
(every `<` escaped as `<`; an editorial block that is not `reviewed` is dropped). The panel validates it with the
same Zod schema as `resorts.json` and renders the resort without waiting for the full file.

### `climate/{resort_id}.json` (Phase 3, lazy loaded per resort)
```
{ version, generated_at, resort_id,
  source_ids: string[], period: { start_year, end_year },
  months: ClimateMonth[12] }   // ordered by ski season: Oct, Nov, Dec, Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep
ClimateMonth { resort_id, month (1..12), snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               density_pct_median, density_mix: { dry, average, moist },   // fractions summing to 1, or null
               density_source: "snotel" | "estimated" | null,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
```
Monthly values are climatological means over `period`; `powder_days` and `banner_days` are mean days per month. Climate values are modeled (ERA5 reanalysis and SNOTEL derived), so the UI treats them as confidence `estimated`, with `as_of` equal to the climate file's `generated_at` and sources from `source_ids`.

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
  professionalism: { score: number|null (0..100), status: computed|weights_unset|insufficient_data,
                     breakdown: [{ component, weight, value, points, source_id }] },   // all six components, always
  lucrativeness: { low, mid, high (USD season savings) | null, status: computed|assumptions_unset|insufficient_data,
                   assumptions: [{ key, value, label }] },
  stale_fields: string[]   // "pay.<cert_level>", "private_rate", "private_split", "professionalism.<component>"
}
```
* **Staleness:** a value is stale when its `as_of` / `verified_date` plus `staleness.max_age_months` (18) calendar months is
  before the build date. A value exactly 18 months old is still current.
* **Professionalism:** score = 100 × Σ(weight × value) / Σ(weight) over components with a value. `points` = that component's
  share of the score. Status `weights_unset` while any weight or `professionalism.min_components` in config is null;
  `insufficient_data` when fewer than `min_components` components have values; otherwise `computed`.
* **Lucrativeness:** earnings = mid cert pay (midpoint of the displayed row for `assumptions.cert_level`) × `hours_per_week`
  × season length days / 7. costs = (rent + `food_monthly_usd` + `transport_monthly_usd`) × `season_months` +
  `pass_offset_usd` + `gear_offset_usd`. mid = earnings − costs; low = mid − `range_spread.low` × earnings;
  high = mid + `range_spread.high` × earnings; whole dollars. Rent is the cheapest employee housing (Phase 5) or the
  HUD / ACS baseline. Status `assumptions_unset` while any assumption or spread is null; `insufficient_data` when pay,
  rent or season length is missing. `assumptions` echoes every config assumption, both spreads, and the resort inputs
  used (`pay_hourly_mid_usd`, `season_length_days`, `rent_monthly_usd`), each with a display label.
* **Shared calculator model (Phase 9):** lucrativeness is computed by `pipeline/season_income.py`, the Python mirror of
  `assets/js/season-income.mjs` used by the Season Income Calculator: earnings = its `gross`, mid = its
  `net_before_tax` (via `lucrativeness_inputs`), so the map and the calculator agree for the same inputs. Whole dollars
  use `round_half_up` (JS `Math.round`, ties toward positive infinity). The calculator's headline net is after tax;
  the map's lucrativeness is savings before tax (ROADMAP Section 6) and applies no tax assumption, which stays the
  owner's call. The calculator has state income tax rates for only 13 states (WA, WY, NV, AK, NH, CO, UT, ID, VT, MT,
  NY, OR, CA).

## Phase 5 housing and commute

Raw inputs (every value sourced; research and ETL fill them later):
* data/raw/housing/*.yaml: `{batch, researched_date, sources: [...], sites: [HousingSite]}`
  HousingSite { id, resort_id, name, type: resort_owned|master_lease|market, lat, lon, location_source_id,
                precision: building|neighborhood,   # building only when the address is publicly listed
                rent_monthly: Sourced<{low, high}> | null, occupancy: shared|private|mixed|null, deposit: Sourced<number>|null,
                eligibility_note: string|null, confidence, verified_date }
* data/raw/rent/: HUD Fair Market Rents and Census ACS median gross rent, cached API responses or CSV
  (county and ZIP level), loaded by pipeline/rent.py into a baseline per resort (county of the base area,
  plus ZIPs of housing sites). HUD API needs a token in env HUD_API_TOKEN; ACS works without a key at low volume.
* data/raw/transit/<agency>/gtfs.zip (gitignored) -> pipeline/transit.py extracts routes and stops within
  `transit.search_radius_km` of any housing site or the base area; committed extract data/raw/transit/<agency>.json.
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
  transit: { routes: [ { id, agency, short_name, long_name, color, geometry (MultiLineString) } ],
             stops: [ { id, name, lat, lon, route_ids } ], source_ids } | null,
  isochrones: [ { minutes, mode: "drive", geometry (Polygon|MultiPolygon), source_id } ] }
```
  Written for every Tier A resort that has at least lifts or housing; empty lists or null where data is missing.
* resorts.json Resort gains `detail_available: bool` and
  `housing_summary: { site_count, cheapest_rent_monthly_low (number|null), cheapest_site_id, has_transit } | null`.
* The Phase 4 lucrativeness rent input uses the cheapest employee housing site (resort_owned or master_lease) rent,
  else the baseline (studio FMR), per config; career output echoes which was used.
* config/definitions.yaml gains: `routing: {provider, base_url, api_key_env, isochrone_minutes: [15, 30, 45]}`,
  `transit: {search_radius_km}`, `rent: {baseline_preference: [fmr_studio, acs_median]}` with sensible defaults
  for provider settings and nulls for anything that is a judgment call for Alexandre.

### Pipeline notes (additions to the contract)
* **HousingSite** also takes `address: Sourced<string> | null`; `precision: building` requires it (the publicly listed address and its source). Site ids are unique across all housing files; `resort_id` must exist in the build.
* **County lookup:** point in polygon of the base area against `data/raw/geo/us-counties.geojson` (us-atlas counties-10m via `npm run build:counties`, committed). ZIP level baselines need ZCTA outlines and are not wired yet.
* **Rent sources:** `hud-fmr-fy<year>` and `census-acs5-<year>-b25064`. A site without published rent shows the first value in `rent.baseline_preference` as `{low: v, high: v}`, confidence `estimated`, with a note naming the baseline.
* **Lucrativeness rent:** `rent.lucrativeness_rent` order (`employee_housing`, then `baseline`); `career.lucrativeness.assumptions` gains `rent_basis` (`employee_housing:<site id>` or a baseline key such as `fmr_studio`) and `rent_source_id`.
* **Transit extract** (`data/raw/transit/<agency>.json`): `{ version, agency, source, search_radius_km, routes, stops, patterns }`; ids are prefixed `<agency>:`; `patterns: [{ route_id, stop_ids, offsets_min, trip_count }]` hold median scheduled minutes from the first kept stop. Bus commute = walk to a stop within `transit.max_walk_km` + scheduled ride + walk from a stop within `max_walk_km` of the base, at `transit.walk_speed_kmh`, no waiting time; null until Alexandre sets both.
* **Routing:** source id `routing-<provider>`; the build reads only the cache `data/raw/routing/<provider>/<sha1>.json` written by `python -m pipeline.routing run`. Drive commute `source_id` is `routing-<provider>`; bus commute `source_id` is `gtfs-<agency>`.
* **Lifts:** extract features carry `{ id, name, ref, lift_type, ski_area_ids }`; only `status: operating` lifts. Detail lifts carry exactly `{ name, lift_type, source_id }`. `name` may be null in OpenStreetMap.
* **Detail files** are written compact; files for resorts that no longer qualify are removed. `housing_summary.cheapest_rent_monthly_low` uses published or reported site rents (never the baseline); `has_transit` is true when the detail file has transit.

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
  `python -m pipeline.editorial approve <cluster|resort> <id> [--by NAME]`, which flips the status in place and records
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
  `note` gives the count). Lucrativeness rent order (`rent.lucrativeness_rent`): `employee_housing` (published or
  reported site rent), `reported_area` (`rent_reported`), `baseline`.

### Staleness (`python -m pipeline.staleness`)
Writes `docs/map/staleness.md`: every sourced value in `resorts.json` and `detail/*.json` whose `as_of` or
`verified_date` is older than `staleness.max_age_months`, grouped by resort and field. Phase 4 already flags
`career.stale_fields` and `pay[].stale` in the build output.

### Submissions endpoint
`config/definitions.yaml` `submissions: { endpoint: null }` is exposed in `definitions.json` as
`submissions: { endpoint }`. The UI shows the report form only when `endpoint` is a URL.

## Frontend (`/map`)

### Load order (Phase 8 performance)
1. The page head preloads `maplibre-gl.js` (271 KB gzipped, the largest file) at low priority, so the render blocking
   CSS goes first, and preloads `map-index.json`, `clusters.geojson` and `states.json`. `app.js` runs MapLibre only
   after the first contentful paint, so its parse and the WebGL setup never hold back the first paint. The map
   control CSS loads without blocking the paint. `/map/index.html` carries the national heading and intro as its
   prerendered block, so the first paint already shows real text. The modules on this path
   (`app`, `config`, `data`, `router`, `panel`, `ui`, `labels`, `listview`, `sheet`, `meta`, `preview`, `camera`) carry no
   Zod and no charts. The first paint files are checked by hand in `data.js`, mirroring `schemas.js`.
2. The panel and the list view render from the slim index. `preview.js` draws the first map frame on a 2D canvas
   (clusters and resort dots with the WebGL map's framing and colors). It is a picture only, removed when MapLibre
   fires `load`.
3. When MapLibre arrives, `map.js` builds the WebGL map (workers prewarmed first).
4. After the map's `load`: `resorts.json`, `sources.json` and `definitions.json` (`index.loadDetails()`), the color
   mode control (`recolor.js`, `heat.js`), then `us-states.geojson`. Source links rendered earlier show a placeholder
   that fills in when `sources.json` lands.
* Full resort records are validated with Zod one at a time, the first time a view needs them (`index.full(id)`).
  A resort page validates the record its stub embeds, so it needs Zod and `definitions.json` but no `resorts.json`.
  While that loads, the stub's prerendered summary stays on screen (`aria-busy` on the panel).
* Lazy modules: `schemas.js` and Zod, `charts.js` and Chart.js (climate charts only), `detailcontrols.js` (resort
  detail map only), `recolor.js` and `heat.js`. The Inter web font loads after the window `load` event.
* **Budget method** (`tests/map/audit.mjs`): Playwright with Chromium under SwiftShader, CDP network throttling
  (150 ms RTT, 1.6 Mbps down, 750 Kbps up), 4x CPU slowdown, cache off, files gzipped. Times are the page's own
  performance marks from navigation start. *Interactive* is the later of two marks: the panel usable
  (`careermap:panel`, or `careermap:panel-full` when a resort record had to load) and the first map frame
  (`careermap:preview`). The WebGL map's first frame (`careermap:first-render`) and `load` (`careermap:map`) are
  reported next to it. Measured 2026-10-12: interactive 1.6 to 1.9 s on `/map/` and the Vail page (desktop and
  phone); WebGL load 4.5 to 5.4 s, down from 7 to 8 s before this change. The WebGL gap is SwiftShader and
  4x CPU: downloading MapLibre takes about 2 s, evaluating it about 0.5 s, the map constructor about 0.4 s, and
  shader compiling plus the first worker tiles another 1.5 to 2 s.
* Lighthouse (mobile defaults, simulated throttling, same SwiftShader Chromium) scores Performance about 65 on `/map/`
  and resort pages, with Accessibility, Best Practices and SEO at 100. First contentful paint is about 1.5 s and
  largest contentful paint about 2 s; total blocking time is 5 to 7 s, all of it MapLibre's script evaluation and
  WebGL setup scaled by the 4x CPU factor. Reaching 90 would mean starting the WebGL map only on the first
  interaction with the map, which is a product decision for the owner.

### Color modes: value heat maps
Lucrativeness, Professionalism, Season length and Snowfall draw a heat surface of the value
(`lucrativeness_mid`, `professionalism_score`, `season_length_days`, `snowfall_annual_in` from the slim index).
A stock MapLibre heatmap layer sums weights, so many small hills would look hotter than one big value. The map uses
an interpolated grid instead (`map/js/heat.js`):
* Each pixel is the inverse distance weighted mean of the resorts with a value within `radiusKm` (120 km):
  weight = taper(d) / max(d, `minDistanceKm`)^`power` with taper(d) = (1 − (d / R)²)², so weights fall smoothly to
  zero at R. Twelve resorts of the same value give the same color as one.
* Opacity depends only on the distance to the nearest resort with data: `maxAlpha` up to `fadeStart` × R, then
  fading to zero at R. Areas beyond R from any resort with data stay empty. Density never raises opacity.
* Resorts with a null value contribute nothing. Grey dots mark them on top of the surface.
* The grid is computed in the browser per mode (cached), about 7 km per pixel, rows evenly spaced in Web Mercator,
  one image per region (Alaska and the lower 48 apart), clipped to the state outlines, and drawn as MapLibre image
  sources with linear resampling under the state lines, cluster outlines and resort dots. Cluster tints are off in
  these modes so they do not shift the colors.
* Ramp: one hue, sequential blue steps 600 to 100 (`#184f95` to `#cde2fb`), dark for low and light for high on the
  dark map; the darkest step keeps about 2:1 against the background. The legend is a continuous bar with min, max,
  units (USD per season, score out of 100, days, inches per season), the resort count and the method in one line.
  Resort dots stay small: dark fill with a light ring, an amber ring for Tier A.
* A mode with no values is disabled and says why ("No resort has snowfall data yet."). Nothing animates, so
  `prefers-reduced-motion` needs no special case.
* The settings live in `config.heat` in `map/js/config.js`. They are display settings and change no resort number.

## Raw inputs (`/data/raw`)

* `data/raw/tier_a/*.yaml`: hand researched Tier A records, one file per research batch (see `data/raw/tier_a/README.md`).
* `data/raw/ownership/*.yaml`: verified Vail and Alterra US portfolios with sources. Every listed resort needs a Tier A record (operator compensation).
* `data/raw/editorial/*.yaml`: Phase 6 drafts and reviewed entries (format above).
* `data/raw/reports/`: Phase 7 queue and moderated reports (gitignored) and `aggregates.json` (committed).
* `data/raw/pay/*.yaml`, `data/raw/career/*.yaml`: Phase 4 pay records and career inputs (formats above).
* `data/raw/sources/*.yaml`: pipeline owned shared sources (`{ sources: Source[] }`, e.g. `owner-decisions-2026-10-11`) that any research file may cite. Source ids are unique across all of `/data/raw`; the same id may repeat only with an identical url.
* `data/raw/openskimap/`: downloaded OpenSkiMap ski areas (gitignored when large; the filtered US extract is committed).
* `data/raw/climate/`: cached Open Meteo and SNOTEL responses (gitignored).

## Pipeline layout

```
pipeline/
  build.py            # entry point: load raw + config, validate, write data/build
  schemas/            # Pydantic models for every entity in this file
  sources.py          # source registry, validation of source_id references
  openskimap.py       # fetch + filter US operating downhill areas (Tier B)
  tier_a.py           # load data/raw/tier_a + ownership, merge with OpenSkiMap
  clusters.py         # cluster assignment and polygon generation
  states.py           # us-atlas to us-states.geojson
  routes.py           # deep link stubs under /map/, data/build/map-index.json
  career.py           # Phase 4 pay display, staleness, professionalism, lucrativeness
  lifts.py            # Phase 5 OpenSkiMap lifts for Tier A resorts
  runs.py             # Trail map: OpenSkiMap runs for Tier A resorts
  housing.py          # Phase 5 housing sites, rent display, detail file assembly
  rent.py             # Phase 5 HUD FMR and ACS baselines, county lookup
  transit.py          # Phase 5 GTFS extraction and bus commute model
  routing.py          # Phase 5 Valhalla / OSRM / ORS client with cache
  editorial.py        # Phase 6 editorial loader, approve command, review document
  reports.py          # Phase 7 report import, moderation, aggregation
  staleness.py        # Phase 7 stale value report
  climate/            # Phase 3 ETL (Open Meteo, SNOTEL, density, aggregation)
config/
  definitions.yaml    # thresholds, weights, assumptions (ROADMAP Section 6)
  clusters.yaml       # cluster definitions and membership rules
```
