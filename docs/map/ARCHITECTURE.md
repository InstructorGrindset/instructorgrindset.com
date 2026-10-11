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
  editorial: null,    // Phase 6; only reviewed editorial is emitted
  climate_available: boolean,                      // true when climate/<id>.json exists
  sources: string[]                                // every source_id referenced by this resort
}
```

### `clusters.geojson`
GeoJSON FeatureCollection, one Polygon or MultiPolygon per cluster. `properties`:
```
{ id, name, states: string[], resort_ids: string[], tier_a_count, overview_md: null, sources: string[] }
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
  staleness: { max_age_months } }                    // records older than this are flagged
```

### `us-states.geojson`
State outlines (simplified) with `properties: { code, name }`.

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
  PayRecord { resort_id, season ("2025-26"), cert_level: none|level_1|level_2|level_3|trainer|examiner,
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
        // one row per cert_level (ordered none, level_1, level_2, level_3, trainer, examiner): the most recent
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

## Raw inputs (`/data/raw`)

* `data/raw/tier_a/*.yaml`: hand researched Tier A records, one file per research batch (see `data/raw/tier_a/README.md`).
* `data/raw/ownership/*.yaml`: verified Vail and Alterra US portfolios with sources. Every listed resort needs a Tier A record (operator compensation).
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
  routes.py           # deep link stubs under /map/
  career.py           # Phase 4 pay display, staleness, professionalism, lucrativeness
  climate/            # Phase 3 ETL (Open Meteo, SNOTEL, density, aggregation)
config/
  definitions.yaml    # thresholds, weights, assumptions (ROADMAP Section 6)
  clusters.yaml       # cluster definitions and membership rules
```
