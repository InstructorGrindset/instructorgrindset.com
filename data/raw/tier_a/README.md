# Tier A research batches

Hand researched Tier A records (ROADMAP Section 3). One YAML file per research batch, for example `vail.yaml`, `alterra.yaml`, `independents.yaml`. `python pipeline/build.py` validates every file here against `pipeline/schemas/raw.py` (`TierABatch`) and fails on any problem. Unknown keys are an error.

Verified Vail and Alterra portfolios live next door in `data/raw/ownership/` (format below).

## Rules

* Every fact carries a source. A value you cannot source stays `null`. Never fill gaps from memory.
* Every `source_id` must be declared in the `sources` list of some file in `data/raw/tier_a/` or `data/raw/ownership/`. The same id may appear in several files only with the same URL.
* Every resort needs `lat`, `lon` (base area, decimal degrees) and `location_source_id`.
* Every resort needs at least one `tier_a_criteria` entry, each with a rationale and a source.
* `cluster_hint` must be a cluster id from `config/clusters.yaml`, and that cluster must list the resort's state.
* An owner containing "Vail" or "Alterra" must appear (same state, matching name) in that operator's ownership file.
* Batch `status` is `draft` until Alexandre approves it. Set `status: approved` to approve every resort in the batch; the build then emits `tier_a_status: approved`.

## Batch file

```yaml
batch: vail                      # kebab case slug
researched_date: 2026-10-11
status: draft                    # draft | approved
sources:
  - id: wikipedia-vail-ski-resort          # kebab case, unique
    url: https://en.wikipedia.org/wiki/Vail_Ski_Resort
    title: Vail Ski Resort (Wikipedia)
    accessed_date: 2026-10-11
    note: optional qualifier       # optional
resorts:
  - id: vail                     # kebab case, unique across all batches; used in URLs
    name: Vail
    state: CO                    # USPS code
    owner: {value: Vail Resorts, source_id: vail-10k-fy2026, as_of: 2026-10-11, confidence: published}
    lat: 39.64
    lon: -106.37
    location_source_id: wikipedia-vail-ski-resort
    cluster_hint: i70-corridor
    tier_a_criteria:
      - {criterion: luxury, rationale: "Short sourced reason.", source_id: wikipedia-vail-ski-resort}
    size:
      acres: {value: 5317, source_id: wikipedia-vail-ski-resort, as_of: 2026-10-11, confidence: published, note: optional}
      vertical_ft: null
      lifts: null
      ski_school_staff_est: null   # optional
```

### Fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | kebab case slug |
| `name` | yes | |
| `state` | yes | two letter USPS code |
| `owner` | no | Sourced string or `null` |
| `lat`, `lon` | yes | base area |
| `location_source_id` | yes | source for the coordinates |
| `cluster_hint` | yes | id from `config/clusters.yaml` |
| `tier_a_criteria` | yes, 1 or more | `criterion` is one of `diehard`, `luxury`, `athlete_development`, `heritage` |
| `size` | no | `acres`, `vertical_ft`, `lifts`, `ski_school_staff_est`; each Sourced number or `null` |
| `elevation` | no | `base_ft`, `summit_ft`; each Sourced number or `null`. When null, the build fills them from OpenSkiMap (confidence `estimated`) |
| `season` | no | `open_typical`, `close_typical`, `training_start` as Sourced `"MM-DD"` strings; `length_days` Sourced number |
| `terrain` | no | `beginner_pct`, `intermediate_pct`, `advanced_pct`, `expert_pct` Sourced numbers (0 to 100); `character_tags: []` |
| `fit` | no | `enthusiast_types: []`, `professional_types: []` |
| `clients` | no | `profile_tags: []`, `notes_md: null` |

### Sourced value

```yaml
{value: 5317, source_id: some-source, as_of: 2026-10-11, confidence: published, note: optional qualifier}
```

`confidence` is `published` (resort, company or official source), `reported` (aggregate of 3 or more approved reports) or `estimated` (derived or modeled).

## Ownership file (`data/raw/ownership/*.yaml`)

```yaml
operator: Vail Resorts
verified_date: 2026-10-11
sources:
  - {id: vail-resorts-our-resorts, url: https://www.vailresorts.com/..., title: ..., accessed_date: 2026-10-11}
resorts:
  - name: Vail
    state: CO
    source_id: vail-resorts-our-resorts
    tier_a_candidate: true
    exclusion_reason: null
  - name: Some Feeder Hill
    state: PA
    source_id: vail-resorts-our-resorts
    tier_a_candidate: false
    exclusion_reason: Regional feeder hill that meets no Tier A criterion.   # required when tier_a_candidate is false
```

The ownership file must list the full current US portfolio from the company's own site or filings. A `tier_a_candidate: true` entry without a matching Tier A record is reported as an open item in `docs/map/tier-a-draft.md`.
