# Transit (Phase 5)

GTFS feeds for agencies serving Tier A resorts (Summit Stage, RFTA, Park City Transit, TART, ECO Transit, ...). `pipeline/transit.py` extracts routes and stops within `transit.search_radius_km` of any Tier A base area or housing site, and writes a small committed extract per agency.

## Files

* `feeds.yaml` (committed): the feed list.
  ```yaml
  feeds:
    - {agency: example-transit, url: https://example.org/gtfs.zip, title: Example Transit GTFS}
  ```
  Use the agency's own published GTFS URL. Each feed becomes the source `gtfs-<agency>`.
* `<agency>/gtfs.zip` (gitignored): the downloaded feed.
* `<agency>.json` (committed): the extract (`TransitExtract` in `pipeline/schemas/housing.py`): routes with `MultiLineString` geometry (from `shapes.txt`, else the stop sequences), stops with the routes serving them, and stop patterns with median scheduled minutes from the first kept stop.

## Commands (once the agency hosts are reachable)

```bash
python pipeline/build.py                 # resorts.json must exist (anchors are Tier A base areas)
python -m pipeline.transit fetch         # downloads every feed in feeds.yaml
python -m pipeline.transit extract       # writes <agency>.json
python pipeline/build.py                 # adds transit to data/build/detail/<id>.json
```

## Bus commute model

Walk from the housing site to a stop within `transit.max_walk_km`, ride a stop pattern to a stop within `max_walk_km` of the base area, then walk to the base. Minutes = walking at `transit.walk_speed_kmh` + scheduled ride (median over trips), with no waiting time. The fastest option is shown. Both settings are `null` until Alexandre sets them, so bus commutes stay empty until then.
