"""Color math for the heat map palettes: OKLab (Bjorn Ottosson) and simulated color vision deficiency
(Machado, Oliveira and Fernandes 2009, severity 1.0, applied in linear sRGB). Distances are Euclidean in
OKLab, times 100, the scale the dataviz palette checks use (6 is a floor, 8 or more is comfortable)."""
from __future__ import annotations

import math
from typing import Optional

MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(_lin(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))  # type: ignore[return-value]


def oklab_from_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab(hex_color: str) -> tuple[float, float, float]:
    return oklab_from_linear(linear_rgb(hex_color))


def simulate(hex_color: str, kind: str) -> tuple[float, float, float]:
    """Linear sRGB as seen with ``kind`` (protan, deutan or tritan) color vision."""
    r, g, b = linear_rgb(hex_color)
    return tuple(max(0.0, min(1.0, row[0] * r + row[1] * g + row[2] * b)) for row in MACHADO[kind])  # type: ignore[return-value]


def delta_e(a: str, b: str, kind: Optional[str] = None) -> float:
    """OKLab distance x 100 between two hex colors, for normal vision or a simulated deficiency."""
    la = oklab_from_linear(simulate(a, kind)) if kind else oklab(a)
    lb = oklab_from_linear(simulate(b, kind)) if kind else oklab(b)
    return 100 * math.dist(la, lb)


def relative_luminance(hex_color: str) -> float:
    r, g, b = linear_rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
