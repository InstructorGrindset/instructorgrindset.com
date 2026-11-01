# Jet stream layer: data contract

Owner request (2026-11-01): a map overlay of the average jet stream path over the ski season, with the last 10
winters as a baseline and El Niño, La Niña and neutral winters to compare, each one toggled on and off by itself,
showing which states and mountains each path passes over. Owner answers: the season is November to April; the
baseline is the last 10 complete seasons; the El Niño, La Niña and neutral averages use every season since 1979/80;
strength classes (weak, moderate, strong) get their own toggles only when their paths differ enough, otherwise one
toggle per phase; each path draws as a line with a band, and the states and resorts under the band light up and are
listed.

The project rules in `CLAUDE.md` apply: data only in `/data`, every displayed value carries `source_id`, `as_of` and
`confidence`, all thresholds live in `config/definitions.yaml` (`jetstream:`), map code loads only on `/map/` routes.

## Sources

| Source id | What | Raw file |
|---|---|---|
| `era5-arco-250hpa-winds` | ECMWF ERA5 hourly 250 hPa u and v (Copernicus Climate Change Service), read from Google's public ARCO ERA5 bucket | `data/raw/climate/era5_jet/season_means_250hpa.json` (+ `source.json`), written by `python -m pipeline.climate.era5_jet fetch` |
| `noaa-cpc-roni` | NOAA CPC Relative Oceanic Niño Index (official ENSO index since February 2026) and the legacy ONI | `data/raw/climate/enso/RONI.ascii.txt`, `oni.ascii.txt` (+ `source.json`), written monthly by `.github/workflows/enso-index.yml` |

The ERA5 extract holds, per season (November 1 to April 30, 1979/80 to 2025/26), the mean u and v at 250 hPa on a
1 degree grid, latitude 15N to 75N and longitude 170W to 50W, from one hour per day (00 UTC on even days of the
year, 12 UTC on odd days) averaged on the native 0.25 degree grid and then into trapezoid weighted 1 degree boxes.

## Method (`pipeline/jetstream.py`; every number in `config/definitions.yaml` `jetstream`)

1. **Classify each season** from the RONI. An episode is a run of at least `enso.episode_min_seasons` consecutive
   overlapping three month values at or above `+enso.threshold` (El Niño) or at or below `-enso.threshold` (La Niña).
   A season takes the phase of the episode that holds its `enso.season_value` (DJF) value; otherwise it is neutral.
   Strength is the episode's peak absolute value against `enso.strength` (weak, moderate, strong; NOAA's very
   strong counts as strong).
2. **Average** the season mean u and v over the seasons of each layer (vector mean), and take the wind speed of
   that mean wind.
3. **Find the jet axis** per longitude: local maxima of speed between `axis.lat_min` and `axis.lat_max` with at
   least `axis.min_speed_ms` and a prominence of `axis.min_prominence_ms`, refined to a fraction of a degree with a
   parabola through the three points around the peak. Peaks link across neighboring longitudes when they move less
   than `axis.max_step_deg`; chains shorter than `axis.min_length_deg` drop. Where two chains run side by side they
   are the northern and southern branches; one chain is the jet stream. Each chain is smoothed with a running mean
   over `axis.smooth_deg` of longitude.
4. **Band**: north and south of the axis, the latitude where speed falls to `band.fraction` of the axis speed,
   capped at `band.max_half_width_deg`.
5. **Overflies**: a state is listed when a band covers any of its area, sampled on a `overflies.sample_deg` grid; a
   resort is listed when its point lies inside a band. Lists run west to east.
6. **Strength split**: a phase splits into strength toggles only when two strength classes with at least
   `split.min_seasons` seasons each have main axes that differ by at least `split.min_mean_shift_deg` of latitude on
   average over `split.lon_west` to `split.lon_east` (the lower 48). Otherwise the phase is one toggle.

## Build output: `data/build/jetstream.json` (loaded only when a jet stream toggle is first turned on)

```json
{
  "version": 1,
  "generated_at": "2026-11-01T00:00:00Z",
  "season_label": "November to April",
  "level_hpa": 250,
  "sources": {
    "winds": {"source_id": "era5-arco-250hpa-winds", "as_of": "2026-11-01", "confidence": "published"},
    "enso": {"source_id": "noaa-cpc-roni", "as_of": "2026-11-01", "confidence": "published"}
  },
  "layers": [
    {
      "id": "last10",
      "label": "Last 10 winters",
      "kind": "baseline",
      "phase": null,
      "strength": null,
      "color": "#e8e8e8",
      "seasons": ["2016/17", "2017/18"],
      "branches": [
        {
          "id": "main",
          "label": "Jet stream",
          "axis": [[-170, 38.4, 31.2], [-169, 38.5, 31.6]],
          "band": [[-170, 33.1, 43.9], [-169, 33.2, 44.0]]
        }
      ],
      "overflies": {
        "states": [{"code": "CA", "name": "California", "branches": ["main"]}],
        "resorts": [{"id": "mammoth-mountain", "state": "CA", "branch": "main"}]
      },
      "source_id": "era5-arco-250hpa-winds",
      "as_of": "2026-11-01",
      "confidence": "estimated"
    }
  ],
  "seasons": [
    {"season": "1979/80", "phase": "neutral", "strength": null, "djf": 0.65, "peak": null,
     "source_id": "noaa-cpc-roni", "as_of": "2026-11-01", "confidence": "published"}
  ],
  "split": {"el_nino": {"split": false, "shift_deg": 1.1}, "la_nina": {"split": false, "shift_deg": 0.8}},
  "method": "Plain text summary of the method above, shown under the toggles."
}
```

* `layers` order is the toggle order: `last10`, then each phase (`el_nino`, `la_nina`, `neutral`), or its strength
  layers (`el_nino_weak`, `el_nino_moderate`, `el_nino_strong`) when `split` says so.
* `axis` points are `[lon, lat, speed_ms]`, one per degree of longitude, west to east; `band` points are
  `[lon, lat_south, lat_north]` at the same longitudes. A branch may cover only part of the longitude range.
* Paths are derived from ERA5 by this method, so they carry `confidence: "estimated"`; the season classes copy the
  RONI and carry `published`.
* `color` comes from `jetstream.colors` in config.

## UI (`/map/` only)

* A "Jet stream" section in the Layers panel: one checkbox per layer with its color swatch and season count
  ("El Niño, 13 winters"), and a short method line with links to both sources.
* Each visible layer draws its band (translucent fill) and its axis (line) per branch, under the resort dots.
* The most recently turned on layer is the active one: states under its band get a tint, resort dots under it get
  a ring, and a card lists its states and resorts by state, west to east. A selector in the card switches the active
  layer when several are visible.
* Nothing of this loads before the first toggle; `jetstream.json` is lazy.
