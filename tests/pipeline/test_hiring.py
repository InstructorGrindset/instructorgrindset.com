"""Tests for pipeline/hiring.py: hiring window text to start and end month (the Hiring now filter).

Texts are synthetic sentences in the shape of the real research (data/raw/jobs/hiring_windows.yaml); the last test
pins how the committed windows parse.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml

from pipeline.hiring import HiringMonths, covers, parse_window

REPO = Path(__file__).resolve().parents[2]
AS_OF = dt.date(2026, 10, 13)
MAX = 9


def months(text, as_of=AS_OF, max_months=MAX):
    m = parse_window(text, as_of, max_months)
    return (m.start, m.end) if m else None


@pytest.mark.parametrize("text, expected", [
    # posting opened, then the job's start date
    ("Instructor posting opened July 1, 2026 with a target start of December 1, 2026.", ("2026-07", "2026-12")),
    ("Posted July 1, 2026; seasonal ski school positions begin in December and end mid April.", ("2026-07", "2026-12")),
    ("Applications open August 15, 2026; the expected start date is November 28.", ("2026-08", "2026-11")),
    ("Applications open Sept. 1, 2026. Apply by Nov 15.", ("2026-09", "2026-11")),
    # a start without a year takes it from the end: the start falls on or before the end
    ("Applications open August 15 for a target start of December 1, 2026.", ("2026-08", "2026-12")),
    ("Applications open November 1 for a start date of January 5, 2027.", ("2026-11", "2027-01")),
    # an end without a year falls on or after the start, across the new year too
    ("Posting opened November 2, 2026; applications are accepted until January 10.", ("2026-11", "2027-01")),
    # open now (as_of month), then a deadline or a start date
    ("Hiring is rolling; applications are accepted until December 15 or until the positions are filled.", ("2026-10", "2026-12")),
    ("Posting open and still accepting applications; estimated start December 5, 2026; part time commitments start at "
     "20 days a season.", ("2026-10", "2026-12")),
    ("Now hiring. Applications are due by November 30.", ("2026-10", "2026-11")),
    # the explicit opening date wins over an open now statement
    ("Posting opened September 3, 2026 and is still accepting applications until December 1.", ("2026-09", "2026-12")),
    # two ends that name the same month agree
    ("Hiring is rolling; applications are accepted until December 15; target start of December 20, 2026.",
     ("2026-10", "2026-12")),
])
def test_explicit_windows_parse(text, expected):
    assert months(text) == expected


@pytest.mark.parametrize("text", [
    # an opening but no end
    "Ski and snowboard instructor applications open August 15 on the 2026-2027 seasonal jobs table; online pre season "
    "training follows in October.",
    "Now hiring part time and full time instructors for winter 2026/27.",
    "The winter selection process begins in September. Preseason training runs from two days to two weeks before the "
    "season opens in December.",
    # season, employment or arrival dates are not a hiring window
    "Recruiting for the season of November 5, 2026 to April 30, 2027; staff may be asked to arrive earlier for training.",
    "Season runs about November 7, 2026 to April 18, 2027. Instructors commit to 15 or more days.",
    "Experienced instructor posting (Alpine Level 2) covers December 2, 2026 to April 18, 2027.",
    "New hires should arrive by December 2, 2026 (J-1 college students by December 9).",
    "Start date options are November 17, December 8 or December 14; hires must be able to work until April 11.",
    # words in place of a month, and hedged dates
    "Hiring for winter work starts in early fall; H-2B and J-1 visas are not sponsored.",
    "Interviews are scheduled in early to mid October; the expected start date is once the snow flies.",
    "Candidates apply in the apprentice window, which opens in late summer.",
    "Instructors start around the end of November or the beginning of December.",
    "Posting dated April 24, 2026; winter employment runs roughly November 23 to April 21.",
    "Posting opened July 1, 2026; positions begin roughly in December.",
    # no year anywhere and no open now statement
    "Applications open August 15; target start of December 1.",
    # two different starts, two different ends
    "Posting opened July 1, 2026. Posted August 3, 2026. Target start of December 1, 2026.",
    "Posting opened July 1, 2026; applications are accepted until November 1; target start of December 1, 2026.",
    # an end before the start, and a window longer than max_months
    "Posting opened December 1, 2026; applications are accepted until October 1, 2026.",
    "Posting opened January 5, 2026 with a target start of December 1, 2026.",
    # an open now statement with a deadline that has passed (the source is older than the posting)
    "Hiring is rolling; applications are accepted until September 1, 2026.",
    # "may" is a verb, never a month
    "Hiring is rolling; staff may start in early winter.",
    "",
])
def test_ambiguous_windows_stay_unparsed(text):
    assert months(text) is None


def test_open_now_counts_from_the_as_of_month_and_max_months_comes_from_config():
    m = parse_window("Hiring is rolling; applications are accepted until December 15.", dt.date(2026, 8, 30), MAX)
    assert m == HiringMonths("2026-08", "2026-12", open_now=True)
    assert parse_window("Posted July 1, 2026; positions begin in December.", AS_OF, 5) is None
    assert months("Posted July 1, 2026; positions begin in December.", max_months=6) == ("2026-07", "2026-12")


def test_covers_includes_both_end_months():
    w = HiringMonths("2026-07", "2026-12")
    assert not covers(w, dt.date(2026, 6, 30))
    assert covers(w, dt.date(2026, 7, 1)) and covers(w, dt.date(2026, 10, 13)) and covers(w, dt.date(2026, 12, 31))
    assert not covers(w, dt.date(2027, 1, 1)) and not covers(w, dt.date(2027, 10, 13))   # a later season is not this one
    assert covers(HiringMonths("2026-11", "2027-02"), dt.date(2027, 1, 20))
    assert not covers(None, AS_OF)


def test_committed_hiring_windows_parse_conservatively():
    """The 27 researched windows: only the ones explicit about both ends get months (docs/map/ARCHITECTURE.md)."""
    path = REPO / "data" / "raw" / "jobs" / "hiring_windows.yaml"
    if not path.exists():
        pytest.skip("no committed hiring windows")
    rows = yaml.safe_load(path.read_text())["hiring_windows"]
    parsed = {}
    for w in rows:
        as_of = w["as_of"] if isinstance(w["as_of"], dt.date) else dt.date.fromisoformat(w["as_of"])
        m = parse_window(" ".join(w["text"].split()), as_of, MAX)
        if m:
            parsed[w["resort_id"]] = (m.start, m.end)
    rolling = ("2026-10", "2026-12")
    expected = {"vail": rolling, "hunter-mountain": rolling, "mount-snow": rolling, "stowe": rolling,
                "liberty-mountain": rolling, "arapahoe-basin": rolling, "sunday-river": ("2026-07", "2026-12"),
                "sugarloaf": ("2026-07", "2026-11"), "copper-mountain": ("2026-07", "2026-12")}
    if {w["resort_id"] for w in rows} >= set(expected):
        assert parsed == expected
