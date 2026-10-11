"""Name normalisation, slugs and similarity for matching resorts across sources."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Generic words that say nothing about which resort a name refers to.
STOPWORDS = frozenset({
    "ski", "skiing", "area", "areas", "resort", "resorts", "mountain", "mountains", "mtn", "mt",
    "the", "of", "at", "and", "snowboard", "bowl", "co", "inc", "llc",
})
# "Mount" is kept as a token (Mount Snow), but "mt" abbreviations normalise to it first.
_ABBREV = {"mt": "mount", "st": "saint"}


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def slugify(text: str) -> str:
    s = ascii_fold(text).lower().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def name_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", ascii_fold(name).lower().replace("'", ""))
    words = [_ABBREV.get(w, w) for w in words]
    kept = [w for w in words if w not in STOPWORDS]
    return kept or words


def name_similarity(a: str, b: str) -> float:
    """0 to 1. Token subset (``Vail`` in ``Vail Ski Resort``) scores at least 0.8."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return 1.0
    small, large = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if small <= large:
        ratio = max(ratio, 0.8)
    return ratio
