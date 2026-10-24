"""Real world BeamNG.drive levels at 1:1 scale (see tools/beamng/README.md).

The generator reads real ground heights (AWS Terrain Tiles), optionally OpenStreetMap roads, and for a resort the
OpenSkiMap runs and lifts already in ``data/build``, and writes a mod zip with one BeamNG level. One terrain grid step
is ``square_m`` metres on the ground and heights are metres above sea level, so the level is the real place at
real size.
"""
