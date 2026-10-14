# Resort facts overlay

Season dates, season length, snowfall and size for every resort (Tier A and Tier B), one YAML file per research batch. `python pipeline/build.py` validates every file against `pipeline/schemas/facts.py` (`FactsBatch`) and fails on any problem.

## Format

```yaml
batch: facts-west
researched_date: 2026-10-13
sources:
  - {id: facts-west-example-season-page, url: https://example.org/season, title: Example Resort season dates, accessed_date: 2026-10-13}
records:
  - resort_id: example-resort          # a resort id in data/build/resorts.json
    open_typical: {value: "11-20", source_id: facts-west-example-season-page, as_of: 2026-10-13, confidence: published}
    close_typical: {value: "04-10", source_id: facts-west-example-season-page, as_of: 2026-10-13, confidence: published}
    length_days: {value: 142, source_id: facts-west-example-season-page, as_of: 2026-10-13, confidence: estimated, note: computed from typical dates}
    training_start: {value: "11-05", ...}       # optional
    annual_snowfall_in: {value: ..., ...}       # optional
    acres: {value: ..., ...}                    # optional, skiable acres
    vertical_ft: {value: ..., ...}              # optional
    lifts: {value: ..., ...}                    # optional
```

## Rules

* Dates are quoted `"MM-DD"` strings. Numbers are positive; `length_days` is at most 366.
* Every value is Sourced (`value`, `source_id`, `as_of`, `confidence`, optional `note`) and its source is defined in some file under `data/raw` (ids are global; prefix yours with the batch).
* `resort_id` must exist in the build, one record per resort across all facts files, and each record has at least one field.

## How values are merged

Each field goes to `Resort.season` (`open_typical`, `close_typical`, `length_days`, `training_start`), `Resort.snow.annual_avg_in` or `Resort.size` (`acres`, `vertical_ft`, `lifts`). Per field the higher confidence wins (published, then reported, then estimated); on a tie the value already on the resort (Tier A YAML, or OpenSkiMap for lifts and vertical) wins. `length_days` also feeds the map's season length colouring and the lucrativeness season months (days / 30.4).
