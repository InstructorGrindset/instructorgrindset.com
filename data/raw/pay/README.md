# Pay records (Phase 4)

Hourly instructor pay, one YAML file per research batch (for example `pay_transparency_co.yaml`). `python pipeline/build.py` validates every file against `pipeline/schemas/career.py` (`PayBatch`) and fails on any problem. Unknown keys are an error.

## Rules

* Every row has a source. Pay transparency job postings and published resort pay sheets come first. Never scrape Glassdoor or Indeed.
* A value you cannot source is left out. Never estimate pay from memory.
* `resort_id` must be a resort id in the build (Tier A or Tier B).
* `season` is written `2025-26`. `cert_level` is one of `none`, `level_1`, `level_2`, `level_3`, `trainer`, `examiner`, `unspecified`. Use `unspecified` for a single posted range that covers every level ("depending on certification and experience").
* `hourly_low` must not exceed `hourly_high`. A single published rate uses the same number twice.
* `confidence: reported` rows come only from the Phase 7 aggregation job and carry `aggregate_count` (approved reports combined). A reported row with fewer than `reports.aggregation_min` (3) reports is never displayed. Individual reports never go in this folder.
* Sources may be declared here or cited from any other file in `data/raw` (ids are global).

## How the map picks a row

For each cert level the build shows one row: the most recent season, then the highest confidence (published, then reported, then estimated), then the latest `verified_date`. A row verified more than 18 months before the build is flagged stale.

## Example (fictional resort and numbers, for format only)

```yaml
batch: example-postings
researched_date: 2026-10-11
sources:
  - {id: example-posting-2026, url: https://example.org/jobs/ski-instructor, title: Example Resort ski instructor posting, accessed_date: 2026-10-11}
records:
  - resort_id: example-resort
    season: 2026-27
    cert_level: level_2
    hourly_low: 20.00
    hourly_high: 24.00
    confidence: published
    source_id: example-posting-2026
    verified_date: 2026-10-11
    note: posted range for certified Level 2 instructors
```
