# Career inputs (Phase 4)

Per resort career facts: private lesson price and split, professionalism components, and client and fit tags. One YAML file per research batch. `python pipeline/build.py` validates every file against `pipeline/schemas/career.py` (`CareerBatch`) and fails on any problem. Unknown keys are an error.

## Rules

* Every value is a Sourced object (`value`, `source_id`, `as_of`, `confidence`, optional `note`) or `null`. Never fill a gap from memory.
* `resort_id` must be a resort id in the build. One entry per resort across all career files.
* `private_rate` is the guest price: `value: {low, high, unit}` with `unit` one of `hour`, `half_day`, `full_day`.
* `private_split` is the instructor's share of the private lesson price, percent from 0 to 100.
* `professionalism_inputs` holds six components, each normalised to a number from 0 to 1 (1 is best): `cert_pay_differential`, `paid_training_hours`, `examiner_trainer_presence`, `private_lesson_share`, `year_round_staff`, `clinic_access`. Say how you normalised it in `note`.
* Tags must come from `config/definitions.yaml` `career.vocab`. Client profiles: `destination_families`, `local_kids_programs`, `adaptive`, `race`, `freestyle`, `international`, `uhnw_privates`. The enthusiast and professional lists are empty until Alexandre fills them, so leave those tags empty for now. Unknown tags fail the build.
* Weights for the professionalism score and the lucrativeness assumptions live in `config/definitions.yaml` and are set by Alexandre, never here.

## Example (fictional resort and numbers, for format only)

```yaml
batch: example-career
researched_date: 2026-10-11
sources:
  - {id: example-ski-school-page, url: https://example.org/ski-school, title: Example Resort ski school, accessed_date: 2026-10-11}
resorts:
  - resort_id: example-resort
    private_rate:
      value: {low: 500, high: 650, unit: full_day}
      source_id: example-ski-school-page
      as_of: 2026-10-11
      confidence: published
    private_split: null
    professionalism_inputs:
      cert_pay_differential: null
      paid_training_hours: {value: 0.5, source_id: example-ski-school-page, as_of: 2026-10-11, confidence: estimated, note: 20 paid hours, scaled against 40 hours}
      examiner_trainer_presence: null
      private_lesson_share: null
      year_round_staff: null
      clinic_access: null
    client_profile_tags: [destination_families, uhnw_privates]
    enthusiast_types: []
    professional_types: []
```
