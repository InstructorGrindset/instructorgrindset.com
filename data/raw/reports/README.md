# Crowdsourced reports (Phase 7)

Pay, rent and housing reports from instructors. Individual reports are private: they live only in `queue/` and `moderated/`, which are gitignored, and never reach `data/build`. Only aggregates of 3 or more approved reports (`reports.aggregation_min`) are published, through the committed `aggregates.json`.

## Folders

| Path | Content |
|---|---|
| `queue/<id>.yaml` | pending reports, one per file (gitignored) |
| `moderated/<id>.yaml` | approved or rejected reports (gitignored) |
| `aggregates.json` | aggregates only, written only by `python -m pipeline.reports aggregate` (committed) |

## Commands

```bash
python -m pipeline.reports import export.csv      # or export.json, from the form backend
python -m pipeline.reports list
python -m pipeline.reports approve r-1a2b3c4d5e6f --note "pay stub checked"
python -m pipeline.reports reject r-1a2b3c4d5e6f --note "season does not match"
python -m pipeline.reports aggregate               # merges into the committed aggregates.json
python -m pipeline.reports aggregate --replace     # rebuild it from this checkout's moderated reports only
python pipeline/build.py
```

## Formspree (owner decision 11)

Reports go to the site's existing Formspree form (`https://formspree.io/f/xyzbdqgq`, `submissions.endpoint` in `config/definitions.yaml`). Download the form's submissions as CSV from the Formspree dashboard and run `python -m pipeline.reports import <file>.csv`. Formspree writes one column per submitted field plus its own columns:

* Column names match case and space insensitively (`Resort ID`, `resort_id` and `resort id` are the same).
* The submission time is read from the first of `submitted_at`, `created_at`, `date`, `_date`, `submitted`, `timestamp`, `time`, `received_at`; ISO, `2026-10-13 14:05:22 UTC`, `10/13/2026 14:05` and `Oct 13, 2026 2:05 PM` style dates are understood. A row with an unreadable date is reported and skipped.
* Formspree metadata (`_subject`, `email`, `_replyto`, ...) and any unknown column are ignored, so no email address reaches the report files.

## Import columns

A CSV with these headers, or a JSON list of objects with the same keys (also accepted: `{"reports": [...]}`):

| Column | Required | Notes |
|---|---|---|
| `id` | no | generated from the content when missing (`r-` plus 12 hex characters) |
| `resort_id` | yes | a resort id from `data/build/resorts.json` |
| `kind` | yes | `pay`, `rent` or `housing` |
| `season` | yes | `2025-26` |
| `cert_level` | pay | `none`, `level_1`, `level_2`, `level_3`, `trainer`, `examiner` |
| `housing_site_id` | housing | a site id from `data/raw/housing` |
| `hourly_usd` | pay | number |
| `monthly_usd` | rent, housing | number |
| `proof_url` | no | `https://` link to proof the moderator can check |
| `submitted_at` | no | ISO datetime; defaults to the import time |

A JSON object may carry `values: {hourly_usd | monthly_usd}` instead of the two value columns. Any `status` in the export is ignored: every import starts as pending. Rows that fail validation or name an unknown resort are reported and skipped; re importing the same export adds nothing.

## Aggregation

Approved reports are grouped by resort, season and kind (plus cert level for pay, housing site for housing). A group with 3 or more reports gives a range from its lowest to highest value with the report count; 2 or fewer give nothing; pending and rejected reports never count. Pay aggregates become `reported` pay rows; housing aggregates become a site's reported rent when the site has no published rent; rent aggregates become the resort's `rent_reported` in its detail file.

`aggregate` merges with the committed file (the group with more reports wins), because moderated reports are gitignored and a checkout may hold only some of them. It refuses to write while any moderated report is invalid. The build never writes the file; it warns when local moderated reports would change it. Aggregates below `reports.aggregation_min` are ignored when loading.
