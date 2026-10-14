"""Read glyph outlines, advances and pair kerning from an OpenType CFF font (stdlib only).

The share cards (``pipeline/share.py``) draw text with vector outlines, so the build needs no imaging library and no
font on the machine. This module is the one time step that turns a font file into the small committed outline file
the build reads (``pipeline/fonts/*.json``, docs/map/ARCHITECTURE.md, "Share cards"):

    python -m pipeline.otf /usr/share/fonts/opentype/inter/Inter-Bold.otf pipeline/fonts/inter-bold.json

It reads the ``cmap`` (formats 4 and 12), ``head``, ``hhea``, ``hmtx``, ``OS/2`` and ``CFF `` tables (Type 2
charstrings with local and global subroutines, hints skipped) and the pair kerning of the ``kern`` feature in
``GPOS`` (lookup type 2, formats 1 and 2, also inside extension lookups). Only the characters in ``CHARSET`` are kept.
The output keeps each glyph as an absolute path string in font units: ``M x y``, ``L x y``, ``C x1 y1 x2 y2 x y``
and ``Z``, with y pointing up.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Optional

# Printable ASCII, Latin 1 letters and signs, and the typographic quotes and dashes names may carry.
CHARSET = ("".join(chr(c) for c in range(0x20, 0x7F)) + "".join(chr(c) for c in range(0xA0, 0x100))
           + "‘’“”–—•…")


class FontError(ValueError):
    pass


def _u16(d: bytes, o: int) -> int:
    return struct.unpack_from(">H", d, o)[0]


def _i16(d: bytes, o: int) -> int:
    return struct.unpack_from(">h", d, o)[0]


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from(">I", d, o)[0]


def tables(data: bytes) -> dict[str, bytes]:
    """Table tag to table bytes of an OpenType file."""
    if data[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        raise FontError("not an OpenType or TrueType font")
    out = {}
    for i in range(_u16(data, 4)):
        tag, _, off, ln = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        out[tag.decode("latin-1")] = data[off:off + ln]
    return out


# ── cmap ──

def cmap(table: bytes) -> dict[int, int]:
    """Unicode code point to glyph id, from the best Unicode subtable (format 12, else format 4)."""
    best: Optional[tuple[int, int]] = None
    for i in range(_u16(table, 2)):
        pid, eid, off = struct.unpack_from(">HHI", table, 4 + 8 * i)
        fmt = _u16(table, off)
        if (pid, eid) in ((3, 10), (0, 4), (0, 6)) and fmt == 12:
            best = (0, off)
        elif (pid, eid) in ((3, 1), (0, 3), (0, 1), (0, 0)) and fmt == 4 and (best is None or best[0] > 1):
            best = (1, off)
    if best is None:
        raise FontError("no Unicode cmap subtable (format 4 or 12)")
    off = best[1]
    out: dict[int, int] = {}
    if _u16(table, off) == 12:
        for g in range(_u32(table, off + 12)):
            start, end, gid = struct.unpack_from(">III", table, off + 16 + 12 * g)
            for c in range(start, end + 1):
                out[c] = gid + c - start
        return out
    seg2 = _u16(table, off + 6)
    ends, starts = off + 14, off + 16 + seg2
    deltas, ranges = starts + seg2, starts + 2 * seg2
    for s in range(seg2 // 2):
        end, start = _u16(table, ends + 2 * s), _u16(table, starts + 2 * s)
        delta, ro = _i16(table, deltas + 2 * s), _u16(table, ranges + 2 * s)
        for c in range(start, end + 1):
            if c == 0xFFFF:
                continue
            if ro == 0:
                gid = (c + delta) & 0xFFFF
            else:
                gid = _u16(table, ranges + 2 * s + ro + 2 * (c - start))
                gid = (gid + delta) & 0xFFFF if gid else 0
            if gid:
                out[c] = gid
    return out


# ── CFF ──

def _index(d: bytes, o: int) -> tuple[list[bytes], int]:
    """Items of the CFF INDEX at ``o`` and the offset just after it."""
    count = _u16(d, o)
    if count == 0:
        return [], o + 2
    size = d[o + 2]
    offs = [int.from_bytes(d[o + 3 + i * size:o + 3 + (i + 1) * size], "big") for i in range(count + 1)]
    base = o + 2 + (count + 1) * size
    return [d[base + offs[i]:base + offs[i + 1]] for i in range(count)], base + offs[-1]


def _dict(d: bytes) -> dict[int, list[float]]:
    """Operator to operands of a CFF DICT (two byte operators as 1200 + b1)."""
    out: dict[int, list[float]] = {}
    ops: list[float] = []
    i = 0
    while i < len(d):
        b = d[i]
        if b <= 21:
            if b == 12:
                key, i = 1200 + d[i + 1], i + 2
            else:
                key, i = b, i + 1
            out[key], ops = ops, []
        elif b == 28:
            ops.append(_i16(d, i + 1)); i += 3
        elif b == 29:
            ops.append(struct.unpack_from(">i", d, i + 1)[0]); i += 5
        elif b == 30:
            s, i = "", i + 1
            while True:
                hi, lo = d[i] >> 4, d[i] & 15
                i += 1
                done = False
                for n in (hi, lo):
                    if n == 15:
                        done = True
                        break
                    s += "0123456789.EE?-"[n] + ("-" if n == 12 else "")
                if done:
                    break
            ops.append(float(s.replace("E-", "e-").replace("E", "e")))
        elif 32 <= b <= 246:
            ops.append(b - 139); i += 1
        elif 247 <= b <= 250:
            ops.append((b - 247) * 256 + d[i + 1] + 108); i += 2
        elif 251 <= b <= 254:
            ops.append(-(b - 251) * 256 - d[i + 1] - 108); i += 2
        else:
            raise FontError(f"bad CFF DICT byte {b}")
    return out


def _bias(n: int) -> int:
    return 107 if n < 1240 else 1131 if n < 33900 else 32768


class Charstrings:
    """Type 2 charstring interpreter for a non CID CFF font."""

    def __init__(self, cff: bytes):
        _, _, hdr, _ = struct.unpack_from(">BBBB", cff, 0)
        _, o = _index(cff, hdr)
        top, o = _index(cff, o)
        _, o = _index(cff, o)
        self.gsubrs, _ = _index(cff, o)
        td = _dict(top[0])
        if 1230 in td:
            raise FontError("CID keyed CFF fonts are not supported")
        if td.get(1206, [2])[0] != 2:
            raise FontError("only Type 2 charstrings are supported")
        self.glyphs, _ = _index(cff, int(td[17][0]))
        psize, poff = (int(x) for x in td[18])
        pd = _dict(cff[poff:poff + psize])
        self.subrs = _index(cff, poff + int(pd[19][0]))[0] if 19 in pd else []

    def outline(self, gid: int) -> list[list[tuple]]:
        """Contours of glyph ``gid``: each a list of ("M", x, y), ("L", x, y) or ("C", x1, y1, x2, y2, x, y)."""
        st: dict[str, Any] = {"stack": [], "x": 0.0, "y": 0.0, "stems": 0, "width": False, "contours": [], "done": False}
        self._run(self.glyphs[gid], st, 0)
        return [c for c in st["contours"] if len(c) > 1]

    def _run(self, code: bytes, st: dict, depth: int) -> None:
        if depth > 10:
            raise FontError("charstring subroutines nest too deep")
        stack = st["stack"]
        contours = st["contours"]
        i = 0

        def width(n_expected_even: bool, n: int) -> None:
            # The first stack clearing operator may carry the advance width as an extra first operand.
            if not st["width"]:
                st["width"] = True
                if (len(stack) % 2 == 1) if n_expected_even else (len(stack) > n):
                    del stack[0]

        def move(dx: float, dy: float) -> None:
            st["x"] += dx; st["y"] += dy
            contours.append([("M", st["x"], st["y"])])

        def line(dx: float, dy: float) -> None:
            st["x"] += dx; st["y"] += dy
            contours[-1].append(("L", st["x"], st["y"]))

        def curve(a: float, b: float, c: float, d: float, e: float, f: float) -> None:
            x1, y1 = st["x"] + a, st["y"] + b
            x2, y2 = x1 + c, y1 + d
            st["x"], st["y"] = x2 + e, y2 + f
            contours[-1].append(("C", x1, y1, x2, y2, st["x"], st["y"]))

        while i < len(code) and not st["done"]:
            b = code[i]
            if b >= 32 or b == 28:
                if b == 28:
                    stack.append(_i16(code, i + 1)); i += 3
                elif b <= 246:
                    stack.append(b - 139); i += 1
                elif b <= 250:
                    stack.append((b - 247) * 256 + code[i + 1] + 108); i += 2
                elif b <= 254:
                    stack.append(-(b - 251) * 256 - code[i + 1] - 108); i += 2
                else:
                    stack.append(struct.unpack_from(">i", code, i + 1)[0] / 65536.0); i += 5
                continue
            i += 1
            if b in (1, 3, 18, 23):  # hstem, vstem, hstemhm, vstemhm
                width(True, 0)
                st["stems"] += len(stack) // 2
                stack.clear()
            elif b in (19, 20):  # hintmask, cntrmask
                width(True, 0)
                st["stems"] += len(stack) // 2
                stack.clear()
                i += (st["stems"] + 7) // 8
            elif b == 21:  # rmoveto
                width(False, 2)
                move(stack[-2], stack[-1]); stack.clear()
            elif b == 22:  # hmoveto
                width(False, 1)
                move(stack[-1], 0); stack.clear()
            elif b == 4:  # vmoveto
                width(False, 1)
                move(0, stack[-1]); stack.clear()
            elif b == 5:  # rlineto
                for k in range(0, len(stack) - 1, 2):
                    line(stack[k], stack[k + 1])
                stack.clear()
            elif b in (6, 7):  # hlineto, vlineto
                horiz = b == 6
                for v in stack:
                    line(v, 0) if horiz else line(0, v)
                    horiz = not horiz
                stack.clear()
            elif b == 8:  # rrcurveto
                for k in range(0, len(stack) - 5, 6):
                    curve(*stack[k:k + 6])
                stack.clear()
            elif b == 24:  # rcurveline
                k = 0
                while len(stack) - k >= 8:
                    curve(*stack[k:k + 6]); k += 6
                line(stack[k], stack[k + 1]); stack.clear()
            elif b == 25:  # rlinecurve
                k = 0
                while len(stack) - k >= 8:
                    line(stack[k], stack[k + 1]); k += 2
                curve(*stack[k:k + 6]); stack.clear()
            elif b == 26:  # vvcurveto
                k, dx1 = 0, 0
                if len(stack) % 2:
                    dx1, k = stack[0], 1
                while k + 4 <= len(stack):
                    curve(dx1, stack[k], stack[k + 1], stack[k + 2], 0, stack[k + 3]); dx1 = 0; k += 4
                stack.clear()
            elif b == 27:  # hhcurveto
                k, dy1 = 0, 0
                if len(stack) % 2:
                    dy1, k = stack[0], 1
                while k + 4 <= len(stack):
                    curve(stack[k], dy1, stack[k + 1], stack[k + 2], stack[k + 3], 0); dy1 = 0; k += 4
                stack.clear()
            elif b in (30, 31):  # vhcurveto, hvcurveto
                horiz = b == 31
                k = 0
                while k + 4 <= len(stack):
                    last = stack[k + 4] if len(stack) - k == 5 else 0
                    a, c, d, e = stack[k:k + 4]
                    if horiz:
                        curve(a, 0, c, d, last, e)
                    else:
                        curve(0, a, c, d, e, last)
                    horiz = not horiz
                    k += 4
                stack.clear()
            elif b in (10, 29):  # callsubr, callgsubr
                subrs = self.subrs if b == 10 else self.gsubrs
                n = int(stack.pop()) + _bias(len(subrs))
                self._run(subrs[n], st, depth + 1)
            elif b == 11:  # return
                return
            elif b == 14:  # endchar
                width(False, 0)
                if len(stack) >= 4:
                    raise FontError("seac accented endchar is not supported")
                stack.clear()
                st["done"] = True
            elif b == 12:
                b2 = code[i]
                i += 1
                s = stack
                if b2 == 35:  # flex
                    curve(*s[0:6]); curve(*s[6:12])
                elif b2 == 34:  # hflex
                    curve(s[0], 0, s[1], s[2], s[3], 0); curve(s[4], 0, s[5], -s[2], s[6], 0)
                elif b2 == 36:  # hflex1
                    curve(s[0], s[1], s[2], s[3], s[4], 0); curve(s[5], 0, s[6], s[7], s[8], -(s[1] + s[3] + s[7]))
                elif b2 == 37:  # flex1
                    dx = s[0] + s[2] + s[4] + s[6] + s[8]
                    dy = s[1] + s[3] + s[5] + s[7] + s[9]
                    e, f = (s[10], -dy) if abs(dx) > abs(dy) else (-dx, s[10])
                    curve(*s[0:6]); curve(s[6], s[7], s[8], s[9], e, f)
                stack.clear()
            else:
                raise FontError(f"unsupported charstring operator {b}")


# ── GPOS pair kerning ──

def _coverage(t: bytes, o: int) -> dict[int, int]:
    fmt = _u16(t, o)
    if fmt == 1:
        return {_u16(t, o + 4 + 2 * i): i for i in range(_u16(t, o + 2))}
    out = {}
    for r in range(_u16(t, o + 2)):
        start, end, idx = struct.unpack_from(">HHH", t, o + 4 + 6 * r)
        for g in range(start, end + 1):
            out[g] = idx + g - start
    return out


def _classdef(t: bytes, o: int) -> dict[int, int]:
    fmt = _u16(t, o)
    if fmt == 1:
        start = _u16(t, o + 2)
        return {start + i: _u16(t, o + 6 + 2 * i) for i in range(_u16(t, o + 4))}
    out = {}
    for r in range(_u16(t, o + 2)):
        start, end, cls = struct.unpack_from(">HHH", t, o + 4 + 6 * r)
        for g in range(start, end + 1):
            out[g] = cls
    return out


def _vr_size(fmt: int) -> int:
    return 2 * bin(fmt & 0xFF).count("1")


def _x_advance(t: bytes, o: int, fmt: int) -> int:
    """XAdvance of the ValueRecord at ``o`` (0 when the format has none)."""
    if not fmt & 4:
        return 0
    return _i16(t, o + 2 * bin(fmt & 3).count("1"))


def _pair_subtable(t: bytes, o: int, glyphs: set[int]) -> Optional[dict[tuple[int, int], Optional[int]]]:
    """(left, right) to XAdvance for the pairs among ``glyphs``; a value of None marks a covered pair with no
    entry (format 1), which lets a later subtable of the same lookup apply."""
    fmt = _u16(t, o)
    cov = _coverage(t, o + _u16(t, o + 2))
    vf1, vf2 = _u16(t, o + 4), _u16(t, o + 6)
    out: dict[tuple[int, int], Optional[int]] = {}
    if fmt == 1:
        rec = 2 + _vr_size(vf1) + _vr_size(vf2)
        for left, ci in cov.items():
            if left not in glyphs:
                continue
            ps = o + _u16(t, o + 10 + 2 * ci)
            for k in range(_u16(t, ps)):
                r = ps + 2 + k * rec
                right = _u16(t, r)
                if right in glyphs:
                    out[(left, right)] = _x_advance(t, r + 2, vf1)
        return out
    if fmt == 2:
        c1 = _classdef(t, o + _u16(t, o + 8))
        c2 = _classdef(t, o + _u16(t, o + 10))
        n1, n2 = _u16(t, o + 12), _u16(t, o + 14)
        rec = _vr_size(vf1) + _vr_size(vf2)
        for left in cov:
            if left not in glyphs:
                continue
            k1 = c1.get(left, 0)
            if k1 >= n1:
                continue
            for right in glyphs:
                k2 = c2.get(right, 0)
                if k2 < n2:
                    out[(left, right)] = _x_advance(t, o + 16 + (k1 * n2 + k2) * rec, vf1)
        return out
    return None


def kerning(gpos: bytes, glyphs: set[int]) -> dict[tuple[int, int], int]:
    """Summed XAdvance pair adjustments of every lookup the ``kern`` feature uses, for pairs among ``glyphs``."""
    feats, lookups = _u16(gpos, 6), _u16(gpos, 8)
    idx: set[int] = set()
    for f in range(_u16(gpos, feats)):
        tag, off = struct.unpack_from(">4sH", gpos, feats + 2 + 6 * f)
        if tag == b"kern":
            ft = feats + off
            idx.update(_u16(gpos, ft + 4 + 2 * k) for k in range(_u16(gpos, ft + 2)))
    total: dict[tuple[int, int], int] = {}
    for li in sorted(idx):
        lt = lookups + _u16(gpos, lookups + 2 + 2 * li)
        kind = _u16(gpos, lt)
        seen: dict[tuple[int, int], int] = {}
        for s in range(_u16(gpos, lt + 4)):
            so = lt + _u16(gpos, lt + 6 + 2 * s)
            sub_kind = kind
            if kind == 9:
                sub_kind, so = _u16(gpos, so + 2), so + _u32(gpos, so + 4)
            if sub_kind != 2:
                continue
            for pair, v in (_pair_subtable(gpos, so, glyphs) or {}).items():
                if pair not in seen and v is not None:
                    seen[pair] = v
        for pair, v in seen.items():
            if v:
                total[pair] = total.get(pair, 0) + v
    return {p: v for p, v in total.items() if v}


# ── Output ──

def _num(v: float) -> str:
    r = round(v)
    return str(int(r))


def path_string(contours: list[list[tuple]]) -> str:
    parts = []
    for c in contours:
        for seg in c:
            parts.append(seg[0] + " ".join(_num(v) for v in seg[1:]))
        parts.append("Z")
    return "".join(parts)


def extract(font_path: Path, charset: str = CHARSET) -> dict:
    """The outline file content for ``charset`` (characters the font lacks are left out)."""
    t = tables(Path(font_path).read_bytes())
    if "CFF " not in t:
        raise FontError("only CFF outlines (.otf) are supported")
    head, hhea, hmtx, os2 = t["head"], t["hhea"], t["hmtx"], t.get("OS/2", b"")
    upem = _u16(head, 18)
    n_metrics = _u16(hhea, 34)
    cmap_ = cmap(t["cmap"])
    cs = Charstrings(t["CFF "])

    def advance(gid: int) -> int:
        return _u16(hmtx, 4 * min(gid, n_metrics - 1))

    gids = {ch: cmap_[ord(ch)] for ch in charset if ord(ch) in cmap_}
    glyphs = {ch: [advance(g), path_string(cs.outline(g))] for ch, g in gids.items()}
    by_gid: dict[int, list[str]] = {}
    for ch, g in gids.items():
        by_gid.setdefault(g, []).append(ch)
    kern: dict[str, int] = {}
    if "GPOS" in t:
        for (a, b), v in kerning(t["GPOS"], set(by_gid)).items():
            for ca in by_gid[a]:
                for cb in by_gid[b]:
                    kern[ca + cb] = v
    cap = _i16(os2, 88) if len(os2) >= 90 else None
    xh = _i16(os2, 86) if len(os2) >= 88 else None
    return {
        "units_per_em": upem, "ascender": _i16(hhea, 4), "descender": _i16(hhea, 6),
        "cap_height": cap, "x_height": xh,
        "glyphs": dict(sorted(glyphs.items())), "kern": dict(sorted(kern.items())),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Write the share card outline file from an OpenType CFF font.")
    ap.add_argument("font", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--name", help="font name recorded in the file (default: the file name)")
    args = ap.parse_args(argv)
    data = extract(args.font)
    out = {"name": args.name or args.font.stem, "source": args.font.name, "license": "OFL-1.1 (see OFL.txt)", **data}
    args.out.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"otf: wrote {len(data['glyphs'])} glyphs and {len(data['kern'])} kerning pairs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
