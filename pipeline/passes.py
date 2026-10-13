"""Multi resort pass membership (ROADMAP item 12, the Epic and Ikon filter).

data/raw/passes/<season>.yaml lists, per resort, the pass families it is on for a season (Epic, Ikon, Mountain
Collective, Indy) with the access level of the family's top pass where the pass company states it. The build copies
the memberships of the configured season (config/definitions.yaml passes.season) to Resort.passes, and
pipeline/routes.py puts the pass ids in the slim map index for the filter chips.

Every membership cites a page on its pass company's own site (passes.domains), or a supplemental reference
(passes.supplemental_domains). Ownership never implies a pass: a Vail Resorts resort is on Epic only when a source
says so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .links import domain_of, load_batches
from .schemas.passes import PASS_ORDER, PassBatch, PassMembership
from .sources import SourceRegistry

RAW_SUBDIR = "passes"


def load_passes(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], season: str,
                domains: dict[str, set[str]], supplemental: set[str] = frozenset(), rel=str
                ) -> tuple[dict[str, list[PassMembership]], list[str], list[str]]:
    """Validate data/raw/passes/*.yaml. Returns ({resort_id: memberships of ``season``, in pass order}, errors,
    warnings). No folder gives ({}, [], []).

    Errors: schema problems (bad enum, days without limited access, a bad season, an unknown source), an unknown
    resort, the same resort, pass and season twice across files, and a source whose domain is not the pass
    company's own (or a supplemental reference). Memberships of other seasons are validated and left out.
    """
    batches, errors = load_batches(raw_dir, RAW_SUBDIR, PassBatch, registry, rel,
                                   record_lists=(("memberships", "resort_id"),))
    warnings: list[str] = []
    out: dict[str, list[PassMembership]] = {}
    where: dict[tuple[str, str, str], str] = {}
    other_seasons: dict[str, int] = {}
    for path, batch in batches:
        for m in batch.memberships:
            label = f"{path} membership '{m.resort_id}' on {m.pass_id}"
            if m.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            key = (m.resort_id, m.pass_id, m.season)
            if key in where and where[key] != path:
                errors.append(f"{label}: also listed for {m.season} in {where[key]}")
                continue
            where[key] = path
            if m.source_id in registry:
                dom = domain_of(registry.get(m.source_id).url)
                allowed = set(domains.get(m.pass_id) or ()) | set(supplemental)
                if dom not in allowed:
                    errors.append(f"{label}: source '{m.source_id}' is on {dom or 'no domain'}; cite the pass "
                                  f"company's own site ({', '.join(sorted(domains.get(m.pass_id) or ())) or 'none set'})")
            if m.season != season:
                other_seasons[m.season] = other_seasons.get(m.season, 0) + 1
                continue
            if m.resort_id in known_ids:
                out.setdefault(m.resort_id, []).append(m.membership())
    for s, n in sorted(other_seasons.items()):
        warnings.append(f"data/raw/passes: {n} memberships for {s} left out (passes.season is {season})")
    for rid in out:
        out[rid].sort(key=lambda x: PASS_ORDER.index(x.pass_id))
    return out, errors, warnings


def as_output(memberships: Optional[list[PassMembership]]) -> list[dict]:
    """Resort.passes in resorts.json: the memberships in pass order, [] for none."""
    return [m.output() for m in memberships or []]

