# Real world BeamNG levels at 1:1

This tool builds a BeamNG.drive map of a real place at real size. One metre in the game is one metre on the ground, and heights are metres above sea level.

## Run it

From the repository root:

```
pip install -r requirements.txt -r tools/beamng/requirements.txt
python -m tools.beamng --resort palisades-tahoe
python -m tools.beamng --lat 39.1968 --lon -120.2354 --name olympic_valley
```

The zip lands in `tools/beamng/out/`. Copy it into your BeamNG `mods` folder and pick the level in Freeroam.

Options:

* `--size` vertices a side, a power of two from 256 to 8192 (default 4096).
* `--square` metres between vertices (default 2). Size times square is the map width: 4096 x 2 m is 8.2 km.
* `--roads auto|osm|transit|none`. Auto reads OpenStreetMap roads and falls back to the resort's transit routes.
* `--smooth` blurs the ground over that many metres.

## What goes into the level

* Ground heights from the AWS Terrain Tiles (USGS 3DEP in the United States, about 10 m; SRTM elsewhere).
* Roads from OpenStreetMap, leveled into the terrain and painted as asphalt. Tracks are gravel. Bridges and tunnels are left out.
* For a resort: its ski runs and lifts from OpenSkiMap (`data/build/trailmap`), a spawn at the base area and one at the top of the highest lift.
* Rock on slopes steeper than 38 degrees, grass elsewhere. Textures are generated, so the mod needs nothing from another level.
* `source.json` in the level folder lists every source, the center, and the terrain scale.

The heights are also saved next to the zip as a north up 16 bit PNG for a manual World Editor import.

## Limits

* BeamNG stays precise within about 10 km of the map center. Bigger maps jitter near the edges.
* No buildings, trees, traffic or AI paths yet.

## Files

`geo.py` local metric frame and grid, `elevation.py` terrain tiles, `paint.py` roads and runs, `sources.py` OpenStreetMap and resort data, `ter.py` the BeamNG terrain format, `textures.py` generated textures, `level.py` level files and zip, `build.py` the command. Tests live in `tests/beamng/`.
