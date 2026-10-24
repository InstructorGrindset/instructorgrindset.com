"""Terrain material textures, generated so the mod needs no art from another level.

Each texture is fractal noise made in the frequency domain, so it tiles without seams. A material gets detail
maps (base color, normal, roughness, ambient occlusion, height) tiled every ``DETAIL_M`` metres, and a flat base
color seen from far away; the macro maps (large scale variation) and the other base maps are shared, the way
BeamNG's own levels share them. The colors are drawing choices for a summer mountain, not measurements.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pipeline import png

DETAIL_PX = 512
MACRO_PX = 512
BASE_PX = 256
DETAIL_M = 4
MACRO_M = 80


@dataclass(frozen=True)
class Look:
    key: str
    dark: tuple[int, int, int]
    light: tuple[int, int, int]
    roughness: float
    bump: float          # normal map strength
    feature_px: float    # noise feature size in detail pixels
    seed: int


LOOKS = {
    "grass": Look("grass", (62, 78, 40), (112, 122, 70), 0.9, 2.0, 6, 11),
    "rock": Look("rock", (104, 102, 98), (172, 168, 160), 0.8, 5.0, 14, 12),
    "ski_run": Look("ski_run", (118, 118, 72), (168, 158, 104), 0.9, 1.5, 5, 13),
    "dirt": Look("dirt", (98, 80, 58), (146, 120, 88), 0.95, 3.0, 4, 14),
    "asphalt": Look("asphalt", (40, 40, 42), (78, 78, 80), 0.75, 0.6, 2, 15),
}


def noise(size: int, feature_px: float, seed: int, beta: float = 1.8) -> np.ndarray:
    """Tileable fractal noise in [0, 1]: white noise shaped to a 1 / k^beta spectrum, cut below ``feature_px``."""
    rng = np.random.default_rng(seed)
    f = np.fft.fftfreq(size)
    k = np.hypot(f[None, :], f[:, None])
    k[0, 0] = 1.0
    shape = k ** (-beta / 2) * np.exp(-(k * feature_px) ** 2 / 2)
    shape[0, 0] = 0.0
    field = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((size, size))) * shape))
    lo, hi = np.percentile(field, [1, 99])
    return np.clip((field - lo) / (hi - lo), 0, 1)


def normal_map(height: np.ndarray, strength: float) -> np.ndarray:
    """RGB normal map (0..1) of a tileable height field."""
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) / 2 * strength
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) / 2 * strength
    n = np.stack([-dx, -dy, np.ones_like(height)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def encode_png(a: np.ndarray, level: int = 6) -> bytes:
    """PNG bytes of a uint8 (h, w) grey, uint8 (h, w, 3) RGB or uint16 (h, w) grey image, rows top first. Every row
    uses the Up filter (ISO/IEC 15948, 9.2), computed for the whole image at once."""
    a = np.asarray(a)
    h, w = a.shape[:2]
    if a.dtype == np.uint16 and a.ndim == 2:
        depth, colour, rows = 16, 0, np.ascontiguousarray(a, dtype=">u2").view(np.uint8).reshape(h, 2 * w)
    elif a.dtype == np.uint8 and a.ndim in (2, 3) and (a.ndim == 2 or a.shape[2] == 3):
        depth, colour, rows = 8, 0 if a.ndim == 2 else 2, a.reshape(h, -1)
    else:
        raise ValueError(f"unsupported image {a.dtype} {a.shape}")
    up = np.diff(rows, axis=0, prepend=np.zeros((1, rows.shape[1]), np.uint8)).astype(np.uint8)
    raw = np.concatenate([np.full((h, 1), png.FILTER_UP, np.uint8), up], axis=1)
    ihdr = png.struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, 0)
    return (png.SIGNATURE + png._chunk(b"IHDR", ihdr) + png._chunk(b"IDAT", png.zlib.compress(raw.tobytes(), level))
            + png._chunk(b"IEND", b""))


def to_png(img: np.ndarray) -> bytes:
    """PNG bytes of a (h, w) grey or (h, w, 3) RGB image with values 0..1."""
    return encode_png(np.clip(np.rint(np.asarray(img) * 255), 0, 255).astype(np.uint8))


def detail_maps(look: Look, size: int = DETAIL_PX) -> dict[str, np.ndarray]:
    h = noise(size, look.feature_px, look.seed)
    tint = noise(size, look.feature_px * 3, look.seed + 100)
    t = (0.6 * h + 0.4 * tint)[..., None]
    color = (np.array(look.dark) * (1 - t) + np.array(look.light) * t) / 255
    return {
        "b": color,
        "nm": normal_map(h, look.bump),
        "r": np.clip(look.roughness - 0.1 + 0.2 * (1 - h), 0, 1),
        "ao": 0.75 + 0.25 * h,
        "h": h,
    }


def base_color(look: Look, size: int = BASE_PX) -> np.ndarray:
    mean = (np.array(look.dark) + np.array(look.light)) / 2 / 255
    v = noise(size, size / 16, look.seed + 200)[..., None]
    return np.clip(mean * (0.92 + 0.16 * v), 0, 1) * np.ones((size, size, 3))


def shared_maps(size_base: int = BASE_PX, size_macro: int = MACRO_PX) -> dict[str, np.ndarray]:
    macro = noise(size_macro, size_macro / 24, 7)
    flat = np.ones((size_base, size_base))
    return {
        "base_nm": np.stack([flat * 0.5, flat * 0.5, flat], axis=-1),
        "base_r": flat * 0.9,
        "base_ao": flat,
        "base_h": flat * 0.5,
        "macro_b": 0.4 + 0.2 * macro,
        "macro_nm": normal_map(macro, 1.0),
        "macro_r": 0.85 + 0.1 * macro,
        "macro_ao": 0.85 + 0.15 * macro,
        "macro_h": macro,
    }


def write_all(directory: Path, keys: list[str]) -> dict[str, str]:
    """Write every texture for the given looks; returns {texture key: file name}."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, img in shared_maps().items():
        files[name] = f"t_{name}.png"
        (directory / files[name]).write_bytes(to_png(img))
    for key in keys:
        look = LOOKS[key]
        files[f"{key}_base_b"] = f"t_{key}_base_b.png"
        (directory / files[f"{key}_base_b"]).write_bytes(to_png(base_color(look)))
        for kind, img in detail_maps(look).items():
            files[f"{key}_{kind}"] = f"t_{key}_{kind}.png"
            (directory / files[f"{key}_{kind}"]).write_bytes(to_png(img))
    return files
