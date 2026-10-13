# Instructor Grindset: National Snowsports Career Map
Development roadmap for Claude Code

Status as of 2026-10-13: Phases 0 to 9 are built on branch `claude/quirky-gates-0bsdyo` and not yet merged. Section 7 records what shipped in each phase. Section 8 is the ordered build queue for the work still to come. Agents pick their next item from Section 8.

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
| Career | Pay scale by cert level, private lesson rates and splits, lucrativeness (estimated savings per season) |
| Clients | Typical client profile (destination families, local kids programs, adaptive, race, freestyle, international, ultra high net worth privates) |
| Terrain and fit | Terrain character, which type of enthusiast and which type of professional the mountain suits |
| Operation size | Skiable acres, lifts, vertical, ski school staff estimate |
| Housing | Employee housing sites, rent, access rating from convenient to car centric, transit lines |
| Trail map | Interactive map drawn from OpenSkiMap, plus a link to the resort's official trail map |

## 2. Design decisions (read before writing code)

1. **Cluster is the core unit, state is a filter.** Tahoe spans CA and NV. The I 70 corridor, Wasatch, Northern Vermont, Summit County, and the Cascades behave as labor markets regardless of state lines. Instructors choose clusters. Clicking a state highlights its clusters; clicking a cluster opens the cluster panel.
2. **Two tiers of resort data.** There are roughly 530 US ski areas and facilities. Deep profiles for all of them would never be finished or kept current.
   * **Tier A (deep):** resorts that meet the Tier A criteria in Section 3. Full career, housing, commute, and editorial data.
   * **Tier B (auto):** every other ski area. Location, size, climate, season length, rent baseline. Generated from open data.
3. **Every number carries a source, a date, and a confidence level.** Pay and rent go stale yearly. The UI shows "last verified" and a confidence badge. This is the credibility moat; the map itself is easy to copy.
4. **Highest confidence wins, per field.** For each field, display the best tier available: Published, then Reported (aggregated), then Estimated. Lower tiers are kept in the data but not displayed when a higher tier exists.
5. **Individual reports are never displayed.** Crowdsourced pay and rent appear only as aggregates of 3 or more reports. A single report from a small ski school is traceable to a person.
6. **Derived scores are transparent and fixed.** Lucrativeness is a model. The UI shows the result as a range plus the stated assumptions behind it. No adjustable inputs in this version.
7. **Static first.** All data compiles to versioned JSON, GeoJSON and PNG rasters at build time. Report submissions go to a hosted form (Formspree); there is no backend.
8. **Editorial text is never invented by the model.** Claude Code may draft culture and economy overviews only from cited sources, and every draft is marked `status: draft` until Alexandre reviews it.
9. **Heat maps show smoothed estimates between resorts.** Colors between resorts are interpolated for display only. Tooltips, panels and pages show only sourced values.
10. **Official trail maps are linked, never copied.** Resort trail map art is copyrighted. The site links to the resort's own file and draws its own interactive map from OpenSkiMap.

## 3. Tier A criteria

A resort qualifies for Tier A when it meets **at least one** of these:

* **Diehard following:** a core local and traveling skier culture that defines the mountain's identity
* **Luxury:** a destination clientele with high private lesson demand and premium pricing
* **Athlete development:** a ski and snowboard academy, club program feeding national teams, or regular national team training presence
* **Heritage:** historic significance to American skiing or snowboarding
* **Operator compensation:** every US resort owned or operated by Vail Resorts or Alterra Mountain Company. Vail compensation packages are top of the industry and standardized across locations. Alterra compensation is set location by location; all Alterra resorts qualify by owner decision
* **Owner selection:** a resort the owner adds to Tier A directly. The record cites the decisions log and carries any other sourced criteria
* **Year round training:** indoor snow slopes and year round training facilities (summer glacier camps, water ramps, airbag and trampoline centers, national team centers). These appear on the map with their own facility type: `ski_area`, `indoor`, or `training_center`

### Seed list
1. **All Vail Resorts US resorts**, regional hills included (operator compensation criterion).
2. **All Alterra Mountain Company US resorts** (operator compensation criterion).
3. **Independents and other operators meeting the criteria.** Starting candidates: Jackson Hole, Aspen Snowmass, Telluride, Taos, Sun Valley, Big Sky, Alta, Snowbird, Sugarloaf, Sugar Bowl, Burke, Waterville Valley.

### Rules
* Ownership changes often. Verify current Vail and Alterra US portfolios from the companies' own sites or filings each build cycle; never hardcode ownership from memory.
* Each Tier A resort records which criteria it meets and a source for each.
* Alexandre approves the list. The current list of 116 is approved (Section 10).

## 4. Stack (as built)

* **Site:** hand built static HTML on GitHub Pages from the repository root. No site build step. `_config.yml` keeps repository only material out of the published site.
* **Placement:** `instructorgrindset.com/map/` (subfolder, so search authority from resort pages flows to the whole domain and the consultation funnel). Map JavaScript and vendored libraries load only on `/map/` routes; the homepage is unaffected.
* **Map frontend:** native ES modules in `/map/js`, with vendored MapLibre GL 5.24.0, Zod 3.23.8 and Chart.js 4.5.1 in `/map/vendor`. A 2D canvas preview paints first; the WebGL map starts on click or tap.
* **Basemap:** USGS The National Map imagery (public domain), muted, under the data layers. Readiness never waits on imagery tiles.
* **Data pipeline:** Python with Pydantic 2 in `/pipeline`, output to `/data/build` (JSON, GeoJSON, heat rasters as PNG) plus prerendered route stubs under `/map/`.
* **Validation:** Pydantic in the pipeline, Zod in the frontend.
* **Routing:** precomputed at build time where data exists. Commute is shown as a researched access rating.
* **Reports:** Formspree form, imported with `python -m pipeline.reports import`.
* **Tests:** pytest for every transform and formula, a Playwright browser test, axe accessibility checks, Lighthouse runs.

## 5. Data sources

| Data | Primary source | Notes |
|---|---|---|
| Ski area locations, boundaries, lifts, runs | OpenSkiMap (OSM derived GeoJSON, ODbL) | Lifts and runs drive the interactive trail map |
| Acres, vertical, season dates | Resort websites, Wikipedia infobox, OnTheSnow and skiresort.info via search results | Values seen only in a search result are `estimated` with the note "From a search result for this page; page not opened" |
| Resort ownership | Vail Resorts and Alterra official sites and filings | Verified each build cycle |
| Monthly climate | Open Meteo historical API (ERA5) for temperature and wind; NRCS SNOTEL SWE for mountain snowfall | ERA5 snowfall undercounts mountains, so it is used only for low relief resorts. `docs/map/climate-validation.md` nulls any resort more than 50% off its published snowfall |
| Snowfall heat map | NOAA NCEI U.S. Climate Normals 1991 to 2020 (AWS Open Data mirror), plus each resort's sourced annual snowfall as a local pocket | Public domain |
| Snow density and storm depth | NRCS SNOTEL (AWDB API) | Nearest station within a set elevation band |
| Rent baseline | HUD Fair Market Rents, Census ACS median gross rent | Blocked from the build environment so far (Section 8, item 9) |
| Employee housing sites | Resort HR and careers pages, housing authority sites | Building level only when publicly listed, otherwise neighborhood level |
| Transit | GTFS feeds (Summit Stage, RFTA, Park City Transit, TART, ECO Transit, etc.) | No extracts committed yet |
| Pay | Job postings in pay transparency states, resort pay sheets, crowdsourced reports | Never scrape Glassdoor or Indeed |
| Local economy | BEA county data, BLS QCEW leisure and hospitality share, Census | Cited in overviews |
| Official trail maps | The resort's own domain or its operator's domain | Linked only |

## 6. Data model

```
State        { code, name, cluster_ids[] }
Cluster      { id, name, states[], polygon, resort_ids[], overview_md, sources[] }
Resort       { id, name, tier, facility_type, tier_a_criteria[], owner, lat, lon, cluster_id, state,
               size: { acres, vertical_ft, lifts, ski_school_staff_est },
               season: { open_typical, close_typical, length_days, training_start },
               snow: { annual_avg_in },
               terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags[] },
               fit: { enthusiast_types[], professional_types[] },
               clients: { profile_tags[], notes_md },
               access: { rating: convenient|transit_friendly|mixed|car_centric, summary_md },
               official_trailmap: { url, format, season },
               career: { pay_display, private_rate, private_split,
                         lucrativeness: { low, mid, high, assumptions[] } },
               editorial: { overview_md, culture_md, alexandre_take_md, status: draft|reviewed },
               sources[] }
ClimateMonth { resort_id, month, snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               density_pct_median, density_mix: { dry, average, moist },
               density_source: snotel|estimated, snowfall_source: snotel_swe|era5,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
HousingSite  { id, resort_id, name, type: resort_owned|master_lease|market,
               lat, lon, precision: building|neighborhood,
               rent_monthly_low, rent_monthly_high, occupancy, deposit,
               confidence, verified_date, sources[] }
PayRecord    { resort_id, season, cert_level, hourly_low, hourly_high,
               confidence: published|reported|estimated, source, verified_date }
Report       { id, resort_id, kind: pay|rent|housing, season, values, status: pending|approved|rejected }
               (raw crowdsourced input; never rendered directly)
Source       { id, url, title, accessed_date }
```

Internal only: `operator_scale` stays in `data/raw/tier_a` as a research note and is never written to `data/build` (Section 10).

### Definitions (all values live in `config/definitions.yaml`)
* **Powder day:** 6 in or more new snow in 24 hours
* **Banner day:** 12 in or more new snow in 24 hours
* **Density classes (water content of new snow):** dry under 7%, average 7 to 12%, moist over 12%
* **Season length:** median of recent seasons' open to close where data exists, else computed from sourced typical open and close dates
* **Display priority:** Published > Reported (3+ approved reports, shown as range) > Estimated
* **Lucrativeness:** season savings before tax = top of the posted wage scale × 40 h × season months × 4.33 weeks + tips, minus average rent × season months. Low uses the bottom of the scale. Assumptions are stated in config and shown in the UI
* **Heat maps:** ramp, bins, reach, fade and pocket radius live in the `heat` block

## 7. Phases (completed)

Each phase lists what shipped and what is still open. Open items are scheduled in Section 8.

### Phase 0: Audit and setup ✅
* Shipped: site audit, `/map/` route inside the site layout and sidebar, `CLAUDE.md`, `_config.yml` exclusions, scaffold of `/pipeline`, `/data/raw`, `/data/build`, `/config`, `/tests`, `docs/map/ARCHITECTURE.md` as the data contract.

### Phase 1: Data foundation ✅
* Shipped: OpenSkiMap US extract filtered to operating areas; 531 resorts (116 Tier A, 415 Tier B), 63 clusters, 37 states; Pydantic schemas that fail on a missing source; `config/definitions.yaml`; Vail and Alterra portfolios verified from their own sites and filings; Tier A list approved.
* Facilities match OpenSkiMap only through explicit `openskimap_names`.

### Phase 2: Map MVP ✅
* Shipped: national map with state outlines, cluster polygons and sized markers (Tier A distinct; indoor and training facilities have their own symbols); state, cluster and resort drill down; deep links such as `/map/co/i70-corridor/vail/`; phone bottom sheet; list view; 2D preview with click or tap to start; Alaska inset in the lower left with the lower 48 centered; muted USGS satellite base.

### Phase 3: Climate engine ✅ (data partial)
* Shipped: ERA5 and SNOTEL ETL with resort ordered fetching, retry on transient errors, API key support; SNOTEL SWE snowfall method; validation report; monthly charts; compare view.
* Open: only 9 of 116 Tier A resorts have monthly climate. Open Meteo's shared free limit blocks the rest (Section 8, item 10). A scheduled retry runs daily.

### Phase 4: Career layer ✅ (data partial)
* Shipped: pay records for 64 resorts with confidence badges; private rate fields; lucrativeness model with stated assumptions; client and fit tags; Color by modes with continuous heat maps (snowfall from NOAA normals with resort pockets, season length).
* Open: lucrativeness shows insufficient data until rent lands (Section 8, item 9). Professionalism was removed from the public site.

### Phase 5: Housing and commute ✅ (data partial)
* Shipped: housing, rent, transit and routing engines; lift lines and the interactive trail map dialog for 110 Tier A resorts (OpenSkiMap runs with US trail sign colors); official trail map links for 96 Tier A resorts; access ratings for 48 Tier A resorts.
* Open: access ratings for the other Tier A resorts, GTFS extracts, rent data.

### Phase 6: Editorial layer ✅
* Shipped: overviews for every named cluster and for 115 of 116 Tier A resorts, all published by owner decision. MetroSlopes has no overview yet.

### Phase 7: Crowdsourcing and freshness ✅
* Shipped: report form posting to Formspree, CSV import, moderation, aggregation that needs 3 or more approved reports, staleness flag at 18 months, regression tests.

### Phase 8: SEO and polish ✅
* Shipped: 681 prerendered route stubs, a trimmed sitemap of 230 URLs, links to the Free Consultation call, Lighthouse Performance 92 to 97 and 100 on Accessibility, Best Practices and SEO, axe with 0 violations, keyboard navigation.
* Analytics skipped by owner decision.

### Phase 9: Shared calculator infrastructure ✅
* Shipped: shared season income module in `assets/js/season-income.mjs` and `pipeline/season_income.py`, with 105 golden vectors proving identical results.

### Facts research (cross phase) ✅ (data partial)
* Shipped: typical open and close dates for 329 of 531 resorts, terrain mix for 74, annual snowfall for 274, acres and vertical gap fill from Wikipedia. Most season dates are `estimated` from search results.
* Open: about 200 resorts without both dates, no staff training start dates yet, a verification pass that opens the cited pages.

## 8. Build queue (what comes next, in order)

Work the queue top to bottom. Items in the same stage can run in parallel. An item marked **Owner** waits on Alexandre. Lane **R** is research and writes only to `/data/raw` with sources. Lane **C** is code. Lane **R+C** is research first, then code.

| # | Stage | Item | Lane | Depends on | Done when |
|---|---|---|---|---|---|
| 1 | 1 | Heat maps v2: remove professionalism everywhere public and from `data/build`; snowfall with grey for little or no snow and most of the contrast in 180 to 900 in (50 in steps readable, isolines every 50 in); season length precomputed as rasters with higher contrast and a clean fade; Alaska covered | C | none | Done (03dbf8d): professionalism removed and tested; snowfall classes with grey under 24 in and isolines every 50 in; season length rasters, ready in 0.7 to 2.1 s |
| 2 | 1 | Official trail map link checker: a GitHub Actions job (open network) that checks every `official_trailmap` URL monthly and opens an issue for broken links; fill the 11 missing resorts | R+C | none | Done: checker in 8b (877b6ab); 102 of 107 eligible Tier A ski areas linked. Yellowstone Club and Hermitage Club publish no public map; Alta winter, Burke and Saskadena Six not found |
| 3 | 1 | Resort search box: type ahead over resort and cluster names, keyboard accessible, works in map and list view | C | none | Done: map/js/search.js loads on first focus; works in map and list view |
| 4 | 1 | Season facts verification and gap fill: open cited pages where the network allows and upgrade confidence; fill about 200 resorts missing dates | R | none | Gap fill done (3ace635): 409 of 531 resorts have both dates; the rest are small club, town or private hills that publish none. Still open: verification pass that opens cited pages when the network allows |
| 5 | 1 | Access ratings for the remaining Tier A resorts | R | none | Done (d5e91fa): every Tier A ski area rated except MetroSlopes, which waits for its location |
| 6 | 1 | Staff training start and hiring windows: when applications open and when clinics start, with the jobs page URL | R | none | Partial (e34c046): training start for 13 Tier A resorts; Vail Resorts publishes none. Hiring windows for 27 resorts handed to item 8b |
| 7 | 1 | Pay follow ups: Big Sky, Killington, Gunstock, Woodward Park City, Burke, private lesson rates | R | none | Done (e34c046): Big Sky and Woodward Park City pay, Schweitzer, 7 private rates; Killington, Gunstock and Burke publish no wage |
| 8 | 1 | Sources and credits page: OpenSkiMap (ODbL), NOAA, USGS, Wikipedia, Formspree privacy note | C | none | Done: /map/credits/ linked from the map attribution, the panel footer and every stub |
| 8a | 1 | Live webcam links: each resort's own cams page plus named cams, linked in a new tab, never embedded | R+C | none | Done (877b6ab): cams for 97 Tier A and 112 Tier B resorts; no cams found for 19 Tier A, mostly facilities |
| 8b | 1 | Jobs: ski school, coaching and club (race, freeride, freestyle, nordic, adaptive), management and parent company careers links per resort; daily live postings from public job board APIs through GitHub Actions; monthly link checker for trail map, webcam and jobs URLs (absorbs item 2's checker) | R+C | none | Done (877b6ab): 383 links for 108 Tier A resorts across 75 employers, 27 hiring windows, daily feed for public boards (Aspen, Middlebury), monthly link checker. Needs merge and Actions write permission to run |
| 9 | 2 | **Owner:** rent data. Allow `www.huduser.gov` plus a `HUD_API_TOKEN` secret, or drop `data/raw/rent/hud_fmr_2026.csv` (fips, countyname, fmr_0, fmr_1, fmr_2). Then turn on lucrativeness and its heat map | R+C | Owner | Lucrativeness computed for every resort with pay; Color by Lucrativeness enabled |
| 10 | 2 | **Owner:** climate completion. Add an `OPEN_METEO_API_KEY` secret; the scheduled retry finishes the run | C | Owner | 116 of 116 Tier A with monthly climate that passes validation |
| 11 | 2 | **Owner:** launch. Owner says "open the PR"; review, merge, then check live: USGS imagery and CORS, Lighthouse on the real host, sitemap submitted to Search Console | C | 1, Owner | Map live at instructorgrindset.com/map/ with green checks |
| 12 | 3 | Epic and Ikon pass filter: pass membership from the pass companies' own sites, then a filter chip | R+C | 11 | Filter works in map and list view; every membership sourced |
| 13 | 3 | Hiring window display on resort pages and a "hiring now" filter | C | 6 | Shows only sourced windows |
| 14 | 3 | SNOTEL mountain stations in the snowfall heat map, in addition to resort pockets | C | 1 | Done: 831 SNOTEL stations, terrain aware field, pockets 10 km; validation in docs/map/climate-validation.md |
| 15 | 3 | Share images per resort page (static PNG built by the pipeline) | C | 11 | Every resort stub has an og:image |
| 16 | 3 | GTFS transit extracts for Tier A clusters (Summit Stage, RFTA, Park City Transit, TART, ECO Transit) | R+C | Done: 20 agencies, 30 Tier A detail maps, schedule dates shown; Park City, Jackson, Big Sky, Whitefish, Taos and Mt Hood feeds are not in the reachable catalog; ECO, Crested Butte, Tahoe TD and Mountain Rides feeds are stale |
| 17 | 3 | Yearly ownership re-verification job for Vail and Alterra | C | 11 | Build fails when a portfolio source is older than one build cycle |
| 18 | 3 | Real device QA after launch: phones and tablets, Lighthouse and axe on the live site | C | 11 | Issues filed and fixed |
| 18a | 1 | Map page performance: make Lighthouse Performance on /map/ reliably 90+ (median 95+); split the 2D preview long task, fix late paint | C | none | Done: /map/ 98 [98 to 99], Vail 97, cluster 98. Still open: the 11 resorts with climate charts score 87 |
| 18b | 1 | Mammoth base point: the record uses the Wikipedia article coordinate (37.63056, -119.0325), about 2.3 km from Main Lodge (37.6514, -119.0373); move it to a sourced base area point | R | none | Done (96a0b43, 814ae2e): Mammoth plus 14 more points moved to their base area lift (Jackson Hole, Whiteface, Magic, Mount Snow, Sugarloaf, Taos, Hunter, Whitefish, Dollar, Alta by named base; Jay Peak, Burke, Crotched, Bohemia by lowest lift); Mount Snow gains MOOver routes |
| 19 | ongoing | **Owner:** MetroSlopes exact address when the facility opens; import Formspree reports each month | R | Owner | Dot moved; reports aggregated |

## 9. Working with Claude Code

* Start each session with: "Read ROADMAP.md and CLAUDE.md. Take item N from Section 8." Plan first, then build.
* **One item per agent.** Mark the item "In progress" in Section 8 when you start and "Done" with the commit hash when you finish.
* **Research agents** write only to `/data/raw` in their own batch file (for example `data/raw/facts/<batch>.yaml`). Never edit another batch's file; put overlaps in your own file or a sidecar for the coordinator to merge.
* **Code agents** update `docs/map/ARCHITECTURE.md` for every contract change and add a test for every transform and formula.
* **Validate before handing back:** `python pipeline/build.py`, `python -m pytest -q`, the browser test (`tests/map/browser_test.mjs`) and axe for UI changes, Lighthouse for performance sensitive changes.
* **Commits:** the coordinator commits and pushes. Commit after each completed acceptance criterion.
* **Network notes for this environment:** Wikipedia via `index.php?action=raw`, NRCS AWDB and the NOAA normals AWS mirror are reachable. Resort sites, OnTheSnow, NCEI, HUD and Census are blocked. WebFetch fails and Composio is unavailable. WebSearch works but has a shared cap of about 200 searches per turn across all agents, so batch queries.
* **Search result rule:** a value seen only in a search result that names its page is recorded as `estimated` with the note "From a search result for this page; page not opened". Never cite a search engine's own summary.
* **Privacy:** never put the owner's email in a request; never ask for keys in chat (they go in environment secrets); never publish the MetroSlopes location beyond the placeholder.

## 10. Decisions log

| Question | Decision |
|---|---|
| Tier A membership | Criteria based (Section 3); every Vail and Alterra US resort plus qualifying independents and facilities |
| Placement | Subfolder `/map/` on instructorgrindset.com |
| Powder and density thresholds | Research based defaults: powder 6 in, banner 12 in, density cutoffs 7% and 12% |
| Crowdsourced data | Highest confidence per field; reports shown only as aggregates of 3+ |
| Season Income Calculator | Shared infrastructure (Phase 9); not visualized in the map |
| Owner additions (2026-10-11) | Big Sky, Taos, Snowmass, Buttermilk, Sun Valley, Dollar Mountain (its own record), Yellowstone Club and Deer Valley are approved Tier A. Indoor skiing and year round training facilities are included through the year round training criterion. Every Vail and Alterra US resort is approved Tier A through the operator compensation criterion |
| Owner review of candidates (2026-10-11) | Grand Targhee and Mt Hood Meadows are approved Tier A (owner selection). Mt Rose, Loon, Bretton Woods and Smugglers' Notch stay Tier B |
| Tier A approval (2026-10-11) | Owner approved every drafted Tier A record (independents, facilities, western and eastern candidates). The Section 3 approval gate is cleared; Phase 4 data collection can begin |
| Editorial publication (2026-10-13) | Every editorial draft is published as is: status reviewed, reviewed_by "site owner", reviewed_date 2026-10-13 |
| Public owner phrases (2026-10-13) | Keep "compensation packages are top of the industry ... (owner assessment)" and "Approved by site owner" |
| Lucrativeness model (2026-10-13) | Season savings before tax = top of the posted wage scale x 40 h x season months x 4.33 weeks + tips, minus average rent x season months. Season months from sourced length / 30.4, else 5. Low uses the bottom of the scale. Tips only when sourced or 3+ reports ("Tips are not included yet"). Rent: employee housing mean, else ACS median, else HUD FMR one bedroom. No other offsets |
| Professionalism (2026-10-13) | First an operator scale 1, 2, 3 shown as "N of 3". Later the same day: **removed from the public site** (owner decision; low confidence in the rating and risk of offending resorts). `operator_scale` stays in `data/raw` as an internal note and never reaches `data/build` |
| Commute (2026-10-13) | No walking speed or distance assumptions; each Tier A resort gets a researched access rating: Convenient, Transit friendly, Mixed, Car centric, with one short sentence |
| Map start (2026-10-13) | The interactive WebGL map starts on first click or tap over the 2D preview ("Tap to explore the map"), keyboard accessible |
| Rooftop fonts (2026-10-13) | Remove the broken @font-face rules; headings keep the Inter fallback |
| Report submissions (2026-10-13) | Reports go to the site's existing Formspree form (https://formspree.io/f/xyzbdqgq); `python -m pipeline.reports import` reads its CSV export |
| Season facts sourcing (2026-10-13) | Search results that name their page may supply season facts as `estimated`, with the note "From a search result for this page; page not opened" |
| Basemap (2026-10-13) | Muted USGS The National Map imagery under the data; public domain; readiness never waits on tiles |
| Alaska (2026-10-13) | Alaska draws as a smaller inset in the lower left; the lower 48 stays centered |
| Heat maps (2026-10-13) | Real heat maps that cover the map, not highlighted resorts. Snowfall uses NOAA NCEI 1991 to 2020 station normals with a pocket for each resort's sourced snowfall. Interpolated colors are never shown as numbers |
| Snowfall contrast (2026-10-13) | Most contrast goes to 180 to 900 in, where people actually ski; a 50 in difference must be easy to read. Little or no snow shows as grey |
| Official trail maps (2026-10-13) | Link to the resort's own trail map in a new tab; never rehost or embed copyrighted maps. The OpenSkiMap drawing stays as the interactive map |
| Webcams and jobs (2026-10-13) | Link live cams and job pages for every resort that has them: ski school, coaching and clubs, management, parent companies and job boards. Live posting counts come only from public job board APIs; never scrape Indeed, Glassdoor or Workday pages |
| MetroSlopes (2026-10-13) | MetroSlopes Next Gen Ski Lab is Tier A (owner selection). Until it opens, its dot is a regional placeholder at SFO and no street or city is published |
