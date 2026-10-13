"""Live job postings from public job board APIs -> data/build/jobs/postings.json.

    python -m pipeline.jobs_feed fetch [--root DIR] [--force]
    python -m pipeline.jobs_feed fetch --fixtures DIR      # recorded responses (offline runs and tests)

Reads data/build/jobs/employers.json and data/build/resorts.json (run pipeline/build.py first), fetches the open
postings of every employer whose ATS vendor publishes a public postings API and whose board is set, keeps the postings
that match config `jobs.feed.keywords`, guesses a role group from the title, assigns resorts and writes
data/build/jobs/postings.json. GitHub Actions runs it daily (.github/workflows/jobs-feed.yml).

Only these public, documented, keyless endpoints are read:

* Greenhouse Job Board API   GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs
* Lever Postings API         GET https://api.lever.co/v0/postings/{board}?mode=json
* SmartRecruiters Posting API GET https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=100&offset={n}
* Workable widget API        GET https://apply.workable.com/api/v1/widget/accounts/{board}
* Ashby Job Posting API      GET https://api.ashbyhq.com/posting-api/job-board/{board}

Workday, iCIMS, UKG, Paylocity, ADP, Indeed, Glassdoor and every other site are never fetched or scraped: those
employers get links only. Postings are published data: each cites its public board page as the source, with
`as_of` the date of the fetch.

Resorts: an employer linked from one resort gives all its kept postings to that resort. For an employer linked from
several resorts (a parent company), a posting goes to the resorts whose name (or an alias in
`jobs.feed.location_aliases`) appears in its location, else in its title or department. A posting that names none
keeps `resort_ids: []` and shows on those resorts as a role at the employer with no single resort named.

The file is rewritten only when its postings or board states change, or when the last fetch is older than
`jobs.feed.refresh_days`, so the daily job commits only real changes. A board that fails keeps its last postings for
`jobs.feed.max_age_days`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote, urlparse

if __package__ in (None, ""):  # executed as a script: python pipeline/jobs_feed.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.config import JobsDefs, load_config  # noqa: E402
from pipeline.jobs import BOARD_PAGE, EMPLOYERS_NAME, OUT_SUBDIR, POSTINGS_NAME  # noqa: E402
from pipeline.links import domain_of  # noqa: E402
from pipeline.schemas.jobs import FEED_VENDORS, PostingsFile  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "InstructorGrindsetCareerMap/1.0 (+https://instructorgrindset.com/map/)"
MAX_BYTES = 8_000_000
SMARTRECRUITERS_PAGES = 10
VENDOR_NAME = {"greenhouse": "Greenhouse", "lever": "Lever", "smartrecruiters": "SmartRecruiters",
               "workable": "Workable", "ashby": "Ashby"}
GENERIC_SUFFIXES = ("ski and snowboard resort", "ski resort", "ski area", "resort")


class FeedError(Exception):
    """A board could not be read (network, HTTP status, or an unexpected response)."""


Getter = Callable[[str], Any]


# ---------------------------------------------------------------- HTTP

def http_get_json(url: str, timeout: float = 20) -> Any:
    """GET a public JSON endpoint over https. Raises FeedError on any failure."""
    if urlparse(url).scheme != "https":
        raise FeedError("only https endpoints are read")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only, checked above)
            if urlparse(resp.geturl()).scheme != "https":
                raise FeedError("redirected off https")
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FeedError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FeedError(f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
    if len(body) > MAX_BYTES:
        raise FeedError("response too large")
    try:
        return json.loads(body)
    except ValueError:
        raise FeedError("response is not JSON") from None


class FixtureGetter:
    """Recorded responses: ``<dir>/responses.json`` maps each request URL to a JSON file in the same folder.
    A URL without a recording fails like an HTTP 404."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.map = json.loads((self.dir / "responses.json").read_text(encoding="utf-8"))
        self.requested: list[str] = []

    def __call__(self, url: str) -> Any:
        self.requested.append(url)
        name = self.map.get(url)
        if name is None:
            raise FeedError("HTTP 404")
        if name.startswith("error:"):
            raise FeedError(name[len("error:"):].strip())
        return json.loads((self.dir / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- vendor adapters

@dataclass
class RawPosting:
    id: str
    title: str
    url: str
    location: Optional[str] = None
    department: Optional[str] = None


def _text(v: Any, limit: int = 300) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())
    return s[:limit] if s else None


def _join(*parts: Any) -> Optional[str]:
    return _text(", ".join(p for p in (_text(x) for x in parts) if p))


def api_url(vendor: str, board: str, offset: int = 0) -> str:
    b = quote(board, safe="")
    return {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{b}?mode=json",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{b}/postings?limit=100&offset={offset}",
        "workable": f"https://apply.workable.com/api/v1/widget/accounts/{b}",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{b}",
    }[vendor]


def _greenhouse(board: str, get: Getter) -> list[RawPosting]:
    data = get(api_url("greenhouse", board))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise FeedError("unexpected Greenhouse response")
    return [RawPosting(f"greenhouse-{j.get('id')}", _text(j.get("title")) or "", str(j.get("absolute_url") or ""),
                       _text((j.get("location") or {}).get("name")),
                       _join(*[d.get("name") for d in j.get("departments") or [] if isinstance(d, dict)]))
            for j in jobs if isinstance(j, dict)]


def _lever(board: str, get: Getter) -> list[RawPosting]:
    data = get(api_url("lever", board))
    if not isinstance(data, list):
        raise FeedError("unexpected Lever response")
    out = []
    for j in data:
        if not isinstance(j, dict):
            continue
        cat = j.get("categories") or {}
        out.append(RawPosting(f"lever-{j.get('id')}", _text(j.get("text")) or "", str(j.get("hostedUrl") or ""),
                              _text(cat.get("location")), _join(cat.get("department"), cat.get("team"))))
    return out


def _smartrecruiters(board: str, get: Getter) -> list[RawPosting]:
    out: list[RawPosting] = []
    offset = 0
    for _ in range(SMARTRECRUITERS_PAGES):
        data = get(api_url("smartrecruiters", board, offset))
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, list):
            raise FeedError("unexpected SmartRecruiters response")
        for j in content:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            loc = j.get("location") or {}
            where = _text(loc.get("fullLocation")) or _join(loc.get("city"), loc.get("region"))
            out.append(RawPosting(f"smartrecruiters-{j['id']}", _text(j.get("name")) or "",
                                  f"https://jobs.smartrecruiters.com/{quote(board, safe='')}/{quote(str(j['id']), safe='')}",
                                  where, _text((j.get("department") or {}).get("label"))))
        offset += len(content)
        total = data.get("totalFound")
        if not content or not isinstance(total, int) or offset >= total:
            break
    return out


def _workable(board: str, get: Getter) -> list[RawPosting]:
    data = get(api_url("workable", board))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise FeedError("unexpected Workable response")
    return [RawPosting(f"workable-{j.get('shortcode')}", _text(j.get("title")) or "",
                       str(j.get("url") or j.get("shortlink") or ""), _join(j.get("city"), j.get("state")),
                       _text(j.get("department"))) for j in jobs if isinstance(j, dict)]


def _ashby(board: str, get: Getter) -> list[RawPosting]:
    data = get(api_url("ashby", board))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise FeedError("unexpected Ashby response")
    return [RawPosting(f"ashby-{j.get('id')}", _text(j.get("title")) or "", str(j.get("jobUrl") or ""),
                       _text(j.get("location")), _join(j.get("department"), j.get("team")))
            for j in jobs if isinstance(j, dict) and j.get("isListed", True) is not False]


ADAPTERS = {"greenhouse": _greenhouse, "lever": _lever, "smartrecruiters": _smartrecruiters,
            "workable": _workable, "ashby": _ashby}
assert set(ADAPTERS) == set(FEED_VENDORS) == set(BOARD_PAGE)


# ---------------------------------------------------------------- matching

def normalize(text: Optional[str]) -> str:
    """Lower case words separated by single spaces ("Ski & Ride School" -> "ski and ride school")."""
    t = (text or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def word_pattern(words: Iterable[str]) -> Optional[re.Pattern]:
    """Whole word (or phrase) match over normalized text; None for an empty list."""
    terms = sorted({normalize(w) for w in words if normalize(w)}, key=len, reverse=True)
    if not terms:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9])")


def resort_terms(name: str, aliases: Iterable[str] = ()) -> list[str]:
    """The resort's name, the name without a generic suffix ("Big Sky Resort" -> "big sky") and its aliases.
    Terms shorter than four letters are dropped (too likely to match by chance)."""
    n = normalize(name)
    terms = {n}
    for suffix in GENERIC_SUFFIXES:
        if n.endswith(" " + suffix):
            terms.add(n[: -len(suffix) - 1])
    terms |= {normalize(a) for a in aliases}
    return sorted(t for t in terms if len(t.replace(" ", "")) >= 4)


class Matcher:
    """Keyword filter, role guess and resort assignment, built from config and the build's resorts."""

    def __init__(self, cfg: JobsDefs, resorts: list[dict]):
        f = cfg.feed
        self.keep = word_pattern(f.keywords)
        self.exclude = word_pattern(f.exclude_keywords)
        self.roles = [(group, word_pattern(words)) for group, words in f.role_keywords.items()]
        self.by_employer: dict[str, list[str]] = {}
        names: dict[str, list[str]] = {}
        for r in resorts:
            for link in (r.get("jobs") or {}).get("links") or []:
                ids = self.by_employer.setdefault(link["employer_id"], [])
                if r["id"] not in ids:
                    ids.append(r["id"])
            names[r["id"]] = resort_terms(r.get("name", ""), f.location_aliases.get(r["id"], []))
        self.patterns = {rid: word_pattern(t) for rid, t in names.items()}

    def kept(self, p: RawPosting) -> bool:
        text = normalize(f"{p.title} {p.department or ''}")
        return bool(self.keep and self.keep.search(text)) and not (self.exclude and self.exclude.search(text))

    def role_group(self, p: RawPosting, employer_kind: str) -> str:
        text = normalize(f"{p.title} {p.department or ''}")
        for group, pattern in self.roles:
            if pattern and pattern.search(text):
                return group
        return "corporate" if employer_kind == "parent_company" else "all"

    def resorts_for(self, employer_id: str, employer_name: str, p: RawPosting) -> list[str]:
        linked = self.by_employer.get(employer_id, [])
        if len(linked) <= 1:
            return list(linked)
        own = word_pattern([employer_name])
        for field in (p.location, f"{p.title} {p.department or ''}"):
            text = normalize(field)
            if own:
                text = own.sub(" ", text)
            hits = [rid for rid in linked if self.patterns.get(rid) and self.patterns[rid].search(text)]
            if hits:
                return sorted(hits)
        return []


# ---------------------------------------------------------------- run

ROLE_ORDER = {"ski_school": 0, "coaching": 1, "management": 2, "corporate": 3, "all": 4}


def _iso(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(ts: Optional[str]) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None


def board_source_id(vendor: str, board: str) -> str:
    return f"jobs-board-{vendor}-{normalize(board).replace(' ', '-') or 'board'}"


def _allowed_url(url: str, vendor: str, careers_url: str, cfg: JobsDefs) -> bool:
    return (urlparse(url).scheme == "https" and bool(urlparse(url).hostname)
            and domain_of(url) in (cfg.domains(vendor) | {domain_of(careers_url)}))


def collect(employers: list[dict], resorts: list[dict], cfg: JobsDefs, get: Getter, now: dt.datetime,
            previous: Optional[dict] = None) -> dict:
    """The new postings.json document (not yet written)."""
    matcher = Matcher(cfg, resorts)
    prev_boards = {b["employer_id"]: b for b in (previous or {}).get("boards", [])}
    prev_posts: dict[str, list[dict]] = {}
    for p in (previous or {}).get("postings", []):
        prev_posts.setdefault(p["employer_id"], []).append(p)
    stamp, today = _iso(now), now.date().isoformat()
    boards, sources, postings = [], {}, []
    for e in sorted(employers, key=lambda x: x["id"]):
        ats = e.get("ats") or {}
        vendor, board = ats.get("vendor"), ats.get("board")
        if vendor not in ADAPTERS or not board or e["id"] not in matcher.by_employer:
            continue
        sid = board_source_id(vendor, board)
        row = {"employer_id": e["id"], "employer_name": e["name"], "vendor": vendor, "board": board,
               "url": BOARD_PAGE[vendor].format(board=board), "source_id": sid, "status": "ok",
               "fetched_at": stamp, "as_of": today, "total": 0, "matched": 0, "error": None}
        try:
            raw = ADAPTERS[vendor](board, get)
        except FeedError as err:
            old = prev_boards.get(e["id"]) or {}
            last = _parse(old.get("fetched_at"))
            fresh = last is not None and (now - last).days < cfg.feed.max_age_days and old.get("board") == board
            row.update(status="error", error=str(err)[:200], fetched_at=old.get("fetched_at") if fresh else None,
                       as_of=old.get("as_of") if fresh else None, total=old.get("total", 0) if fresh else 0,
                       matched=old.get("matched", 0) if fresh else 0)
            kept = [p for p in prev_posts.get(e["id"], [])] if fresh else []
            postings += kept
        else:
            kept = []
            seen = set()
            for p in raw:
                if not p.title or p.id in seen or not _allowed_url(p.url, vendor, e["careers_url"], cfg) or not matcher.kept(p):
                    continue
                seen.add(p.id)
                kept.append({"id": p.id[:200], "employer_id": e["id"], "title": p.title, "location": p.location,
                             "department": p.department, "url": p.url, "role_group": matcher.role_group(p, e.get("kind", "")),
                             "resort_ids": matcher.resorts_for(e["id"], e["name"], p), "source_id": sid,
                             "as_of": today, "confidence": "published", "fetched_at": stamp})
            kept.sort(key=lambda x: (ROLE_ORDER.get(x["role_group"], 9), x["title"].lower(), x["id"]))
            kept = kept[: cfg.feed.max_postings_per_board]
            row.update(total=len(raw), matched=len(kept))
            postings += kept
        boards.append(row)
        if row["as_of"] or kept:
            sources[sid] = {"id": sid, "url": row["url"], "title": f"{e['name']} jobs on {VENDOR_NAME[vendor]}",
                            "accessed_date": row["as_of"] or today,
                            "note": "Public job board API; open postings that match the Career Map keywords"}
        else:
            sources[sid] = {"id": sid, "url": row["url"], "title": f"{e['name']} jobs on {VENDOR_NAME[vendor]}",
                            "accessed_date": today, "note": "Board could not be read at the last fetch"}
    doc = {"version": 1, "generated_at": stamp, "fetched_at": stamp, "keywords": list(cfg.feed.keywords),
           "boards": boards, "sources": [sources[k] for k in sorted(sources)], "postings": postings}
    PostingsFile.model_validate(doc)
    return doc


TIMESTAMP_KEYS = {"generated_at", "fetched_at", "as_of", "accessed_date"}


def _content(doc: Optional[dict]) -> Any:
    """The document without fetch timestamps: what counts as a change."""
    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in TIMESTAMP_KEYS}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    return strip(doc) if doc else None


def should_write(new: dict, old: Optional[dict], refresh_days: int, now: dt.datetime) -> bool:
    if old is None or _content(new) != _content(old):
        return True
    last = _parse(old.get("fetched_at"))
    return last is None or (now - last) >= dt.timedelta(days=refresh_days)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run(root: Path, get: Optional[Getter] = None, now: Optional[dt.datetime] = None, force: bool = False,
        config_dir: Optional[Path] = None) -> tuple[bool, dict]:
    """Fetch every board and write postings.json when it changed. Returns (written, document)."""
    cfg = load_config(config_dir or root / "config").definitions.jobs
    build = root / "data" / "build"
    employers_file = _read_json(build / OUT_SUBDIR / EMPLOYERS_NAME)
    resorts_file = _read_json(build / "resorts.json")
    if employers_file is None or resorts_file is None:
        raise FileNotFoundError("data/build/jobs/employers.json or data/build/resorts.json is missing; "
                                "run python pipeline/build.py first")
    path = build / OUT_SUBDIR / POSTINGS_NAME
    old = _read_json(path)
    if old is not None:
        try:
            PostingsFile.model_validate(old)
        except ValueError:
            old = None
    now = now or dt.datetime.now(dt.timezone.utc)
    get = get or (lambda url: http_get_json(url, cfg.feed.timeout_s))
    doc = collect(employers_file.get("employers", []), resorts_file.get("resorts", []), cfg, get, now, old)
    if not force and not should_write(doc, old, cfg.feed.refresh_days, now):
        return False, old or doc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True, doc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="fetch every public board and update data/build/jobs/postings.json")
    f.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root (default: this repository)")
    f.add_argument("--fixtures", type=Path, help="read recorded responses from DIR/responses.json instead of the network")
    f.add_argument("--force", action="store_true", help="write the file even when nothing changed")
    args = ap.parse_args(argv)
    get = FixtureGetter(args.fixtures) if args.fixtures else None
    try:
        written, doc = run(args.root, get, force=args.force)
    except FileNotFoundError as e:
        print(f"jobs feed: {e}", file=sys.stderr)
        return 1
    for b in doc["boards"]:
        state = f"{b['matched']} of {b['total']} kept" if b["status"] == "ok" else f"failed ({b['error']})"
        print(f"  {b['employer_id']} ({b['vendor']} {b['board']}): {state}")
    print(f"jobs feed: {len(doc['postings'])} postings from {len(doc['boards'])} boards; "
          f"{'wrote' if written else 'unchanged,'} {POSTINGS_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
