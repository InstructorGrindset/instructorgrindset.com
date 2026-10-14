"""Jobs links per resort (owner request 2026-10-13).

data/raw/jobs/<batch>.yaml declares employers (parent companies, resort operators, clubs, ski schools) and, per
resort, links to ski school, coaching and club, management, corporate and general job pages. The build copies the
links to Resort.jobs and writes every employer once to data/build/jobs/employers.json. Live postings come from
pipeline/jobs_feed.py (public job board APIs only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .hiring import parse_window
from .links import domain_of, load_batches
from .config import HiringWindowDefs
from .schemas.jobs import ROLE_GROUPS, Employer, EmployerOut, HiringWindowRecord, JobLink, JobsBatch
from .sources import SourceRegistry

RAW_SUBDIR = "jobs"
OUT_SUBDIR = "jobs"
EMPLOYERS_NAME = "employers.json"
POSTINGS_NAME = "postings.json"

# Public board page of each vendor with a postings API: the source cited for its postings.
BOARD_PAGE = {
    "greenhouse": "https://boards.greenhouse.io/{board}",
    "lever": "https://jobs.lever.co/{board}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{board}",
    "workable": "https://apply.workable.com/{board}/",
    "ashby": "https://jobs.ashbyhq.com/{board}",
}


def board_url(employer: Employer) -> Optional[str]:
    """The employer's public job board page, when it has a board with a postings API."""
    if not employer.ats.feed:
        return None
    return BOARD_PAGE[employer.ats.vendor].format(board=employer.ats.board)


@dataclass
class JobsData:
    employers: dict[str, Employer] = field(default_factory=dict)
    links: dict[str, list[JobLink]] = field(default_factory=dict)   # resort_id -> links, file order
    hiring_windows: dict[str, HiringWindowRecord] = field(default_factory=dict)   # resort_id -> window

    def allowed_domains(self, employer_id: str, ats_domains: dict[str, set[str]]) -> set[str]:
        e = self.employers[employer_id]
        return {domain_of(e.careers_url)} | set(ats_domains.get(e.ats.vendor, set()))


def _same(a: Employer, b: Employer) -> bool:
    keys = ("name", "kind", "careers_url", "ats")
    return a.model_dump(include=set(keys)) == b.model_dump(include=set(keys))


def load_jobs(raw_dir: Path, registry: SourceRegistry, known_ids: set[str], ats_domains: dict[str, set[str]],
              rel=str, blocked_domains: frozenset[str] | set[str] = frozenset()) -> tuple[JobsData, list[str]]:
    """Validate data/raw/jobs/*.yaml. No folder gives empty data and no errors.

    Fails (returns errors) on an unknown resort, employer or source id; a link whose domain is none of its employer's
    careers domain, ATS domains and its own source's domain, or is a blocked aggregator; an employer declared twice
    with different details; a url listed twice in one role group of a resort; and two hiring windows for one resort.
    An ats.board on a vendor without a public postings API (Workday, iCIMS and the rest) is kept; the feed never
    reads it.
    """
    batches, errors = load_batches(raw_dir, RAW_SUBDIR, JobsBatch, registry, rel,
                                   record_lists=(("records", "resort_id"), ("employers", "id"),
                                                 ("hiring_windows", "resort_id")))
    data = JobsData()
    where: dict[str, str] = {}
    for path, batch in batches:
        for e in batch.employers:
            if e.id in data.employers:
                if not _same(e, data.employers[e.id]):
                    errors.append(f"{path} employer '{e.id}': declared with different details in {where[e.id]}")
                continue
            data.employers[e.id] = e
            where[e.id] = path
    seen: dict[tuple[str, str, str], str] = {}
    for path, batch in batches:
        for r in batch.records:
            label = f"{path} resort '{r.resort_id}'"
            if r.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            for link in r.links:
                key = (r.resort_id, link.role_group, link.url)
                if key in seen:
                    errors.append(f"{label}: {link.role_group} url {link.url} is also listed in {seen[key]}")
                    continue
                seen[key] = path
                if link.employer_id not in data.employers:
                    errors.append(f"{label}: employer_id '{link.employer_id}' is not declared in any jobs file")
                    continue
                allowed = data.allowed_domains(link.employer_id, ats_domains)
                if link.source_id in registry:
                    allowed.add(domain_of(registry.get(link.source_id).url))
                dom = domain_of(link.url)
                if dom in blocked_domains:
                    errors.append(f"{label}: link '{link.label}' is on the job aggregator {dom}; link the employer's "
                                  "own careers page or job board")
                elif dom not in allowed:
                    errors.append(f"{label}: link '{link.label}' url domain {dom} is neither the careers domain, an ATS "
                                  f"domain of '{link.employer_id}' nor its source's domain ({', '.join(sorted(allowed))})")
                data.links.setdefault(r.resort_id, []).append(link)
    window_from: dict[str, str] = {}
    for path, batch in batches:
        for hw in batch.hiring_windows:
            label = f"{path} hiring window '{hw.resort_id}'"
            if hw.resort_id not in known_ids:
                errors.append(f"{label}: no such resort in this build")
            if hw.resort_id in data.hiring_windows:
                errors.append(f"{label}: also has a hiring window in {window_from[hw.resort_id]}")
                continue
            data.hiring_windows[hw.resort_id] = hw
            window_from[hw.resort_id] = path
    return data, errors


def hiring_window_output(window: Optional[HiringWindowRecord], max_months: int) -> Optional[dict]:
    """Resort.jobs.hiring_window: the source's text and citation, plus start_month and end_month (YYYY-MM) when
    pipeline/hiring.py can read both from the text, else null."""
    if window is None:
        return None
    months = parse_window(window.text, window.as_of, max_months)
    return {**window.model_dump(mode="json", exclude={"resort_id"}),
            "start_month": months.start if months else None, "end_month": months.end if months else None}


def resort_jobs(data: JobsData, resort_id: str, max_months: int = HiringWindowDefs().max_months) -> Optional[dict]:
    """Resort.jobs in resorts.json: links ordered by role group (ski school, coaching, management, corporate, all),
    file order within a group, whether a linked employer has a live postings feed, and the hiring window.
    ``max_months`` is config jobs.hiring_windows.max_months."""
    links = data.links.get(resort_id) or []
    window = data.hiring_windows.get(resort_id)
    if not links and window is None:
        return None
    order = {g: i for i, g in enumerate(ROLE_GROUPS)}
    ordered = sorted(enumerate(links), key=lambda x: (order[x[1].role_group], x[0]))
    feed = any(data.employers[x.employer_id].ats.feed for x in links if x.employer_id in data.employers)
    return {"links": [x.model_dump(mode="json") for _, x in ordered], "feed": feed,
            "hiring_window": hiring_window_output(window, max_months)}


def employers_output(data: JobsData, header: dict) -> dict:
    """data/build/jobs/employers.json: every declared employer once, with its board page when it has a feed."""
    rows = []
    for eid in sorted(data.employers):
        e = data.employers[eid]
        rows.append(EmployerOut(**e.model_dump(), feed=e.ats.feed, board_url=board_url(e)).model_dump(mode="json"))
    return {**header, "employers": rows}


def empty_postings(header: dict, jobs_cfg) -> dict:
    """postings.json before the first feed run: no boards, no postings. The panel then shows links only."""
    return {**header, "fetched_at": None, "keywords": list(jobs_cfg.feed.keywords), "boards": [], "sources": [],
            "postings": []}


def check_postings(path: Path, data: JobsData, errors: list[str], warnings: list[str], rel=str) -> Optional[dict]:
    """Validate an existing data/build/jobs/postings.json (published data written by the feed). None when absent."""
    import json

    from pydantic import ValidationError

    from .schemas.jobs import PostingsFile

    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        PostingsFile.model_validate(raw)
    except (OSError, ValueError) as e:
        msgs = [f"{'.'.join(map(str, m['loc']))}: {m['msg']}" for m in e.errors()[:5]] if isinstance(e, ValidationError) else [str(e)]
        errors.extend(f"{rel(path)}: {m}" for m in msgs)
        return None
    stale = sorted({p["employer_id"] for p in raw.get("postings", [])} - set(data.employers))
    if stale:
        warnings.append(f"{rel(path)}: postings of employers no longer in data/raw/jobs ({', '.join(stale)}) stay "
                        "hidden until the next feed run")
    return raw
