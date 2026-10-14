# Transit (Phase 5, ROADMAP Section 8 item 16)

GTFS schedule feeds for agencies serving Tier A clusters. `pipeline/transit.py` keeps the stops within `transit.search_radius_km` of any Tier A base area or housing site and the routes serving them, and writes a small committed extract per agency. The resort detail map draws those routes and stops.

## Files

* `feeds.yaml` (committed): one entry per agency.
  ```yaml
  feeds:
    - agency: summit-stage                 # slug; the source id is gtfs-<agency>
      title: Summit Stage GTFS
      mdb_source_id: 820                   # Mobility Database catalog id
      url: http://data.trilliumtransit.com/gtfs/summitstage-co-us/summitstage-co-us.zip   # the agency's own feed
      mirror_url: https://storage.googleapis.com/storage/v1/b/mdb-latest/o/us-colorado-summit-stage-gtfs-820.zip?alt=media
      license: null                        # urls.license from the catalog; null when it lists none
      agency_ids: ["904"]                  # optional: keep only these GTFS agencies of a shared feed
  ```
  Take every field from the Mobility Database catalog `sources.csv` (`urls.direct_download`, `urls.latest`, `urls.license`), at `https://storage.googleapis.com/download/storage/v1/b/mdb-csv/o/sources.csv?alt=media`.
* `<agency>/gtfs.zip` (gitignored): the downloaded feed.
* `<agency>.json` (committed): the extract (`TransitExtract` in `pipeline/schemas/housing.py`), one route, stop or pattern per line:
  * `source`: `gtfs-<agency>` with the agency's feed URL, the download date, and a note naming the catalog entry, the mirror, the feed's validity dates and the license.
  * `feed`: the catalog fields above plus the feed's own `version`, `valid_from`, `valid_to`, `validity_basis` and `as_of` (the start of the validity period).
  * `routes` with `MultiLineString` geometry (from `shapes.txt`, else the stop sequences, simplified to `transit.simplify_tolerance_m`), `stops` with the routes serving them, and stop `patterns` with median scheduled minutes from the first kept stop.

## Feed currency

The validity period comes from `feed_info.txt` (`feed_start_date`, `feed_end_date`), else from the earliest `calendar.txt` start and the latest end, plus dates added in `calendar_dates.txt`. A feed is current when that period overlaps `transit.target_season` (2026 to 27: Nov 1 to Apr 30), or when it starts within `staleness.max_age_months` (18) of the build date. A period longer than `transit.max_validity_years` (3) is an open ended placeholder, such as a calendar running to 2099, and does not count as covering the season. `extract` skips feeds that are not current, and the build drops an extract once its feed goes stale, with a warning. Move `target_season` forward each year.

## Commands

The agency hosts, transit.land and the Mobility Database API are blocked from the build environment. Its Google Cloud Storage mirror is reachable, so `fetch` tries `mirror_url` first. Requests use the pipeline's generic User Agent and never an email address.

```bash
python pipeline/build.py                 # resorts.json must exist (anchors are Tier A base areas)
python -m pipeline.transit fetch         # downloads every feed in feeds.yaml
python -m pipeline.transit check         # prints each feed's validity and whether it is current
python -m pipeline.transit extract       # writes <agency>.json for current feeds with stops near a Tier A base
python pipeline/build.py                 # adds transit to data/build/detail/<id>.json
```

## Coverage (researched 2026-10-13, catalog updated 2026-10-13)

| Agency | Catalog id | Feed valid | Tier A detail maps |
|---|---|---|---|
| Summit Stage | 820 | 2026-04-02 to 2026-11-14 | Arapahoe Basin, Breckenridge, Copper Mountain, Keystone |
| Breckenridge Free Ride | 237 | 2025-11-19 to 2026-04-12 | Breckenridge |
| Vail Transit | 174 | 2026-05-21 to 2026-12-06 | Vail |
| Avon Transit (CC BY 4.0) | 2433 | 2026-03-31 to 2026-11-25 | Beaver Creek |
| Winter Park The Lift | 175 | 2026-05-23 to 2026-11-14 | Winter Park |
| Bustang (CDOT) | 2280 | 2026-01-04 to 2026-06-06 | Beaver Creek, Vail |
| Bustang Outrider | 2330 | 2026-01-29 to 2026-08-01 | Howelsen Hill |
| RFTA | 2278 | 2026-06-01 to 2026-09-20 | Aspen Highlands, Aspen Mountain, Buttermilk, Snowmass |
| Snowmass Village Shuttle | 164 | 2026-05-27 to 2026-09-20 | Snowmass |
| Steamboat Springs Transit | 2051 | 2026-04-30 to 2026-11-27 | Howelsen Hill, Steamboat |
| Telluride, Mountain Village and SMART (Galloping Goose) | 2195 | 2026-06-02 to 2026-10-19 | Telluride |
| Gunnison Valley RTA | 163 | 2025-11-26 to 2026-11-25 | Crested Butte |
| UTA (terms of use in the catalog) | 170 | 2026-05-24 to 2026-06-20 | Alta, Snowbird |
| TART | 94 | 2026-03-30 to 2026-06-30 | Northstar, Palisades Tahoe, Sugar Bowl |
| Mountain Transit (Big Bear) | 107 | 2026-02-20 to 2026-07-01 | Bear Mountain, Snow Summit, Snow Valley |
| Green Mountain Transit | 629 | 2026-04-24 to 2026-06-28 | Sugarbush |
| Marble Valley RTD (The Bus) | 430 | 2026-03-25 to 2026-08-31 | Killington, Okemo, Pico |
| Southeast Vermont Transit MOOver | 2194 | 2026-03-23 to 2026-06-22 | Okemo, Stratton |
| Tri Valley Transit | 852 | 2026-05-27 to 2026-08-01 | Middlebury Snow Bowl |
| Greene County Transit (NY open data license) | 613 | 2025-11-17 to 2027-01-01 | Hunter Mountain |

Most current feeds carry a summer or spring schedule, so winter only ski routes may be missing until the agencies publish winter feeds. The detail map says so: its legend gives each feed's agency (linked to the site in agency.txt) and schedule dates, with "Offseason schedule. Winter ski service can differ; check the agency." for a feed that overlaps Dec 1 to Mar 31 (`transit.winter_window`) by fewer than 30 days (`winter_min_overlap_days`), and "Last season's schedule. This winter's service can differ; check the agency." for a winter feed that ended before the build date. Extracts carry each route's `agency_name` and `agency_url`. UTA's summer feed runs route 994 to Snowbird and Alta; its winter ski buses (972 to Solitude and Brighton, 674 to Powder Mountain, 675 and 677 to Snowbasin) have no trips in it. Refresh before the season.

## Checked and left out

| Feed (catalog id) | Feed valid | Reason |
|---|---|---|
| ECO Transit (2061, 173) | 2023-01-23 to 2023-04-15 | Stale. Its successor Core Transit (2045) carries only paratransit |
| Mountain Express, Crested Butte (581) | 2022-06-01 to 2023-06-05 | Stale |
| Tahoe Transportation District (897) | 2024-06-29, calendar to 2099 | Stale; the end date is a placeholder (Heavenly) |
| Mountain Rides, Sun Valley (2282, 143) | 2023-01-16, calendar to 2050 | Stale; the end date is a placeholder |
| UTA winter feed (2349) | 2024-12-08 to 2025-04-12 | Stale; it had the ski buses to Snowbasin, Powder Mountain, Brighton and Solitude |
| NCRTD, Taos (582) | 2021-05-11 to 2021-07-31 | Stale and inactive |
| Cascades East Transit (138), Mt. Bachelor (933) | 2023-12-11 to 2024-04-23; 2022-09-02 to 2023-07-01 | Stale |
| Franklin County, NY (545) | 2024-08-02 to 2026-08-31 | Stale (Lake Placid) |
| Telluride (603), SMART (811), Mountain Village (594), RFTA (162), Bustang (169), Wilmington MOOver (434), Adirondack Trailways (826) | 2021 to 2023 | Stale; current feeds 2195, 2278, 2280 and 2194 replace most of them |
| Streamline, Bozeman (295) | 2025-01-23 to 2028-12-31 | Placeholder end date and older than 18 months; no stop near Bridger Bowl either |
| Eastern Sierra Transit (25) | 2026-01-01 to 2026-12-31 | Current, but no stop within 2 km of the Mammoth or June base points (the Main Lodge stop is 2.2 km from the Mammoth point) |
| RCT (448), Clackamas County (2740) | 2026 | Current, but no scheduled stop within 2 km of a Tier A base (Burke; the Mount Hood Express is not in the Clackamas feeds) |
| North Lake Tahoe Express (2328), Epic Mountain Express (172), Blue Sky Limo (2616) | 2026 to 2027 | Airport shuttles by reservation, not local transit |
| RFTA flex (2179), Steamboat Yellow Zone, Core Transit (2045) | 2026 | On demand or paratransit only |

Not in the catalog at all: Park City Transit, High Valley Transit, START Bus (Jackson), Skyline (Big Sky), the Whitefish SNOW Bus, the Taos Chile Line and the Mount Hood Express. They need the agency's own feed URL once its host is reachable.

## Bus commute model

Walk from the housing site to a stop within `transit.max_walk_km`, ride a stop pattern to a stop within `max_walk_km` of the base area, then walk to the base. Minutes = walking at `transit.walk_speed_kmh` + scheduled ride (median over trips), with no waiting time. The fastest option is shown. Both settings are `null` (owner decision 6: no walking model), so bus commutes stay empty.
