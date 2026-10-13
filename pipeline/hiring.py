"""Hiring windows in months, for the "Hiring now" filter (ROADMAP item 13).

A hiring window's text is its source's own sentence (data/raw/jobs, never reworded). The build reads a start month
and an end month from it only when the text is explicit about both:

* Start: the date a posting or the applications opened ("Instructor posting opened July 1, 2026", "Posted July 1,
  2026", "applications open August 15"), or else a statement that hiring is open ("Hiring is rolling", "Posting open
  and still accepting applications", "Now hiring"), which counts from the month of the source's ``as_of`` date.
* End: an application deadline ("applications are accepted until December 15", "accepting applications until",
  "apply by November 1") or the job's own start date ("target start of December 1, 2026", "estimated start
  December 5, 2026", "positions begin in December").

Everything else stays unparsed (``None``): season or employment dates ("Season runs about November 7"), arrival
dates, words in place of a month ("early fall", "late summer", "once the snow flies"), hedged dates ("roughly
November 23"), two different starts or two different ends, dates with no year to fix them, an end before the start,
and windows longer than ``jobs.hiring_windows.max_months``. Month names (in full, as three letters, or "Sept") must
start with a capital letter, so the verb "may" is never a month.

Years: a date without a year takes its year from the other end of the window (an end falls on or after the start;
a start falls on or before the end). The open now start uses the year of ``as_of``. A window whose dates carry no
year at all, and has no open now statement, stays unparsed.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
MONTH_NUMBER = {m: i for i, m in enumerate(MONTHS, 1)}
MONTH_NUMBER.update({m[:3]: i for i, m in enumerate(MONTHS, 1)})
MONTH_NUMBER["Sept"] = 9

# Month names are case sensitive (always capitalized in English), so the verb "may" is never a month. Cue words
# ignore case through scoped (?i:...) groups.
_MONTH = r"(?P<month>" + "|".join(sorted(MONTH_NUMBER, key=len, reverse=True)) + r")\.?"
_DATE = (r"(?:(?i:early|mid|late) )?" + _MONTH
         + r"(?: (?P<day>[1-3]?\d)(?i:st|nd|rd|th)?)?(?:,? (?P<year>20\d\d))?(?![\w-])")

# The date a posting or the applications opened.
OPENED = [re.compile(p + _DATE) for p in (
    r"\b(?i:postings?|applications?|hiring|recruiting) (?i:opened|opens|open|began|begins|started|starts)(?: (?i:on|in))? ",
    r"\b(?i:posted|posting dated)(?: (?i:on|in))? ",
)]
# Hiring is open when the source was checked (as_of).
OPEN_NOW = re.compile(
    r"\b(?i:hiring is (?:rolling|open|ongoing)|now hiring|now recruiting|(?:still )?accepting applications"
    r"|postings? (?:is |are )?(?:open|live)(?![\w-]))")
# An application deadline, or the job's own start date.
ENDS = [re.compile(p + _DATE) for p in (
    r"\b(?i:applications?|postings?) (?i:are |is |will be |remain )?(?i:accepted|taken|considered|open) (?i:until|through|by) ",
    r"\b(?i:accepting|taking) (?i:applications) (?i:until|through) ",
    r"\b(?i:applications?|postings?) (?i:are |is )?(?i:due|close|closes|closed)(?: (?i:on|by))? ",
    r"\b(?i:apply by|deadline(?: is| of)?) ",
    r"\b(?i:(?:target|estimated|expected|anticipated|planned) start(?: date)?|start date)(?: (?i:of|is|on))? ",
    r"\b(?i:positions?|jobs?|employment) (?i:begins?|starts?)(?: (?i:in|on))? ",
)]


@dataclass(frozen=True)
class HiringMonths:
    """A parsed window: first and last month as YYYY-MM. ``open_now`` is true when the start is the as_of month."""

    start: str
    end: str
    open_now: bool = False


def _dates(patterns: list[re.Pattern], text: str) -> set[tuple[int, Optional[int]]]:
    """(month, year or None) of every date the patterns find."""
    out = set()
    for p in patterns:
        for m in p.finditer(text):
            out.add((MONTH_NUMBER[m.group("month")], int(m.group("year")) if m.group("year") else None))
    return out


def _key(year: int, month: int) -> int:
    return year * 12 + month - 1


def parse_window(text: str, as_of: dt.date, max_months: int) -> Optional[HiringMonths]:
    """The window's start and end month, or None when the text is not explicit about both (see the module doc)."""
    starts = _dates(OPENED, text)
    ends = _dates(ENDS, text)
    open_now = not starts and bool(OPEN_NOW.search(text))
    if open_now:
        starts = {(as_of.month, as_of.year)}
    if len(starts) != 1 or not ends:
        return None
    (sm, sy), = starts
    years = {y for _, y in ends if y is not None}
    if sy is None:
        if len(years) != 1:
            return None
        ey = next(iter(years))
        # the start falls on or before the end
        sy = ey if all(sm <= m for m, _ in ends) else ey - 1
    resolved = {_key(y if y is not None else (sy if m >= sm else sy + 1), m) for m, y in ends}
    if len(resolved) != 1:
        return None
    start, end = _key(sy, sm), resolved.pop()
    if end < start or end - start + 1 > max_months:
        return None
    return HiringMonths(f"{start // 12:04d}-{start % 12 + 1:02d}", f"{end // 12:04d}-{end % 12 + 1:02d}", open_now)


def covers(window: Optional[HiringMonths], today: dt.date) -> bool:
    """True when today's month falls inside the window (both ends included). Mirrors map/js/data.js hiringWindowCovers."""
    if window is None:
        return False
    key = f"{today.year:04d}-{today.month:02d}"
    return window.start <= key <= window.end
