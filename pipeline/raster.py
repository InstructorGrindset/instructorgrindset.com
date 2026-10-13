"""A small antialiased vector rasterizer (stdlib only) for the share cards (``pipeline/share.py``).

Shapes are lists of closed rings in pixel coordinates (x right, y down) filled with the nonzero winding rule.
Coverage is exact along each row and sampled ``sub`` times per pixel vertically, then quantized to ``LEVELS``
steps, so a card holds few distinct colors; ``Canvas.indexed`` reduces them to a 256 color palette for a palette
PNG. Point coordinates are rounded to 1/256 pixel before use, so the same calls give the same pixels on every
machine.
"""
from __future__ import annotations

import math
from array import array
from collections import Counter
from typing import Iterable, Optional, Sequence

Point = tuple[float, float]
Ring = list[Point]
Color = tuple[int, int, int]

LEVELS = 32  # alpha steps per blend


def hex_color(text: str) -> Color:
    t = text.lstrip("#")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


def signed_area(ring: Sequence[Point]) -> float:
    n = len(ring)
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))


def oriented(ring: Ring, positive: bool = True) -> Ring:
    """``ring`` with a positive (or negative) signed area, so unions and holes behave under nonzero winding."""
    return ring if (signed_area(ring) >= 0) == positive else ring[::-1]


def circle(cx: float, cy: float, r: float, n: int = 0) -> Ring:
    n = n or max(16, min(96, int(r * 3)))
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float, n: int = 10) -> Ring:
    pts: Ring = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def stroke(line: Sequence[Point], width: float, closed: bool = True) -> list[Ring]:
    """Rings that cover a polyline drawn ``width`` pixels wide: one quad per segment and a small disc per joint,
    all with the same orientation so they unite under nonzero winding."""
    pts = list(line)
    if closed and len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    h = width / 2.0
    out: list[Ring] = []
    n = len(pts)
    segs = n if closed else n - 1
    for k in range(segs):
        (xa, ya), (xb, yb) = pts[k], pts[(k + 1) % n]
        dx, dy = xb - xa, yb - ya
        ln = math.hypot(dx, dy)
        if ln == 0:
            continue
        nx, ny = -dy / ln * h, dx / ln * h
        out.append(oriented([(xa + nx, ya + ny), (xb + nx, yb + ny), (xb - nx, yb - ny), (xa - nx, ya - ny)]))
    if width >= 1.2:
        for p in pts:
            out.append(oriented(circle(p[0], p[1], h, 8)))
    return out


def decimate(ring: Sequence[Point], min_step: float = 0.6) -> Ring:
    """Drop points closer than ``min_step`` pixels to the last kept point (keeps the first point)."""
    if not ring:
        return []
    out = [ring[0]]
    lx, ly = ring[0]
    for x, y in ring[1:]:
        if abs(x - lx) >= min_step or abs(y - ly) >= min_step:
            out.append((x, y))
            lx, ly = x, y
    return out


class Canvas:
    """An image held as palette indices (one per pixel, top row first, ``array('H')``) into ``palette``. Blends add
    palette entries as needed; ``indexed`` reduces the palette to 256 colors for an 8 bit palette PNG."""

    def __init__(self, width: int, height: int, background: Color, reserve: Iterable[Color] = ()):
        """``reserve`` lists the solid colors, which ``indexed`` always keeps exact."""
        self.w, self.h = width, height
        self.palette: list[Color] = [tuple(background)]
        self._index: dict[Color, int] = {tuple(background): 0}
        self.px = array("H", [0]) * (width * height)
        for c in reserve:
            self.color_index(tuple(c))
        self.reserved = len(self.palette)

    def color_index(self, rgb: Color) -> int:
        i = self._index.get(rgb)
        if i is None:
            if len(self.palette) >= 65535:
                raise ValueError("too many colors")
            i = len(self.palette)
            self.palette.append(rgb)
            self._index[rgb] = i
        return i

    def fill(self, rings: Iterable[Sequence[Point]], color: Color, alpha: float = 1.0,
             clip: Optional[tuple[int, int, int, int]] = None, sub: int = 5) -> None:
        """Fill ``rings`` (nonzero winding) with ``color`` at ``alpha``, inside ``clip`` (x0, y0, x1, y1)."""
        rings = [r for r in rings if len(r) >= 3]
        if not rings or alpha <= 0:
            return
        cx0, cy0, cx1, cy1 = clip or (0, 0, self.w, self.h)
        cx0, cy0, cx1, cy1 = max(0, cx0), max(0, cy0), min(self.w, cx1), min(self.h, cy1)
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        y_lo, y_hi = max(cy0, math.floor(min(ys))), min(cy1, math.ceil(max(ys)))
        x_lo, x_hi = max(cx0, math.floor(min(xs))), min(cx1, math.ceil(max(xs)))
        if y_lo >= y_hi or x_lo >= x_hi:
            return
        rows = y_hi - y_lo
        nsub = rows * sub
        buckets: list[list[tuple[float, int]]] = [[] for _ in range(nsub)]
        for ring in rings:
            n = len(ring)
            for k in range(n):
                xa, ya = ring[k]
                xb, yb = ring[(k + 1) % n]
                xa, ya, xb, yb = round(xa * 256) / 256, round(ya * 256) / 256, round(xb * 256) / 256, round(yb * 256) / 256
                if ya == yb:
                    continue
                d = 1
                if ya > yb:
                    xa, ya, xb, yb, d = xb, yb, xa, ya, -1
                j0 = max(0, math.ceil((ya - y_lo) * sub - 0.5))
                j1 = min(nsub, math.ceil((yb - y_lo) * sub - 0.5))
                if j0 >= j1:
                    continue
                slope = (xb - xa) / (yb - ya)
                for j in range(j0, j1):
                    buckets[j].append((xa + (y_lo + (j + 0.5) / sub - ya) * slope, d))
        width = x_hi - x_lo
        inv = 1.0 / sub
        top = round(alpha * LEVELS)
        if top <= 0:
            return
        solid = self.color_index(tuple(color)) if top >= LEVELS else -1
        blends: dict[tuple[int, int], int] = {}
        pal, px = self.palette, self.px
        r_, g_, b_ = color
        half = LEVELS // 2

        def blend(dst: int, q: int) -> int:
            key = (dst, q)
            v = blends.get(key)
            if v is None:
                pr, pg, pb = pal[dst]
                k = LEVELS - q
                v = self.color_index(((pr * k + r_ * q + half) // LEVELS, (pg * k + g_ * q + half) // LEVELS,
                                      (pb * k + b_ * q + half) // LEVELS))
                blends[key] = v
            return v

        eps = 0.5 / LEVELS
        solid_run = array("H", [solid]) * width if solid >= 0 else None

        def paint(o: int, a: int, b: int, c: float) -> None:
            """Pixels o + a to o + b - 1 at coverage c."""
            if c > 0.999 and solid_run is not None:
                px[o + a:o + b] = solid_run[:b - a]
                return
            q = top if c > 0.999 else round(alpha * c * LEVELS)
            if q <= 0:
                return
            if q >= LEVELS and solid >= 0:
                for i in range(o + a, o + b):
                    px[i] = solid
                return
            for i in range(o + a, o + b):
                px[i] = blend(px[i], min(q, LEVELS))

        for row in range(rows):
            acc: dict[int, float] = {}
            ev: dict[int, float] = {}
            for s in range(sub):
                cr = buckets[row * sub + s]
                if not cr:
                    continue
                cr.sort()
                wind = 0
                start = 0.0
                for x, d in cr:
                    if wind == 0:
                        start = x
                    wind += d
                    if wind == 0:
                        us = (start if start > x_lo else x_lo) - x_lo
                        ue = (x if x < x_hi else x_hi) - x_lo
                        if ue <= us:
                            continue
                        i0, i1 = int(us), int(ue)
                        if i0 == i1:
                            acc[i0] = acc.get(i0, 0.0) + (ue - us) * inv
                        else:
                            acc[i0] = acc.get(i0, 0.0) + (i0 + 1 - us) * inv
                            ev[i0 + 1] = ev.get(i0 + 1, 0.0) + inv
                            ev[i1] = ev.get(i1, 0.0) - inv
                            if ue > i1:
                                acc[i1] = acc.get(i1, 0.0) + (ue - i1) * inv
            if not acc and not ev:
                continue
            o = (y_lo + row) * self.w + x_lo
            run = 0.0
            prev = -1
            # Coverage changes only at these pixels; between them it is constant (run).
            for idx in sorted(set(acc) | set(ev)):
                if idx - prev > 1 and run > eps:
                    paint(o, prev + 1, min(idx, width), run)
                run += ev.get(idx, 0.0)
                c = run + acc.get(idx, 0.0)
                if idx < width and c > eps:
                    paint(o, idx, idx + 1, c)
                prev = idx

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Fill a pixel aligned rectangle with an opaque color."""
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if x0 >= x1:
            return
        run = array("H", [self.color_index(tuple(color))]) * (x1 - x0)
        for y in range(y0, y1):
            o = y * self.w + x0
            self.px[o:o + len(run)] = run

    def rgb(self, x: int, y: int) -> Color:
        return self.palette[self.px[y * self.w + x]]

    def indexed(self, max_colors: int = 256) -> tuple[bytes, list[Color]]:
        """(one byte per pixel, palette of at most ``max_colors``). Keeps the reserved colors and then the most
        used colors; every other color maps to its nearest kept color (ties to the lower index)."""
        used = Counter(self.px)
        if len(self.palette) <= max_colors:
            return bytes(self.px.tolist()), list(self.palette)
        reserved = [i for i in range(self.reserved) if i in used or i == 0]
        rest = sorted((i for i in used if i >= self.reserved), key=lambda i: (-used[i], i))
        kept = sorted(reserved + rest[:max_colors - len(reserved)])
        pal = [self.palette[i] for i in kept]
        lookup = [0] * len(self.palette)
        for n, i in enumerate(kept):
            lookup[i] = n
        keep = set(kept)
        for i in used:
            if i not in keep:
                r, g, b = self.palette[i]
                lookup[i] = min(range(len(pal)), key=lambda n: ((pal[n][0] - r) ** 2 + (pal[n][1] - g) ** 2
                                                                + (pal[n][2] - b) ** 2, n))
        return bytes(map(lookup.__getitem__, self.px)), pal
