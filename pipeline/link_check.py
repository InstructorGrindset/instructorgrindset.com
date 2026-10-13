"""Monthly link check for the official trail map, webcam and jobs links.

    python -m pipeline.link_check [--root DIR] [--report FILE] [--summary FILE] [--workers N]

Collects every outbound link the site shows from data/build (resorts.json `official_trailmap`, `webcams` and
`jobs` links, and the careers pages in jobs/employers.json), requests each URL once (HEAD, then GET when HEAD is
refused) and writes a Markdown report. GitHub Actions (.github/workflows/link-check.yml) runs it monthly and opens or
updates one issue with the report while links are broken, and closes it when none are. Nothing is committed.

Results:
* broken: HTTP 404 or 410, a host that does not resolve, or an invalid TLS certificate. Fix these in data/raw.
* could not verify: any other error status (401, 403, 429, 5xx and the like, often bot protection) or a timeout.
  Listed for information; a person checks them in a browser.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0 (compatible; InstructorGrindsetLinkCheck/1.0; +https://instructorgrindset.com/map/)"
TIMEOUT_S = 20
BROKEN_STATUS = {404, 410}
RETRY_HEAD_WITH_GET = {400, 403, 405, 406, 429, 500, 501, 503}
ISSUE_BODY_LIMIT = 60000  # GitHub issue bodies hold 65,536 characters


@dataclass
class Link:
    url: str
    used_by: list[str] = field(default_factory=list)   # "vail: official trail map"


@dataclass
class Result:
    url: str
    state: str          # ok | broken | unverified
    detail: str         # "HTTP 200", "HTTP 404", "DNS lookup failed", "timeout"


def collect_links(build: Path) -> list[Link]:
    """Every https link in the build output that points off the site, deduplicated by URL."""
    links: dict[str, Link] = {}

    def add(url: Optional[str], where: str) -> None:
        if isinstance(url, str) and url.startswith("https://"):
            links.setdefault(url, Link(url)).used_by.append(where)

    resorts = json.loads((build / "resorts.json").read_text(encoding="utf-8")).get("resorts", [])
    for r in resorts:
        rid = r.get("id", "?")
        add((r.get("official_trailmap") or {}).get("url"), f"{rid}: official trail map")
        w = r.get("webcams") or {}
        add((w.get("cams_page") or {}).get("url"), f"{rid}: live cams page")
        for c in w.get("cams") or []:
            add(c.get("url"), f"{rid}: cam {c.get('label', '')}".rstrip())
        for x in (r.get("jobs") or {}).get("links") or []:
            add(x.get("url"), f"{rid}: jobs link {x.get('label', '')}".rstrip())
    emp = build / "jobs" / "employers.json"
    if emp.is_file():
        for e in json.loads(emp.read_text(encoding="utf-8")).get("employers", []):
            add(e.get("careers_url"), f"employer {e.get('id', '?')}: careers page")
    return [links[u] for u in sorted(links)]


Opener = Callable[[str, str], int]   # (method, url) -> HTTP status; raises urllib errors


def _open(method: str, url: str) -> int:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (https links from the build)
            if method == "GET":
                resp.read(65536)
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def classify(url: str, opener: Opener = _open, pause: float = 3.0) -> Result:
    """HEAD, then GET when HEAD is refused or errors; one retry for timeouts and server errors."""
    detail = "no response"
    for attempt in range(2):
        try:
            status = opener("HEAD", url)
            if status in RETRY_HEAD_WITH_GET:
                status = opener("GET", url)
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.gaierror):
                return Result(url, "broken", "DNS lookup failed")
            if isinstance(reason, ssl.SSLCertVerificationError):
                return Result(url, "broken", "invalid TLS certificate")
            detail = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else f"{type(reason).__name__}"
        else:
            if status < 400:
                return Result(url, "ok", f"HTTP {status}")
            if status in BROKEN_STATUS:
                return Result(url, "broken", f"HTTP {status}")
            detail = f"HTTP {status}"
            if status < 500:
                break
        if attempt == 0 and pause:
            time.sleep(pause)
    return Result(url, "unverified", detail)


def check_all(links: list[Link], opener: Opener = _open, workers: int = 8, pause: float = 3.0) -> list[Result]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda link: classify(link.url, opener, pause), links))


def _used(link: Link, n: int = 4) -> str:
    shown = "; ".join(link.used_by[:n])
    more = len(link.used_by) - n
    return f"{shown}; and {more} more" if more > 0 else shown


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(links: list[Link], results: list[Result], checked_on: dt.date) -> str:
    by_url = {x.url: x for x in links}
    broken = [r for r in results if r.state == "broken"]
    unverified = [r for r in results if r.state == "unverified"]

    def table(rows: list[Result]) -> list[str]:
        out = ["| Link | Result | Used by |", "|---|---|---|"]
        out += [f"| {_cell(r.url)} | {r.detail} | {_cell(_used(by_url[r.url]))} |" for r in rows]
        return out

    lines = [f"# Broken links: {len(broken)} of {len(results)}", "",
             f"Checked {checked_on.isoformat()} by `python -m pipeline.link_check` (`.github/workflows/link-check.yml`): "
             "official trail maps, live cams and jobs links from `data/build`.", ""]
    if broken:
        lines += ["## Broken", ""] + table(broken) + [""]
    else:
        lines += ["No broken links.", ""]
    if unverified:
        lines += ["<details><summary>Could not verify "
                  f"({len(unverified)}): bot protection, rate limits or server errors. Check these in a browser.</summary>",
                  ""] + table(unverified) + ["", "</details>", ""]
    lines += ["To fix a broken link, correct its URL in `data/raw` (trailmaps_official, webcams or jobs) with a source, "
              "run `python pipeline/build.py` and commit. The next run closes this issue when no link is broken."]
    text = "\n".join(lines) + "\n"
    if len(text) > ISSUE_BODY_LIMIT:
        text = text[:ISSUE_BODY_LIMIT].rsplit("\n", 1)[0] + "\n\n(Report shortened to fit an issue.)\n"
    return text


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--report", type=Path, default=Path("link-report.md"))
    ap.add_argument("--summary", type=Path, default=Path("link-summary.json"))
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    build = args.root / "data" / "build"
    if not (build / "resorts.json").is_file():
        print("link check: data/build/resorts.json is missing; run python pipeline/build.py first", file=sys.stderr)
        return 1
    links = collect_links(build)
    results = check_all(links, workers=args.workers)
    counts = {s: sum(1 for r in results if r.state == s) for s in ("ok", "broken", "unverified")}
    args.report.write_text(render_report(links, results, dt.datetime.now(dt.timezone.utc).date()), encoding="utf-8")
    args.summary.write_text(json.dumps({"checked": len(results), **counts}) + "\n", encoding="utf-8")
    print(f"link check: {len(results)} links, {counts['broken']} broken, {counts['unverified']} could not be verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
