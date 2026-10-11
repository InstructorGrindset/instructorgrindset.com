# Instructor Grindset: National Snowsports Career Map
Development roadmap for Claude Code

## 1. What we are building

An interactive US map at **instructorgrindset.com/map/** that answers one question for a working or aspiring snowsports instructor: **where should I work next season, and what will that season actually look like?**

The user drills down:

**National view → State (filter) → Cluster (mountain range or resort group) → Resort → Resort detail with housing and commute layer**

At each level the side panel shows:

| Category | Contents |
|---|---|
| Season | Typical open and close dates, season length in days, staff training start |
| Snow | Snowfall by month, powder days and banner days by month, typical storm depth, snow density (dry, average, moist), typical temps, wind |
| Economy and culture | Local economy in snowsports context, town culture, cost of living, who skis here |
| Career | Pay scale by cert level, private lesson rates and splits, professionalism score, lucrativeness (estimated savings per season) |
| Clients | Typical client profile (destination families, local kids programs, adaptive, race, freestyle, international, ultra high net worth privates) |
| Terrain and fit | Terrain character, which type of enthusiast and which type of professional the mountain suits |
| Operation size | Skiable acres, lifts, vertical, ski school staff estimate |
| Housing | Employee housing sites, rent, distance and commute mode to the base, transit lines |

## 2. Design decisions (read before writing code)

1. **Cluster is the core unit, state is a filter.** Tahoe spans CA and NV. The I 70 corridor, Wasatch, Northern Vermont, Summit County, and the Cascades behave as labor markets regardless of state lines. Instructors choose clusters. Clicking a state highlights its clusters; clicking a cluster opens the cluster panel.
2. **Two tiers of resort data.** There are roughly 470 US ski areas. Deep profiles for all of them would never be finished or kept current.
   * **Tier A (deep):** resorts that meet the Tier A criteria in Section 3. Full career, housing, commute, and editorial data.
   * **Tier B (auto):** every other ski area. Location, size, climate, season length, rent baseline. Generated entirely from open data.
3. **Every number carries a source, a date, and a confidence level.** Pay and rent go stale yearly. The UI shows "last verified" and a confidence badge. This is the credibility moat; the map itself is easy to copy.
4. **Highest confidence wins, per field.** For each field, display the best tier available: Published, then Reported (aggregated), then Estimated. Lower tiers are kept in the data but not displayed when a higher tier exists.
5. **Individual reports are never displayed.** Crowdsourced pay and rent appear only as aggregates of 3 or more reports. A single report from a small ski school is traceable to a person.
6. **Derived scores are transparent and fixed.** Lucrativeness and professionalism are models. The UI shows the result as a range plus the stated assumptions behind it. No adjustable inputs in this version.
7. **Static first.** All data compiles to versioned JSON and GeoJSON at build time. No backend until crowdsourced submissions (Phase 7).
8. **Editorial text is never invented by the model.** Claude Code may draft culture and economy overviews only from cited sources, and every draft is marked `status: draft` until Alexandre reviews it.

## 3. Tier A criteria

A resort qualifies for Tier A when it meets **at least one** of these:

* **Diehard following:** a core local and traveling skier culture that defines the mountain's identity
* **Luxury:** a destination clientele with high private lesson demand and premium pricing
* **Athlete development:** a ski and snowboard academy, club program feeding national teams, or regular national team training presence
* **Heritage:** historic significance to American skiing or snowboarding

### Seed list
1. **Vail Resorts destination resorts.** Exclude regional feeder hills (for example Midwest and Mid Atlantic urban hills) unless they independently meet a criterion. Those go to Tier B.
2. **Alterra Mountain Company destination resorts.**
3. **Independents and other operators meeting the criteria.** Starting candidates: Jackson Hole, Aspen Snowmass, Telluride, Taos, Sun Valley, Big Sky, Alta, Snowbird, Sugarloaf, Sugar Bowl, Burke, Waterville Valley.

### Rules
* Ownership changes often. Phase 1 verifies current Vail and Alterra US portfolios from the companies' own sites; never hardcode ownership from memory.
* Each Tier A resort records which criteria it meets and a source for each.
* Alexandre approves the final list before Phase 4 data collection begins.

## 4. Stack

Phase 0 confirms the current site setup. instructorgrindset.com appears to be a custom static site, so the map ships as a subfolder route.

* **Placement:** `instructorgrindset.com/map/` (subfolder, not subdomain, so search authority from resort pages flows to the whole domain and the consultation funnel). Map JavaScript loads only on `/map/` routes.
* **Framework:** match the existing site. If it is hand built HTML, use Astro (static output, island components) for the map section and keep the site's existing header, footer, and styles.
* **Map:** MapLibre GL JS with a Protomaps basemap served as PMTiles from Cloudflare R2 or similar
* **Charts:** Observable Plot or Chart.js
* **Data pipeline:** Python ETL scripts in `/pipeline`, output to `/data/build/*.json` and `*.geojson`
* **Validation:** Pydantic in the pipeline, Zod in the frontend
* **Routing and isochrones:** OpenRouteService API (free tier) or self hosted Valhalla, precomputed at build time
* **Phase 7 backend:** Supabase or a simple form plus moderation queue

## 5. Data sources

| Data | Primary source | Notes |
|---|---|---|
| Ski area locations, boundaries, lifts, runs | OpenSkiMap (OSM derived GeoJSON) | Free, includes lift count and run difficulty |
| Acres, vertical, season dates | Resort websites, NSAA, Wikipedia infobox | Tier A verified by hand |
| Resort ownership | Vail Resorts and Alterra official sites | Verified each build cycle |
| Snowfall, temperature, wind (all resorts) | Open Meteo historical API (ERA5 reanalysis), 20 to 30 year climatology | Covers every lat and lon |
| Snow density and storm depth | NRCS SNOTEL (AWDB API) | Density = change in SWE / change in snow depth per storm. Use nearest station within a set elevation band |
| Rent baseline | HUD Fair Market Rents API, Census ACS median gross rent | County and ZIP level |
| Employee housing sites | Resort HR and careers pages, housing authority sites | Building level only when publicly listed, otherwise neighborhood level |
| Transit | GTFS feeds (Summit Stage, RFTA, Park City Transit, TART, ECO Transit, etc.) | Precompute lines and stops near housing |
| Pay | Job postings in pay transparency states (CO, CA, WA, NY and newer states, verify the current list), resort pay sheets, crowdsourced reports | Never scrape Glassdoor or Indeed |
| Local economy | BEA county data, BLS QCEW leisure and hospitality share, Census | Cited in overviews |

## 6. Data model

```
State        { code, name, cluster_ids[] }
Cluster      { id, name, states[], polygon, resort_ids[], overview_md, sources[] }
Resort       { id, name, tier, tier_a_criteria[], owner, lat, lon, cluster_id, state,
               size: { acres, vertical_ft, lifts, ski_school_staff_est },
               season: { open_typical, close_typical, length_days, training_start },
               terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags[] },
               fit: { enthusiast_types[], professional_types[] },
               clients: { profile_tags[], notes_md },
               career: { pay_display, private_rate, private_split,
                         professionalism, lucrativeness: { low, mid, high, assumptions[] } },
               editorial: { overview_md, culture_md, alexandre_take_md, status: draft|reviewed },
               sources[] }
ClimateMonth { resort_id, month, snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               density_pct_median, density_mix: { dry, average, moist },
               density_source: snotel|estimated,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
HousingSite  { id, resort_id, name, type: resort_owned|master_lease|market,
               lat, lon, precision: building|neighborhood,
               rent_monthly_low, rent_monthly_high, occupancy, deposit,
               confidence, verified_date, sources[] }
Commute      { housing_id, resort_id, mode: drive|bus|walk|shuttle,
               minutes, distance_mi, transit_route_ids[], geometry }
PayRecord    { resort_id, season, cert_level, hourly_low, hourly_high,
               confidence: published|reported|estimated, source, verified_date }
Report       { id, resort_id, kind: pay|rent|housing, season, values, status: pending|approved|rejected }
               (raw crowdsourced input; never rendered directly)
Source       { id, url, title, accessed_date }
```

### Definitions (all values live in `config/definitions.yaml`)
* **Powder day:** 6 in or more new snow in 24 hours
* **Banner day:** 12 in or more new snow in 24 hours
* **Density classes (water content of new snow):** dry under 7%, average 7 to 12%, moist over 12%
* **Season length:** median of the last 10 seasons' open to close, where data exists
* **Display priority:** Published > Reported (3+ approved reports, shown as range) > Estimated
* **Professionalism score (0 to 100):** weighted from cert pay differential, paid training hours, examiner and trainer presence, private lesson share, year round staff, clinic access. Weights live in config.
* **Lucrativeness:** estimated season savings = season earnings minus (rent × months, food, transport, pass and gear offsets). Calculated with fixed standard assumptions (stated in config and shown in the UI): mid cert level, standard full time hours, cheapest available employee housing. Output as low, mid, high.

## 7. Phases

Each phase ends with acceptance criteria. Work one phase per Claude Code session; start each session in plan mode.

### Phase 0: Audit and setup
* Inspect the existing instructorgrindset.com codebase and hosting; document the stack and how existing tools are embedded
* Set up the `/map/` route inside the existing site layout
* Create `CLAUDE.md` with conventions: data lives only in `/data`, every record needs a source, no invented numbers, user facing copy follows Alexandre's style (no hyphens, no em dashes, no negative definitions, clean sentences)
* Scaffold `/pipeline`, `/data/raw`, `/data/build`, `/config`, `/src`
* **Done when:** `/map/` renders an empty map inside the site layout, CLAUDE.md committed

### Phase 1: Data foundation
* Pull OpenSkiMap ski areas for the US; filter to operating downhill areas
* Verify current Vail and Alterra US portfolios from official sites
* Write Pydantic schemas from Section 6; validation fails on missing sources
* Write `config/definitions.yaml` with Section 6 values
* Define clusters (hand drawn polygons or convex hulls of member resorts) and assign every resort
* Apply Section 3 criteria to produce the draft Tier A list with criteria and sources per resort
* **Done when:** `python pipeline/build.py` produces validated `resorts.json`, `clusters.geojson`, `states.json`; Tier A list approved by Alexandre

### Phase 2: Map MVP
* National map with state outlines, cluster polygons, resort markers sized by operation size; Tier A markers visually distinct
* Click state → zoom, highlight its clusters, panel lists clusters
* Click cluster → zoom, panel shows cluster summary and resort list
* Click resort → panel shows size, season, terrain basics
* URL state for deep links (`/map/co/i70/vail/`)
* Mobile layout: bottom sheet panel
* **Done when:** full drill down works on desktop and phone, deep links load the right view

### Phase 3: Climate engine
* ETL: Open Meteo daily history per resort, 20+ years, aggregate to ClimateMonth
* ETL: SNOTEL storms near Tier A resorts; compute storm density and depth distribution
* Fallback density estimate from storm temperature for resorts without a usable station; mark `density_source: estimated`
* Panel charts: snowfall by month, powder days and banner days by month, storm depth, density mix, temps
* Cluster comparison view (side by side climate for 2 to 4 resorts)
* **Done when:** every resort has 12 ClimateMonth rows, Tier A has SNOTEL density where a station exists, charts render

### Phase 4: Career layer
* Collect pay records for Tier A (pay transparency postings first)
* Private lesson rate and instructor split where published
* Professionalism score from config weights; show breakdown on hover
* Lucrativeness as fixed low, mid, high range with assumptions listed beneath it
* Client profile and "best suited for" tags (enthusiast and professional)
* Confidence badges on every career figure
* Map recolor modes: by lucrativeness, professionalism, season length, snowfall
* **Done when:** Tier A resorts show pay with confidence badges, lucrativeness range and assumptions display, recolor works

### Phase 5: Housing and commute
* Housing sites for Tier A with precision flag
* Rent ranges: resort published first, HUD and ACS baseline otherwise
* Precompute drive and transit times from each housing site to the base area
* GTFS lines and stops drawn near housing
* Resort detail map: base area, lifts, housing pins, commute lines, 15 / 30 / 45 minute isochrones
* **Done when:** clicking a Tier A resort shows housing pins with rent and commute, routes draw on the map

### Phase 6: Editorial layer
* Culture and economy overviews per cluster and Tier A resort, drafted only from cited sources
* "Alexandre's take" field for first hand notes
* Review workflow: `status: draft` hidden in production until set to `reviewed`
* **Done when:** all Tier A clusters have reviewed overviews

### Phase 7: Crowdsourcing and freshness
* Submission form for pay, rent, and housing reports (season, resort, cert level, optional proof)
* Moderation queue; approved reports stored as `Report`
* Aggregation job: a field gains a Reported value only with 3+ approved reports; displayed as a range only
* Display priority applied per field (Published > Reported > Estimated)
* Staleness job: flag any record older than 18 months
* **Done when:** test submissions flow through moderation, a field with 3 reports shows an aggregate range, a field with 2 shows nothing new

### Phase 8: SEO and polish
* Static page per resort and cluster under `/map/`, generated from the same data (for example "Instructor pay, housing, and snow at Jackson Hole")
* Every resort and cluster page links to the Free Consultation call
* Performance budget: map interactive under 3 s on 4G; climate data lazy loaded per resort; homepage unaffected by map assets
* Accessibility: keyboard navigation for the panel, list view alternative to the map
* Analytics on resort views to guide Tier A expansion
* Links into the book, blog, and other Grindset tools
* **Done when:** Lighthouse 90+ on map and resort pages, resort pages indexed

### Phase 9 (later): Shared calculator infrastructure
* Extract Season Income Calculator formulas into a shared module consumed by the map's lucrativeness pipeline
* No calculator UI inside the map
* **Done when:** map lucrativeness and the standalone calculator produce identical results for the same inputs, covered by tests

## 8. Working with Claude Code

* Start each phase with: "Read ROADMAP.md and CLAUDE.md. Plan Phase N." Approve the plan before it writes code.
* Keep data collection (pay, housing, editorial) separate from code sessions; research sessions output to `/data/raw` with sources.
* Require tests for every pipeline transform and every score formula.
* Commit after each completed acceptance criterion.

## 9. Decisions log

| Question | Decision |
|---|---|
| Tier A membership | Criteria based (Section 3); Vail and Alterra destination resorts plus qualifying independents as seed |
| Placement | Subfolder `/map/` on instructorgrindset.com |
| Powder and density thresholds | Research based defaults: powder 6 in, banner 12 in, density cutoffs 7% and 12% |
| Crowdsourced data | Highest confidence per field; reports shown only as aggregates of 3+ |
| Season Income Calculator | Shared infrastructure later (Phase 9); not visualized in the map |
