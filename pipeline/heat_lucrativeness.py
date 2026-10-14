"""Lucrativeness heat rasters: ``heat/lucrativeness.json`` and ``heat/lucrativeness-<region>.png``.

The same surface map/js/heat.js draws in the browser from the slim index: the ``heat.field`` interpolation,
resolution and fade, colored with ``heat.ramp`` over ``legend_scale`` (the twin of ``legendScale`` in heat.js).
Values are the resorts' computed ``career.lucrativeness.mid``; interpolated colors never become numbers.
Kept apart from pipeline/heat.py so a change here leaves the snowfall and season rasters alone (their digest
covers heat.py). Contract: docs/map/ARCHITECTURE.md, heat map rasters.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pipeline import png
from pipeline.heat import (
    MANIFEST_VERSION, Fade, Grid, Point, _drop_stale, _load_manifest, _write_if_changed, colorizer, coverage,
    decluster, hex_rgb, inputs_digest, nice_ceil, normalize_lon, paint, region_polygons, scale_t, solve_field,
)
from pipeline.season_income import round_half_up


def _code_digest() -> list[str]:
    """This module and the shared heat code: a change to either rebuilds these rasters."""
    import pipeline.heat as heat_mod  # noqa: PLC0415

    return [hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in (__file__, heat_mod.__file__, png.__file__)]


def legend_scale(mode: Any, values: Sequence[float]) -> dict:
    """``{scale, domain: {min, max}, ticks}`` of a linear or sqrt mode: the twin of ``legendScale`` in map/js/heat.js,
    so a precomputed raster and the browser fallback color the same values the same way."""
    scale = mode.scale
    if mode.domain:
        lo, hi = mode.domain
        return {"scale": scale, "domain": {"min": lo, "max": hi}, "ticks": list(mode.ticks or [lo, hi])}
    lo, hi = min(values), max(values)
    if scale == "sqrt":
        mn = mode.domain_min if mode.domain_min is not None else lo
        mx = nice_ceil(max(hi, mn + 1))
        ticks = [t for t in (mode.ticks or []) if mn <= t < mx and 1 - scale_t(t, "sqrt", mn, mx) >= 0.12]
        return {"scale": scale, "domain": {"min": mn, "max": mx}, "ticks": ticks + [mx]}
    low = mode.domain_min if mode.domain_min is not None else lo
    raw = max(hi - low, abs(hi) * 0.1, 1e-9) / 4
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next((f * mag for f in (1, 2, 2.5, 5, 10) if f * mag >= raw), 10 * mag)
    mn = math.floor(low / step) * step
    mx = math.ceil(hi / step) * step
    if mx == mn:
        mx = mn + step
    ticks = []
    t = mn
    while t <= mx + step / 2:
        ticks.append(round_half_up(t / step) * step)   # JS Math.round
        t += step
    return {"scale": scale, "domain": {"min": mn, "max": mx}, "ticks": ticks}


def lucrativeness_points(resorts: Iterable[dict]) -> list[Point]:
    """Resorts with a computed ``career.lucrativeness.mid`` (estimated net income at the top of the wage scale)."""
    out = []
    for r in resorts:
        luc = (r.get("career") or {}).get("lucrativeness") or {}
        v = luc.get("mid")
        if luc.get("status") == "computed" and isinstance(v, (int, float)) and r.get("lat") is not None \
                and r.get("lon") is not None:
            out.append(Point(float(r["lon"]), float(r["lat"]), float(v)))
    return out


def write_lucrativeness(out_dir: Path, *, resorts: Sequence[dict], outlines: dict, heat: Any,
                        generated_at: str) -> Optional[dict]:
    """Write ``heat/lucrativeness-<region>.png`` and ``heat/lucrativeness.json`` from the resorts' computed
    lucrativeness mids, the same surface the browser draws (``heat.field`` interpolation, resolution and fade,
    ``heat.ramp`` over ``legend_scale``). Returns the manifest, or None when no resort has a value."""
    heat_dir = out_dir / "heat"
    mode = heat.modes.lucrativeness
    field = heat.field
    raw = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value) for p in lucrativeness_points(resorts)]
    if not raw or mode.scale == "classed":
        _drop_stale(heat_dir, "lucrativeness*", set())
        return None
    pts = decluster(raw, field.decluster_km)
    lg = legend_scale(mode, [p.value for p in raw])
    regions = {"lower48": heat.regions.lower48, "alaska": heat.regions.alaska}
    digest = inputs_digest(sorted((p.lon, p.lat, p.value) for p in raw), outlines.get("features", []),
                           heat.model_dump(mode="json"), MANIFEST_VERSION, _code_digest())
    manifest_path = heat_dir / "lucrativeness.json"
    old = _load_manifest(manifest_path, digest)
    if old:
        return old
    color_of = colorizer(mode, heat.ramp.colors, (lg["domain"]["min"], lg["domain"]["max"]))
    fade = Fade(field.full_opacity_km, field.fade_end_km, field.max_alpha, field.floor_alpha, field.fade_to,
                hex_rgb(heat.neutral), None)
    rasters = []
    for name, box in regions.items():
        polys = region_polygons(outlines, name)
        if not polys:
            continue
        grid = Grid.for_box(box.west, box.south, box.east, box.north, field.px_per_degree)
        cov = coverage(grid, polys)
        vals, dist = solve_field(grid, cov, pts, box, field.neighbors, field.min_distance_km, field.grid_step_px)
        buf, colored = paint(vals, dist, cov, color_of, fade)
        if not colored:
            continue
        data = png.encode_rgba(grid.width, grid.height, bytes(buf))
        image = f"lucrativeness-{name}.png"
        _write_if_changed(heat_dir / image, data)
        rasters.append({"region": name, "image": image, "width": grid.width, "height": grid.height,
                        "coordinates": grid.coordinates(), "bytes": len(data),
                        "resorts": sum(1 for p in raw if box.west <= p.lon <= box.east and box.south <= p.lat <= box.north)})
    _drop_stale(heat_dir, "lucrativeness-*.png", {r["image"] for r in rasters})
    if not rasters:
        return None
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "mode": "lucrativeness",
        "units": "usd",
        "scale": lg["scale"],
        "domain": lg["domain"],
        "ticks": lg["ticks"],
        "ramp": list(heat.ramp.colors),
        # Each value is a resort's computed career.lucrativeness.mid in resorts.json, whose assumptions and rent
        # sources are listed there.
        "values": {"field": "career.lucrativeness.mid", "resorts": len(raw), "points": len(pts)},
        "method": {"interpolation": "adaptive modified Shepard", "neighbors": field.neighbors,
                   "min_distance_km": field.min_distance_km, "decluster_km": field.decluster_km,
                   "grid_step_px": field.grid_step_px, "px_per_degree": field.px_per_degree,
                   "full_opacity_km": field.full_opacity_km, "fade_end_km": field.fade_end_km,
                   "floor_alpha": field.floor_alpha, "fade_to": field.fade_to},
        "attribution": None,
        "inputs_sha256": digest,
        "rasters": rasters,
    }
    heat_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
