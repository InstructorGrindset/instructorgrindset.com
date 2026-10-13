"""Phase 7 crowdsourced reports: import, moderation queue, aggregation.

    python -m pipeline.reports list
    python -m pipeline.reports approve <id> [--note TEXT]
    python -m pipeline.reports reject <id> --note TEXT
    python -m pipeline.reports import <export.json|export.csv>
    python -m pipeline.reports aggregate [--replace]

One report per file: data/raw/reports/queue/<id>.yaml while pending, moved to
data/raw/reports/moderated/<id>.yaml when approved or rejected. Both folders are
gitignored. Only aggregates of ``reports.aggregation_min`` or more approved
reports leave them, through data/raw/reports/aggregates.json, which only the
``aggregate`` command writes. It merges with the committed file (a checkout may hold
only some moderated reports); ``--replace`` rebuilds it from this checkout alone.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from pydantic import ValidationError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.schemas.career import PayRecord, season_start_year  # noqa: E402
from pipeline.schemas.common import Source  # noqa: E402
from pipeline.schemas.reports import AggregatesFile, Report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "raw" / "reports"
SOURCE_ID = "instructorgrindset-reports"
SOURCE_URL = "https://instructorgrindset.com/map/"
SOURCE_TITLE = "Instructor Grindset member reports (aggregates of 3 or more approved reports)"
IMPORT_COLUMNS = ("id", "resort_id", "kind", "season", "cert_level", "housing_site_id", "hourly_usd", "monthly_usd",
                  "proof_url", "submitted_at")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


@dataclass
class ReportStore:
    root: Path = REPORTS_DIR

    @property
    def queue(self) -> Path:
        return self.root / "queue"

    @property
    def moderated(self) -> Path:
        return self.root / "moderated"

    def _load_dir(self, d: Path) -> tuple[list[Report], list[str]]:
        out, errors = [], []
        for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
            try:
                out.append(Report.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))))
            except (yaml.YAMLError, ValidationError) as e:
                errors.append(f"{p.name}: {e}".splitlines()[0] if isinstance(e, yaml.YAMLError) else
                              f"{p.name}: " + "; ".join(f"{'.'.join(map(str, m['loc'])) or '(report)'}: {m['msg']}"
                                                       for m in e.errors()))
        return out, errors

    def pending(self) -> tuple[list[Report], list[str]]:
        reports, errors = self._load_dir(self.queue)
        errors += [f"queue/{r.id}.yaml: status is {r.status}; queue holds pending reports" for r in reports if r.status != "pending"]
        return [r for r in reports if r.status == "pending"], errors

    def decided(self) -> tuple[list[Report], list[str]]:
        reports, errors = self._load_dir(self.moderated)
        errors += [f"moderated/{r.id}.yaml: status is pending" for r in reports if r.status == "pending"]
        return [r for r in reports if r.status != "pending"], errors

    def ids(self) -> set[str]:
        return {p.stem for d in (self.queue, self.moderated) if d.is_dir() for p in d.glob("*.yaml")}

    def write(self, report: Report) -> Path:
        d = self.queue if report.status == "pending" else self.moderated
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{report.id}.yaml"
        path.write_text(yaml.safe_dump(report.model_dump(mode="json", exclude_none=True), sort_keys=False), encoding="utf-8")
        return path

    def moderate(self, report_id: str, status: str, note: Optional[str] = None, now: Optional[dt.datetime] = None) -> Report:
        """Approve or reject a pending report; moves it from the queue to moderated."""
        src = self.queue / f"{report_id}.yaml"
        if not src.exists():
            raise LookupError(f"no pending report '{report_id}' in {self.queue}")
        report = Report.model_validate(yaml.safe_load(src.read_text(encoding="utf-8")))
        updated = Report.model_validate({**report.model_dump(mode="json"), "status": status,
                                         "moderation_note": note or report.moderation_note,
                                         "moderated_at": (now or _now()).isoformat()})
        self.write(updated)
        src.unlink()
        return updated


# --------------------------------------------------------------------- import
def _report_id(row: dict) -> str:
    key = json.dumps({k: (str(row.get(k)) if row.get(k) not in (None, "") else None)
                      for k in IMPORT_COLUMNS if k != "id"}, sort_keys=True)
    return "r-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def _num(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    return float(v)


# Formspree CSV exports name the submission time differently over time and per locale; the first match wins.
TIMESTAMP_COLUMNS = ("submitted_at", "created_at", "date", "_date", "submitted", "timestamp", "time", "received_at")
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                 "%b %d, %Y %I:%M %p", "%b %d, %Y %H:%M", "%d %b %Y %H:%M:%S", "%Y-%m-%d")


def _norm_key(k: str) -> str:
    return k.strip().lower().replace(" ", "_").replace("-", "_")


def parse_timestamp(v: Any) -> Optional[str]:
    """ISO datetime string from an export timestamp (ISO, "2026-10-13 14:05:22 UTC", "10/13/2026 14:05", ...)."""
    if v in (None, ""):
        return None
    text = str(v).strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(text)
    except ValueError:
        d = None
        for fmt in _DATE_FORMATS:
            try:
                d = dt.datetime.strptime(str(v).strip(), fmt)
                break
            except ValueError:
                continue
        if d is None:
            raise ValueError(f"unreadable timestamp {v!r}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()


def row_to_report(row: dict) -> Report:
    """One exported form submission (JSON object, CSV row or Formspree CSV row) to a pending Report.

    Column names are matched case and space insensitively; Formspree metadata columns (_subject,
    email, _replyto, ...) and any other unknown column are ignored."""
    row = {_norm_key(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
    stamp = next((row[k] for k in TIMESTAMP_COLUMNS if row.get(k) not in (None, "")), None)
    row["submitted_at"] = parse_timestamp(stamp)
    values = row.get("values") if isinstance(row.get("values"), dict) else {
        "hourly_usd": _num(row.get("hourly_usd")), "monthly_usd": _num(row.get("monthly_usd"))}
    data = {
        "id": row.get("id") or _report_id(row), "resort_id": row.get("resort_id"), "kind": row.get("kind"),
        "season": row.get("season"), "cert_level": row.get("cert_level") or None,
        "housing_site_id": row.get("housing_site_id") or None,
        "values": {k: v for k, v in values.items() if v is not None},
        "proof_url": row.get("proof_url") or None, "submitted_at": row["submitted_at"] or _now().isoformat(),
        "status": "pending",  # whatever the export says, every import starts in the queue
    }
    return Report.model_validate(data)


def read_export(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("reports", data.get("submissions")) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("expected a JSON list of submissions, or {reports: [...]}")
    return rows


def import_export(path: Path, store: ReportStore, known_resorts: Optional[set[str]] = None) -> tuple[int, list[str]]:
    """Add every valid, new submission to the queue. Returns (added, problems)."""
    added, problems = 0, []
    existing = store.ids()
    for i, row in enumerate(read_export(path), start=1):
        try:
            report = row_to_report(row)
        except (ValidationError, ValueError, TypeError) as e:
            msg = "; ".join(f"{'.'.join(map(str, m['loc']))}: {m['msg']}" for m in e.errors()) if isinstance(e, ValidationError) else str(e)
            problems.append(f"row {i}: {msg}")
            continue
        if known_resorts is not None and report.resort_id not in known_resorts:
            problems.append(f"row {i}: unknown resort_id '{report.resort_id}'")
            continue
        if report.id in existing:
            problems.append(f"row {i}: report '{report.id}' already exists; skipped")
            continue
        store.write(report)
        existing.add(report.id)
        added += 1
    return added, problems


# ------------------------------------------------------------------ aggregate
def aggregate(reports: Iterable[Report], aggregation_min: int, generated_at: str) -> dict:
    """Aggregates of approved reports: groups of ``aggregation_min`` or more give a min to max range."""
    groups: dict[tuple, list[Report]] = {}
    for r in reports:
        if r.status != "approved":
            continue
        key = (r.kind, r.resort_id, r.season, r.cert_level if r.kind == "pay" else r.housing_site_id)
        groups.setdefault(key, []).append(r)
    out: dict[str, Any] = {"version": 1, "generated_at": generated_at, "source": None, "pay": [], "rent": [], "housing": []}
    latest: Optional[dt.date] = None
    for (kind, rid, season, extra), rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        if len(rs) < aggregation_min:
            continue
        vals = [r.values.hourly_usd if kind == "pay" else r.values.monthly_usd for r in rs]
        verified = max(r.moderated_at for r in rs).date()
        latest = max(latest, verified) if latest else verified
        if kind == "pay":
            out["pay"].append({"resort_id": rid, "season": season, "cert_level": extra, "hourly_low": min(vals),
                               "hourly_high": max(vals), "confidence": "reported", "source_id": SOURCE_ID,
                               "verified_date": verified.isoformat(), "aggregate_count": len(rs),
                               "note": f"aggregate of {len(rs)} approved reports"})
        else:
            row = {"resort_id": rid, "season": season, "low": min(vals), "high": max(vals), "count": len(rs),
                   "verified_date": verified.isoformat()}
            if kind == "housing":
                row["housing_site_id"] = extra
            out[kind].append(row)
    if latest:
        out["source"] = {"id": SOURCE_ID, "url": SOURCE_URL, "title": SOURCE_TITLE, "accessed_date": latest.isoformat()}
    AggregatesFile.model_validate(out, context={"source_ids": {SOURCE_ID}})
    return out


def write_aggregates(path: Path, data: dict) -> bool:
    """Write aggregates.json unless only generated_at would change. Returns True when written."""
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if {**old, "generated_at": None} == {**data, "generated_at": None}:
                return False
        except ValueError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return True


def _agg_key(kind: str, row: dict) -> tuple:
    if kind == "pay":
        return (row["resort_id"], row["season"], row["cert_level"])
    return (row["resort_id"], row["season"], row.get("housing_site_id"))


def _agg_count(kind: str, row: dict) -> int:
    return int(row["aggregate_count"] if kind == "pay" else row["count"])


def merge_aggregates(committed: Optional[dict], local: Optional[dict]) -> Optional[dict]:
    """Union of committed and locally recomputed aggregates, never dropping a committed group.

    A local checkout may hold only part of the moderated reports (they are gitignored), so for a
    group present in both the row with more reports wins (local on a tie, as it is newer).
    """
    if not committed:
        return local
    if not local:
        return committed
    out: dict[str, Any] = {"version": 1, "generated_at": local["generated_at"], "source": None}
    for kind in ("pay", "rent", "housing"):
        rows = {_agg_key(kind, r): r for r in committed.get(kind) or []}
        for r in local.get(kind) or []:
            k = _agg_key(kind, r)
            if k not in rows or _agg_count(kind, r) >= _agg_count(kind, rows[k]):
                rows[k] = r
        out[kind] = [rows[k] for k in sorted(rows, key=lambda t: tuple(str(x) for x in t))]
    sources = [x for x in (committed.get("source"), local.get("source")) if x]
    if sources:
        out["source"] = max(sources, key=lambda x: x["accessed_date"])
    AggregatesFile.model_validate(out, context={"source_ids": {SOURCE_ID}})
    return out


@dataclass
class Aggregates:
    source: Optional[Source] = None
    pay: dict[str, list[PayRecord]] = field(default_factory=dict)
    rent: dict[str, dict] = field(default_factory=dict)                  # resort_id -> latest season aggregate
    housing: dict[tuple[str, str], dict] = field(default_factory=dict)   # (resort_id, site_id) -> latest season aggregate
    dropped: int = 0                                                      # rows below aggregation_min


def load_aggregates(data: Optional[dict], aggregation_min: int = 1) -> Aggregates:
    """Aggregates for the build. Rows with fewer than ``aggregation_min`` reports are dropped, so raising
    the minimum in config hides older small aggregates."""
    if not data:
        return Aggregates()
    f = AggregatesFile.model_validate(data, context={"source_ids": {SOURCE_ID}})
    agg = Aggregates(source=f.source)
    for p in f.pay:
        if (p.aggregate_count or 0) < aggregation_min:
            agg.dropped += 1
            continue
        agg.pay.setdefault(p.resort_id, []).append(p)

    def latest(rows: list, key) -> dict:
        best: dict[Any, Any] = {}
        for r in rows:
            if r.count < aggregation_min:
                agg.dropped += 1
                continue
            k = key(r)
            if k not in best or season_start_year(r.season) > season_start_year(best[k].season):
                best[k] = r
        return {k: v.model_dump(mode="json") for k, v in best.items()}
    agg.rent = latest(f.rent, lambda r: r.resort_id)
    agg.housing = latest(f.housing, lambda r: (r.resort_id, r.housing_site_id))
    return agg


def reported_rent(row: Optional[dict]) -> Optional[dict]:
    """A rent aggregate as a Sourced range with confidence reported."""
    if not row:
        return None
    return {"value": {"low": row["low"], "high": row["high"]}, "source_id": SOURCE_ID, "as_of": row["verified_date"],
            "confidence": "reported", "note": f"range of {row['count']} approved reports, season {row['season']}"}


# ------------------------------------------------------------------------ CLI
def main(argv: Optional[list[str]] = None) -> int:
    from pipeline.config import load_config

    ap = argparse.ArgumentParser(description="Moderate crowdsourced reports.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list pending reports")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--note", required=name == "reject")
    p = sub.add_parser("import", help="queue submissions from a JSON or CSV export")
    p.add_argument("file", type=Path)
    p = sub.add_parser("aggregate", help="merge aggregates of moderated reports into data/raw/reports/aggregates.json")
    p.add_argument("--replace", action="store_true",
                   help="rebuild the file from this checkout's moderated reports only (drops committed groups)")
    args = ap.parse_args(argv)
    store = ReportStore()

    if args.command == "list":
        pending, errors = store.pending()
        for r in pending:
            detail = f"{r.cert_level} {r.values.hourly_usd}/h" if r.kind == "pay" else \
                f"{r.housing_site_id or 'area'} {r.values.monthly_usd}/month"
            print(f"{r.id}  {r.kind:7} {r.resort_id:28} {r.season}  {detail}  proof={r.proof_url or '-'}")
        for e in errors:
            print(f"invalid: {e}", file=sys.stderr)
        print(f"{len(pending)} pending")
        return 0
    if args.command in ("approve", "reject"):
        try:
            r = store.moderate(args.id, "approved" if args.command == "approve" else "rejected", args.note)
        except (LookupError, ValidationError) as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"{r.id}: {r.status}")
        return 0
    if args.command == "import":
        known = None
        resorts = REPO_ROOT / "data" / "build" / "resorts.json"
        if resorts.exists():
            known = {r["id"] for r in json.loads(resorts.read_text())["resorts"]}
        added, problems = import_export(args.file, store, known)
        for p in problems:
            print(p, file=sys.stderr)
        print(f"{added} reports added to {store.queue}")
        return 0 if not problems else 1
    decided, errors = store.decided()
    if errors:
        for e in errors:
            print(f"invalid: {e}", file=sys.stderr)
        print("aggregates.json left unchanged: fix the moderated reports above first", file=sys.stderr)
        return 1
    path = store.root / "aggregates.json"
    data = aggregate(decided, load_config().definitions.reports.aggregation_min, _now().isoformat().replace("+00:00", "Z"))
    if not args.replace and path.exists():
        data = merge_aggregates(json.loads(path.read_text(encoding="utf-8")), data)
    write_aggregates(path, data)
    print(f"{len(data['pay'])} pay, {len(data['rent'])} rent, {len(data['housing'])} housing aggregates in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
