"""Share cards: one 1200 x 630 PNG per Tier A resort page and per cluster page, for og:image (stdlib only).

``pipeline/routes.py`` calls ``write_cards`` with the canonical URL of every page it writes and puts each card's
URL, size and alt text in that page's Open Graph tags. Tier B resort pages, state pages and /map/ keep the site
logo (``routes.OG_IMAGE``) so the repository stays small. See docs/map/ARCHITECTURE.md, "Share cards".

A card shows the site name, the resort or cluster name with its cluster and state, a map of the state (from
``data/build/us-states.geojson``) with the cluster polygon and the resorts marked, and up to three sourced facts
copied from ``resorts.json``: skiable acres, season length and the resort's own annual snowfall, each with its
confidence. Cluster cards count the cluster's resorts and Tier A resorts. Nothing else from a record reaches a card:
no heat map value, no pay, and never the operator scale (owner decision 2026-10-13).

Cards are written to ``images/share/<state>/<cluster>/<resort>.png`` and ``images/share/<state>/<cluster>.png``
(the canonical state), served as static files. ``images/share/.cards.json`` maps each card to a key over its
inputs and the code that draws it; a card is drawn only when its key changed and written only when its bytes
changed, so rebuilds leave unchanged PNGs alone. Cards no page uses are removed.

    python -m pipeline.share   # rewrite the cards for the current data/build (routes.py does this too)
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from pipeline import glyphs, png
from pipeline.raster import Canvas, circle, decimate, hex_color, oriented, rounded_rect, signed_area, stroke

SITE = "https://instructorgrindset.com"
WIDTH, HEIGHT = 1200, 630
OUT_DIR = ("images", "share")
MANIFEST = ".cards.json"
BRAND_TEXT = "Instructor Grindset Career Map"

# Site colors: assets/global.css (--bg, --ink, --muted, --line, --accent), map/css/map.css (badges) and
# map/js/config.js (map palette).
C = {k: hex_color(v) for k, v in {
    "bg": "#0f1217", "ink": "#e7e9ee", "title": "#f4f6fb", "muted": "#cbd5e1", "faint": "#8891a0", "line": "#2a3140",
    "eyebrow": "#f2c27a", "map_bg": "#0a0e14", "state": "#121821", "state_line": "#283244", "focus": "#1f2a3a",
    "focus_line": "#6f86a8", "cluster": "#a6c8ff", "tier_a": "#e0a03a", "tier_a_ring": "#fff4dc", "tier_b": "#7fa6d9",
    "published": "#6fd3ac", "reported": "#a6c8ff", "estimated": "#e0b064",
}.items()}

PANEL = (716, 48, 1152, 582)  # map panel x0, y0, x1, y1
PANEL_RADIUS = 26
LEFT, RIGHT = 72, 668  # text column

FACILITY = {"ski_area": "ski area", "indoor": "indoor ski facility", "training_center": "year round training center"}
CONFIDENCE = {"published": "Published", "reported": "Reported", "estimated": "Estimated"}

# The only record fields a card may show: (group, field, label, unit).
FACTS = (("size", "acres", "Skiable acres", ""), ("season", "length_days", "Season length", "days"),
         ("snow", "annual_avg_in", "Annual snowfall", "in"))
ALT_FACT = {"acres": "{v} skiable acres", "length_days": "{v} day season", "annual_avg_in": "{v} in annual snowfall"}


# ── Card plans ──

def _num(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def _join_and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def resort_facts(r: dict) -> list[dict]:
    """Up to three sourced facts: a value with a source_id, formatted, with its confidence."""
    out = []
    for group, key, label, unit in FACTS:
        src = (r.get(group) or {}).get(key)
        if not isinstance(src, dict) or not src.get("source_id"):
            continue
        v = src.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            continue
        out.append({"key": key, "value": _num(v) + (f" {unit}" if unit else ""), "label": label,
                    "confidence": src.get("confidence") if src.get("confidence") in CONFIDENCE else None,
                    "alt": ALT_FACT[key].format(v=_num(v))})
    return out


def _place(cluster: dict, state_name: str) -> str:
    """"I 70 Corridor, Colorado", or the state alone for an "Other ..." cluster or one named after the state."""
    name = cluster.get("name") or ""
    if cluster.get("id", "").startswith("other-") or name == state_name or not name:
        return state_name
    return f"{name}, {state_name}"


def _members(cluster: dict, resorts: dict[str, dict], skip: Optional[str] = None) -> list[list]:
    out = []
    for rid in cluster.get("resort_ids") or []:
        r = resorts.get(rid)
        if r and rid != skip and r.get("lat") is not None and r.get("lon") is not None:
            out.append([round(float(r["lon"]), 5), round(float(r["lat"]), 5), "A" if r.get("tier") == "A" else "B"])
    return sorted(out)


def resort_card(r: dict, cluster: dict, geometry: Any, st: str, state_names: dict[str, str], resorts: dict) -> dict:
    home = r["state"].upper()
    state_name = state_names.get(home, home)
    facts = resort_facts(r)
    place = _place(cluster, state_name)
    alt = f"Career Map card for {r['name']}, {place}, with the resort marked on a map of {state_name}."
    if facts:
        alt += " " + _join_and([f["alt"] for f in facts]).capitalize() + "."
    return {
        "kind": "resort", "file": f"{st}/{cluster['id']}/{r['id']}.png",
        "eyebrow": f"Tier A {FACILITY.get(r.get('facility_type') or 'ski_area', 'ski area')}",
        "title": r["name"], "subtitle": place,
        "facts": [{k: f[k] for k in ("value", "label", "confidence")} for f in facts],
        "map": {"states": [home], "cluster": geometry, "points": _members(cluster, resorts, r["id"]),
                "focus": [round(float(r["lon"]), 5), round(float(r["lat"]), 5)]},
        "alt": alt,
    }


def cluster_card(cluster: dict, geometry: Any, st: str, state_names: dict[str, str], resorts: dict) -> dict:
    codes = [s.upper() for s in cluster.get("states") or []]
    names = _join_and([state_names.get(s, s) for s in codes]) if codes else ""
    points = _members(cluster, resorts)
    n, a = len(points), sum(1 for p in points if p[2] == "A")
    facts = [{"value": f"{n:,}", "label": _plural(n, "Resort", "Resorts"), "confidence": None}]
    if a:
        facts.append({"value": f"{a:,}", "label": _plural(a, "Tier A resort", "Tier A resorts"), "confidence": None})
    alt = (f"Career Map card for the {cluster['name']} cluster in {names}, with its resorts marked on a map. "
           f"{n:,} {_plural(n, 'resort', 'resorts')}" + (f", {a:,} Tier A." if a else "."))
    return {
        "kind": "cluster", "file": f"{st}/{cluster['id']}.png", "eyebrow": "Cluster", "title": cluster["name"],
        "subtitle": names, "facts": facts,
        "map": {"states": codes, "cluster": geometry, "points": points, "focus": None},
        "alt": alt,
    }


def _load(build: Path, name: str) -> Any:
    path = build / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def plan_cards(build: Path, canonicals: Iterable[str]) -> dict[str, dict]:
    """Canonical page URL to card plan, for every cluster page and every Tier A resort page among ``canonicals``."""
    resorts = {r["id"]: r for r in (_load(build, "resorts.json") or {}).get("resorts", [])}
    feats = (_load(build, "clusters.geojson") or {}).get("features", [])
    clusters = {f["properties"]["id"]: (f["properties"], f.get("geometry")) for f in feats
                if isinstance(f.get("properties"), dict) and f["properties"].get("id")}
    state_names = {s["code"].upper(): s["name"] for s in (_load(build, "states.json") or {}).get("states", [])}
    out: dict[str, dict] = {}
    for canonical in sorted(set(canonicals)):
        parts = canonical.strip("/").split("/")
        if parts[0] != "map" or len(parts) not in (3, 4) or parts[2] not in clusters:
            continue
        cluster, geometry = clusters[parts[2]]
        if len(parts) == 3:
            out[canonical] = cluster_card(cluster, geometry, parts[1], state_names, resorts)
        else:
            r = resorts.get(parts[3])
            if r and r.get("tier") == "A" and r.get("lat") is not None and r.get("lon") is not None:
                out[canonical] = resort_card(r, cluster, geometry, parts[1], state_names, resorts)
    return out


# ── Drawing ──

def _polygons(geometry: Any) -> list[list[list[list[float]]]]:
    """Polygons (lists of rings) of a GeoJSON Polygon or MultiPolygon, longitudes east of 0 moved west by 360
    (the Aleutians), so a state never wraps around the map."""
    if not isinstance(geometry, dict):
        return []
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    polys = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    return [[[[x - 360 if x > 0 else x, y] for x, y, *_ in ring] for ring in poly] for poly in polys]


class Projection:
    """Equirectangular at the frame's middle latitude, fitted into the map panel."""

    def __init__(self, lonlat: list[tuple[float, float]], box: tuple[float, float, float, float], pad: float):
        lons, lats = [p[0] for p in lonlat], [p[1] for p in lonlat]
        w0, e0, s0, n0 = min(lons), max(lons), min(lats), max(lats)
        span = 1.2  # a lone point or a tiny frame still gets about a degree of context
        if e0 - w0 < span:
            w0, e0 = (w0 + e0) / 2 - span / 2, (w0 + e0) / 2 + span / 2
        if n0 - s0 < span * 0.7:
            s0, n0 = (s0 + n0) / 2 - span * 0.35, (s0 + n0) / 2 + span * 0.35
        self.k = math.cos(math.radians((s0 + n0) / 2))
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0 - 2 * pad, y1 - y0 - 2 * pad
        fw, fh = (e0 - w0) * self.k, n0 - s0
        self.scale = min(bw / fw, bh / fh)
        self.cx, self.cy = (w0 + e0) / 2 * self.k, (s0 + n0) / 2
        self.ox, self.oy = (x0 + x1) / 2, (y0 + y1) / 2

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        return (self.ox + (lon * self.k - self.cx) * self.scale, self.oy - (lat - self.cy) * self.scale)

    def ring(self, ring: list[list[float]]) -> list[tuple[float, float]]:
        return decimate([self(x, y) for x, y in ring], 0.7)


def _frame_points(polys: list, min_share: float = 0.01) -> list[tuple[float, float]]:
    """Vertices of the rings that frame a shape: outer rings at least ``min_share`` of the largest one, so far
    island chains do not shrink the state."""
    outers = [poly[0] for poly in polys if poly and len(poly[0]) >= 3]
    if not outers:
        return []
    areas = [abs(signed_area([(x, y) for x, y in r])) for r in outers]
    big = max(areas)
    return [(x, y) for r, a in zip(outers, areas) if a >= big * min_share for x, y in r]


def _shape(proj: Projection, polys: list) -> list:
    rings = []
    for poly in polys:
        for k, ring in enumerate(poly):
            pts = proj.ring(ring)
            if len(pts) >= 3:
                rings.append(oriented(pts, positive=(k == 0)))
    return rings


def _intersects(rings: list, box: tuple[float, float, float, float]) -> bool:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return bool(xs) and min(xs) < box[2] and max(xs) > box[0] and min(ys) < box[3] and max(ys) > box[1]


def draw_map(cv: Canvas, m: dict, states: dict[str, Any]) -> None:
    x0, y0, x1, y1 = PANEL
    clip = (x0, y0, x1, y1)
    cv.fill([rounded_rect(x0, y0, x1, y1, PANEL_RADIUS)], C["map_bg"])
    focus = {code: _polygons(states.get(code)) for code in m["states"]}
    cluster = _polygons(m.get("cluster"))
    pts = [(p[0], p[1]) for p in m["points"]] + ([tuple(m["focus"])] if m.get("focus") else [])
    frame = [p for polys in focus.values() for p in _frame_points(polys)] + _frame_points(cluster, 0.0) + pts
    if frame:
        proj = Projection(frame, PANEL, 44)
        for code, geom in sorted(states.items()):
            if code in focus:
                continue
            rings = _shape(proj, _polygons(geom))
            if rings and _intersects(rings, PANEL):
                cv.fill(rings, C["state"], clip=clip, sub=3)
                cv.fill([s for r in rings for s in stroke(r, 1.2)], C["state_line"], clip=clip, sub=3)
        for code in m["states"]:
            rings = _shape(proj, focus[code])
            cv.fill(rings, C["focus"], clip=clip, sub=3)
            cv.fill([s for r in rings for s in stroke(r, 1.6)], C["focus_line"], clip=clip, sub=3)
        rings = _shape(proj, cluster)
        if rings:
            cv.fill(rings, C["cluster"], 0.14, clip=clip, sub=3)
            cv.fill([s for r in rings for s in stroke(r, 1.8)], C["cluster"], 0.85, clip=clip, sub=3)
        big = m.get("focus") is None
        for lon, lat, tier in sorted(m["points"], key=lambda p: p[2] == "A"):
            x, y = proj(lon, lat)
            if tier == "A":
                cv.fill([circle(x, y, 7.5 if big else 6.0)], C["tier_a_ring"], clip=clip)
                cv.fill([circle(x, y, 5.8 if big else 4.5)], C["tier_a"], clip=clip)
            else:
                cv.fill([circle(x, y, 5.5 if big else 4.5)], C["map_bg"], clip=clip)
                cv.fill([circle(x, y, 4.2 if big else 3.4)], C["tier_b"], clip=clip)
        if m.get("focus"):
            x, y = proj(*m["focus"])
            cv.fill([circle(x, y, 30)], C["tier_a"], 0.16, clip=clip)
            cv.fill([circle(x, y, 19)], C["tier_a"], 0.22, clip=clip)
            cv.fill([circle(x, y, 12.5)], C["tier_a_ring"], clip=clip)
            cv.fill([circle(x, y, 9.5)], C["tier_a"], clip=clip)
    r = PANEL_RADIUS
    for cx, cy, sx, sy in ((x0 + r, y0 + r, -1, -1), (x1 - r, y0 + r, 1, -1), (x1 - r, y1 - r, 1, 1), (x0 + r, y1 - r, -1, 1)):
        corner = [(cx + sx * r, cy + sy * r)] + [(cx + sx * r * math.cos(math.radians(a)), cy + sy * r * math.sin(math.radians(a)))
                                                  for a in range(0, 91, 6)]
        cv.fill([corner], C["bg"])
    cv.fill([oriented(rounded_rect(x0, y0, x1, y1, r)), oriented(rounded_rect(x0 + 1.5, y0 + 1.5, x1 - 1.5, y1 - 1.5, r - 1.5), False)],
            C["line"])


def draw_brand(cv: Canvas, bold: glyphs.Font) -> None:
    x, y, s = LEFT, 48, 44
    cv.fill([rounded_rect(x, y, x + s, y + s, 9.5)], C["map_bg"])
    cv.fill([oriented(rounded_rect(x, y, x + s, y + s, 9.5)), oriented(rounded_rect(x + 1, y + 1, x + s - 1, y + s - 1, 8.5), False)],
            C["line"])
    k = s / 32
    cv.fill([[(x + px * k, y + py * k) for px, py in ((5, 25), (13, 11), (18, 19), (21, 14), (27, 25))]], C["tier_a"])
    size = 28
    base = y + s / 2 + bold.cap_height * size / bold.upem / 2
    cv.fill(bold.rings(BRAND_TEXT, size, x + s + 16, base), C["ink"])


def draw_text(cv: Canvas, card: dict, bold: glyphs.Font, regular: glyphs.Font) -> None:
    width = RIGHT - LEFT
    eyebrow = card["eyebrow"].upper()
    cv.fill(bold.rings(eyebrow, 20, LEFT, 168, tracking=0.08), C["eyebrow"])

    facts = card["facts"]
    vs, ls, cs, gap = 46, 22, 15, 48
    while True:
        cols = [max(bold.width(f["value"], vs), regular.width(f["label"], ls),
                    bold.width(CONFIDENCE.get(f["confidence"] or "", "").upper(), cs, 0.08)) for f in facts]
        if sum(cols) + gap * max(0, len(cols) - 1) <= width or vs <= 32:
            break
        vs, ls, gap = vs - 4, max(18, ls - 1), max(28, gap - 6)
    has_conf = any(f["confidence"] for f in facts)
    value_base = 518 if has_conf else 540
    facts_top = value_base - vs * 0.75 - 30 if facts else 560

    top = 190
    sub_size = 30
    avail = facts_top - top - (sub_size + 26)
    size, lines = glyphs.fit(bold, card["title"], [88, 80, 72, 64, 58, 52, 46, 42, 38], width, avail)
    base = top + size * 0.80
    for line in lines:
        cv.fill(bold.rings(line, size, LEFT, base), C["title"])
        base += size * 1.08
    sub_base = base - size * 1.08 + sub_size + 22
    sub = card["subtitle"]
    if regular.width(sub, sub_size) > width:
        sub_size, sub_lines = glyphs.fit(regular, sub, [28, 26, 24, 22], width, 60, max_lines=2)
    else:
        sub_lines = [sub]
    for line in sub_lines:
        cv.fill(regular.rings(line, sub_size, LEFT, sub_base), C["muted"])
        sub_base += sub_size * 1.2

    if not facts:
        return
    cv.fill([[(LEFT, facts_top), (RIGHT, facts_top), (RIGHT, facts_top + 1.5), (LEFT, facts_top + 1.5)]], C["line"])
    x = LEFT
    for f, w in zip(facts, cols):
        cv.fill(bold.rings(f["value"], vs, x, value_base), C["ink"])
        cv.fill(regular.rings(f["label"], ls, x, value_base + 34), C["muted"])
        if f["confidence"]:
            cv.fill(bold.rings(CONFIDENCE[f["confidence"]].upper(), cs, x, value_base + 62, tracking=0.08),
                    C[f["confidence"]])
        x += w + gap


def render(card: dict, states: dict[str, Any]) -> bytes:
    """PNG bytes of one card (palette PNG, deterministic for the same inputs)."""
    bold, regular = glyphs.load("inter-bold"), glyphs.load("inter-regular")
    cv = Canvas(WIDTH, HEIGHT, C["bg"])
    draw_map(cv, card["map"], states)
    draw_brand(cv, bold)
    draw_text(cv, card, bold, regular)
    return png.encode_indexed(WIDTH, HEIGHT, cv.px, cv.palette)


# ── Writing ──

def _code_hash() -> str:
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in [here / "share.py", here / "raster.py", here / "glyphs.py", here / "png.py",
              *sorted((here / "fonts").glob("*.json"))]:
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def states_hash(states: dict[str, Any]) -> str:
    blob = json.dumps(states, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def card_key(card: dict, states: str, code: str) -> str:
    """Hash over everything that decides a card's pixels: its plan, the state outlines and the drawing code."""
    blob = json.dumps({"card": {k: v for k, v in card.items() if k != "alt"}, "states": states, "code": code},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _out_dir(repo_root: Path) -> Optional[Path]:
    """images/share under ``repo_root``, or None when it would resolve outside it (for example a test site whose
    images folder links to the repository's)."""
    root = Path(repo_root).resolve()
    out = Path(repo_root).joinpath(*OUT_DIR)
    if root not in out.resolve().parents:
        return None
    return out


def write_cards(repo_root: Path, canonicals: Iterable[str], log=None) -> dict[str, dict]:
    """Draw and write the cards for ``canonicals``; returns canonical URL to {url, width, height, alt}."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    repo_root = Path(repo_root)
    out = _out_dir(repo_root)
    if out is None:
        log("share: images/share resolves outside the site root; pages keep the site logo")
        return {}
    build = repo_root / "data" / "build"
    plans = plan_cards(build, canonicals)
    geo = _load(build, "us-states.geojson") or {}
    states = {f["properties"]["code"].upper(): f.get("geometry") for f in geo.get("features", [])
              if isinstance(f.get("properties"), dict) and f["properties"].get("code")}
    manifest_path = out / MANIFEST
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8")).get("cards", {})
    except (OSError, ValueError, AttributeError):
        old = {}
    code, outlines = _code_hash(), states_hash(states)
    keys: dict[str, str] = {}
    drawn = written = 0
    result: dict[str, dict] = {}
    for canonical, card in plans.items():
        target = out / card["file"]
        key = card_key(card, outlines, code)
        keys[card["file"]] = key
        if not (old.get(card["file"]) == key and target.is_file()):
            data = render(card, states)
            drawn += 1
            if not target.is_file() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                written += 1
        result[canonical] = {"url": f"{SITE}/{'/'.join(OUT_DIR)}/{card['file']}", "width": WIDTH, "height": HEIGHT,
                             "alt": card["alt"]}
    removed = _remove_stale(out, set(keys))
    text = json.dumps({"generated_by": "pipeline/share.py",
                       "note": "Share card keys; a card is redrawn when its key changes. Do not edit.",
                       "cards": dict(sorted(keys.items()))}, indent=1) + "\n"
    if keys or manifest_path.exists():
        out.mkdir(parents=True, exist_ok=True)
        if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != text:
            manifest_path.write_text(text, encoding="utf-8")
    if drawn or removed:
        log(f"share: {len(plans)} cards ({drawn} drawn, {written} written, {removed} removed)")
    return result


def _remove_stale(out: Path, keep: set[str]) -> int:
    if not out.is_dir():
        return 0
    n = 0
    for p in sorted(out.rglob("*.png")):
        rel = p.relative_to(out).as_posix()
        if rel not in keep and p.is_file() and not p.is_symlink():
            p.unlink()
            n += 1
    for d in sorted((d for d in out.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        try:
            d.rmdir()  # only empty folders
        except OSError:
            pass
    return n


def main(argv: Optional[list[str]] = None) -> int:
    from pipeline.routes import plan_pages  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1]
    pages = plan_pages(root / "data" / "build")
    cards = write_cards(root, {p.canonical for p in pages.values()}, log=print)
    print(f"share: {len(cards)} cards under {root.joinpath(*OUT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
