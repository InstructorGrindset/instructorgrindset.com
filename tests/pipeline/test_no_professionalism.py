"""Owner decision 2026-10-13: the professionalism rating is off the public site.

Everything under data/build is publicly served, and every route stub under /map/ embeds a resort record, so none
of them may carry the operator scale or any professionalism score or component. data/raw/tier_a keeps
operator_scale as an internal note.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BANNED = ("operator_scale", "professionalism", "Professionalism")


def _hits(paths):
    out = []
    for f in paths:
        text = f.read_text(encoding="utf-8", errors="replace")
        out += [f"{f.relative_to(REPO)}: {k}" for k in BANNED if k in text]
    return out


def test_no_file_in_data_build_carries_the_rating():
    files = [f for f in (REPO / "data" / "build").rglob("*") if f.is_file() and f.suffix in (".json", ".geojson")]
    assert files, "data/build is empty"
    assert _hits(files) == []


def test_no_route_stub_or_map_module_carries_the_rating():
    stubs = list((REPO / "map").rglob("index.html"))
    assert len(stubs) > 100
    assert _hits(stubs + list((REPO / "map" / "js").glob("*.js"))) == []


def test_fixtures_mirror_the_contract():
    files = [f for f in (REPO / "tests" / "map" / "fixtures").rglob("*") if f.is_file() and f.suffix in (".json", ".geojson")]
    assert _hits(files) == []
