# Tier A research batches

Hand researched Tier A records (ROADMAP Section 3). One YAML file per research batch, for example `vail.yaml`, `alterra.yaml`, `independents.yaml`. `python pipeline/build.py` validates every file here against `pipeline/schemas/raw.py` (`TierABatch`) and fails on any problem. Unknown keys are an error.

Verified Vail and Alterra portfolios live next door in `data/raw/ownership/` (format below).

## Rules

* Every fact carries a source. A value you cannot source stays `null`. Never fill gaps from memory.
* Every `source_id` must be declared in the `sources` list of some file in `data/raw/tier_a/` or `data/raw/ownership/`. The same id may appear in several files only with the same URL.
* Every resort needs `lat`, `lon` (base area, decimal degrees) and `location_source_id`.
* Every resort needs at least one `tier_a_criteria` entry, each with a rationale and a source.
* `cluster_hint` must be a cluster id from `config/clusters.yaml` that lists the resort's state, or `other-<state>` (for example `other-mi`) for a place outside every named cluster.
* An owner containing "Vail" or "Alterra" must appear (same state, matching name, or `resort_id`) in that operator's ownership file, and every resort in an ownership file needs a Tier A record (warning for now; it becomes an error once research is complete).
* Shared sources live in `data/raw/sources/` (pipeline owned). Cite them by id without redefining them; `owner-decisions-2026-10-11` is the owner decisions log.
* Approval is per record. A record's `tier_a_status` overrides the batch `status`. Any record that ends up `approved` needs an `approval: {by, date, note}` block; a draft must not carry one.
* `facility_type` is `ski_area` (default), `indoor` or `training_center`. Only `ski_area` records are matched to OpenSkiMap.
* A record matches its single best OpenSkiMap area. When one record covers several mapped areas (Palisades Tahoe: Olympic Valley and Alpine Meadows), list every OpenSkiMap area name in `openskimap_names`.

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
      - {criterion: operator_compensation, rationale: "Vail Resorts US resort.", source_id: owner-decisions-2026-10-11}
    tier_a_status: approved          # optional; overrides the batch status
    approval: {by: Alexandre, date: 2026-10-11, note: null}   # required when approved
    snow:
      annual_avg_in: {value: <inches as the source states>, source_id: wikipedia-vail-ski-resort, as_of: 2026-10-11, confidence: published, note: resort reported average}
    size:
      acres: {value: 5317, source_id: wikipedia-vail-ski-resort, as_of: 2026-10-11, confidence: published, note: optional}
      vertical_ft: null
      lifts: null
      ski_school_staff_est: null   # optional
```

A facility record (no OpenSkiMap match and no size data is fine):

```yaml
  - id: some-indoor-slope
    name: Some Indoor Slope
    facility_type: indoor
    state: NJ
    lat: 40.81
    lon: -74.07
    location_source_id: some-source
    cluster_hint: new-york-metro
    tier_a_criteria:
      - {criterion: year_round_training, rationale: "Indoor snow slope open all year.", source_id: some-source}
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
| `cluster_hint` | yes | id from `config/clusters.yaml`, or `other-<state>` |
| `facility_type` | no | `ski_area` (default), `indoor`, `training_center` |
| `tier_a_status` | no | `draft` or `approved`; overrides the batch `status` |
| `approval` | when approved | `{by, date, note}`; `note` may be `null` |
| `openskimap_names` | no | OpenSkiMap area names this record covers when it spans several mapped areas |
| `tier_a_criteria` | yes, 1 or more | `criterion` is one of `diehard`, `luxury`, `athlete_development`, `heritage`, `operator_compensation`, `year_round_training`, `owner_selection` (owner decision; cite `owner-decisions-2026-10-11`) |
| `size` | no | `acres`, `vertical_ft`, `lifts`, `ski_school_staff_est`; each Sourced number or `null` |
| `elevation` | no | `base_ft`, `summit_ft`; each Sourced number or `null`. When null, the build fills them from OpenSkiMap (confidence `estimated`) |
| `snow` | no | `annual_avg_in`: Sourced number, the resort reported average annual snowfall in inches (for example the Wikipedia infobox "snowfall" field); omit or `null` when unsourced |
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
    resort_id: vail               # optional exact link to the Tier A record
  - name: Some Regional Hill
    state: PA
    source_id: vail-resorts-our-resorts
    tier_a_candidate: true        # optional, kept for history
    exclusion_reason: null        # optional, kept for history
```

The ownership file must list the full current US portfolio from the company's own site or filings. Every entry needs a Tier A record (operator compensation); a missing one is listed under open items in `docs/map/tier-a-draft.md`.
