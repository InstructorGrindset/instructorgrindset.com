"""Vector text for the share cards: outline fonts from ``pipeline/fonts/*.json`` (written once by ``pipeline/otf.py``),
laid out with advance widths and pair kerning, flattened to rings for ``pipeline/raster.py``. Stdlib only.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pipeline.raster import Ring

FONTS = Path(__file__).resolve().parent / "fonts"
_TOKEN = re.compile(r"[MLCZ]|-?\d+")


def _parse(path: str) -> list[list[tuple]]:
    contours: list[list[tuple]] = []
    toks = _TOKEN.findall(path)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "M":
            contours.append([("M", int(toks[i + 1]), int(toks[i + 2]))]); i += 3
        elif t == "L":
            contours[-1].append(("L", int(toks[i + 1]), int(toks[i + 2]))); i += 3
        elif t == "C":
            contours[-1].append(("C", *(int(v) for v in toks[i + 1:i + 7]))); i += 7
        elif t == "Z":
            i += 1
        else:
            raise ValueError(f"bad glyph path token {t!r}")
    return contours


class Font:
    def __init__(self, data: dict):
        self.name = data.get("name", "")
        self.upem = data["units_per_em"]
        self.cap_height = data.get("cap_height") or round(self.upem * 0.73)
        self.glyphs = {ch: (adv, _parse(path)) for ch, (adv, path) in data["glyphs"].items()}
        self.kern = data.get("kern", {})

    def char(self, ch: str) -> Optional[str]:
        """The character drawn for ``ch``: itself, its unaccented base letter, else None."""
        if ch in self.glyphs:
            return ch
        base = unicodedata.normalize("NFKD", ch)[:1]
        return base if base in self.glyphs else None

    def _chars(self, text: str) -> list[str]:
        return [c for c in (self.char(ch) for ch in text) if c is not None]

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        """Advance width of ``text`` in pixels at ``size`` (the em size), ``tracking`` in em per character."""
        chars = self._chars(text)
        units = sum(self.glyphs[c][0] for c in chars) + sum(self.kern.get(a + b, 0) for a, b in zip(chars, chars[1:]))
        return units * size / self.upem + tracking * size * max(0, len(chars) - 1)

    def rings(self, text: str, size: float, x: float, baseline: float, tracking: float = 0.0) -> list[Ring]:
        """Flattened outline rings of ``text`` with its left edge at ``x`` and its baseline at ``baseline``."""
        s = size / self.upem
        out: list[Ring] = []
        pen = 0.0
        chars = self._chars(text)
        for k, c in enumerate(chars):
            adv, contours = self.glyphs[c]
            ox = x + pen * s + tracking * size * k
            for contour in contours:
                ring: Ring = []
                cx = cy = 0.0
                for seg in contour:
                    if seg[0] in ("M", "L"):
                        cx, cy = seg[1], seg[2]
                        ring.append((ox + cx * s, baseline - cy * s))
                    else:
                        x1, y1, x2, y2, x3, y3 = seg[1:]
                        est = (math.hypot(x1 - cx, y1 - cy) + math.hypot(x2 - x1, y2 - y1)
                               + math.hypot(x3 - x2, y3 - y2)) * s
                        n = max(2, min(32, math.ceil(math.sqrt(est) * 1.6)))
                        for j in range(1, n + 1):
                            t = j / n
                            u = 1 - t
                            bx = u * u * u * cx + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3
                            by = u * u * u * cy + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3
                            ring.append((ox + bx * s, baseline - by * s))
                        cx, cy = x3, y3
                if len(ring) >= 3:
                    out.append(ring)
            pen += adv + (self.kern.get(c + chars[k + 1], 0) if k + 1 < len(chars) else 0)
        return out


@lru_cache(maxsize=None)
def load(name: str) -> Font:
    """The committed outline font ``pipeline/fonts/<name>.json``."""
    return Font(json.loads((FONTS / f"{name}.json").read_text(encoding="utf-8")))


def wrap(font: Font, text: str, size: float, max_width: float, tracking: float = 0.0) -> list[str]:
    """Greedy word wrap; a single word wider than ``max_width`` keeps its own line."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and font.width(trial, size, tracking) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit(font: Font, text: str, sizes: list[float], max_width: float, max_height: float, line_height: float = 1.08,
        max_lines: int = 4) -> tuple[float, list[str]]:
    """The largest size in ``sizes`` whose wrapped lines fit ``max_width`` and ``max_height``; falls back to the
    smallest size, trimming the last line with an ellipsis."""
    for size in sizes:
        lines = wrap(font, text, size, max_width)
        if (len(lines) <= max_lines and len(lines) * size * line_height <= max_height
                and all(font.width(ln, size) <= max_width for ln in lines)):
            return size, lines
    size = sizes[-1]
    n = max(1, min(max_lines, int(max_height // (size * line_height))))
    lines = wrap(font, text, size, max_width)
    if len(lines) > n:
        lines = lines[:n]
        last = lines[-1]
        while last and font.width(last + "…", size) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return size, lines
