# Housing sites (Phase 5)

Employee and market housing near Tier A resorts, one YAML file per research batch. `python pipeline/build.py` validates every file against `pipeline/schemas/housing.py` (`HousingBatch`) and fails on any problem. Unknown keys are an error.

## Rules

* Sources: resort HR and careers pages, housing authority sites, published listings. Every value is sourced; anything you cannot source is `null`.
* `resort_id` must be a resort in the build. Site `id` is a kebab case slug, unique across all housing files.
* `type`: `resort_owned`, `master_lease` (resort leased units for staff) or `market`.
* `precision: building` only when the street address is publicly listed. Then `address` is required, as a Sourced string. Otherwise use `precision: neighborhood` and a neighborhood level point.
* `rent_monthly` is a Sourced range `{low, high}` in USD per person per month; `low` must not exceed `high`. A site without a published rent shows the county baseline (HUD FMR or ACS) as an estimate.
* `occupancy`: `shared`, `private`, `mixed` or `null`. `deposit` is a Sourced number or `null`.
* `town` (optional): the town the site is in, when a source names it. Without it the panel reads the town from a
  building's listed address ("street, town, ST 12345").

## Example (fictional resort and numbers, for format only)

```yaml
batch: example-housing
researched_date: 2026-10-12
sources:
  - {id: example-employee-housing-page, url: https://example.org/careers/housing, title: Example Resort employee housing, accessed_date: 2026-10-12}
sites:
  - id: example-resort-staff-lodge
    resort_id: example-resort
    name: Staff Lodge
    type: resort_owned
    lat: 39.6012
    lon: -106.3021
    location_source_id: example-employee-housing-page
    precision: building
    address: {value: "1 Example Way, Example, CO", source_id: example-employee-housing-page, as_of: 2026-10-12, confidence: published}
    rent_monthly: {value: {low: 600, high: 900}, source_id: example-employee-housing-page, as_of: 2026-10-12, confidence: published}
    occupancy: shared
    deposit: null
    eligibility_note: Seasonal employees with a full time contract.
    confidence: published
    verified_date: 2026-10-12
```

## Commutes

Drive times, distances and route lines come from the routing cache (`data/raw/routing/`, gitignored):

```bash
python pipeline/build.py                 # writes data/build/resorts.json (detail_available) first
python -m pipeline.routing run           # needs the routing host in config/definitions.yaml routing.base_url
python pipeline/build.py                 # picks the cached routes and isochrones up
```

The default provider is the public Valhalla server `valhalla1.openstreetmap.de`. Set `routing.provider: osrm` with `base_url: https://router.project-osrm.org` (no isochrones), or `ors` with `base_url: https://api.openrouteservice.org` and `api_key_env: ORS_API_KEY`.
