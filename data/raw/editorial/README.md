# Editorial (Phase 6)

Culture and economy overviews per cluster and Tier A resort, drafted only from cited sources. One YAML file per research batch. `python pipeline/build.py` validates every file against `pipeline/schemas/editorial.py` (`EditorialBatch`).

## Format

```yaml
batch: editorial-west
researched_date: 2026-10-12
sources:
  - {id: ed-west-wikipedia-example-town, url: https://en.wikipedia.org/wiki/Example, title: Example (Wikipedia), accessed_date: 2026-10-12}
entries:
  - target: resort              # or cluster
    id: example-resort          # an existing resort id or cluster id
    overview_md: "..."
    culture_md: "..."           # resort only; null for clusters
    alexandre_take_md: null     # Alexandre writes this
    status: draft               # draft | reviewed
    source_ids: [ed-west-wikipedia-example-town]
```

## Rules

* Every entry cites at least one defined source. Source ids are unique across `data/raw`.
* One entry per target and id across all files.
* Style lint (Alexandre's rules): an em dash or en dash fails the build. Hyphenated words ("full-time", "I-70") and negative definitions ("is not", "isn't") are warnings shown in `docs/map/editorial-review.md`.
* Only `reviewed` entries reach the map. Drafts never leave `data/raw`.

## Review

`docs/map/editorial-review.md` lists every draft with its text, sources and warnings. To publish an entry, set `status: reviewed` in its YAML, or run:

```bash
python -m pipeline.editorial approve resort example-resort --by Alexandre
python pipeline/build.py
```

The command flips the status in place and records `reviewed_by` and `reviewed_date`; comments and the rest of the file stay as they are.
