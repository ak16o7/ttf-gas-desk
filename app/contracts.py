"""ICE strip names -> delivery periods.

ICE labels: 'Oct26', 'Q4 26', 'Winter26', 'Summer27', 'Cal 27', 'GY 26', 'Nov26-Mar27'.
Gas seasons: Winter YY = Oct YY .. Mar YY+1, Summer YY = Apr .. Sep YY. Gas year GY YY = Oct YY .. Sep YY+1.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

_RE_MONTH = re.compile(r"^([A-Z][a-z]{2})(\d{2})$")
_RE_QUARTER = re.compile(r"^Q([1-4])\s?(\d{2})$")
_RE_SEASON = re.compile(r"^(Winter|Summer|Win|Sum)\s?(\d{2})$")
_RE_CAL = re.compile(r"^Cal\s?(\d{2})$")
_RE_GY = re.compile(r"^GY\s?(\d{2})$")
_RE_RANGE = re.compile(r"^([A-Z][a-z]{2})(\d{2})-([A-Z][a-z]{2})(\d{2})$")


@dataclass(frozen=True)
class Strip:
    label: str          # original ICE label
    kind: str           # month | quarter | season | cal | gasyear | range
    start: dt.date
    end: dt.date        # inclusive last delivery day

    @property
    def months(self) -> int:
        return (self.end.year - self.start.year) * 12 + self.end.month - self.start.month + 1

    @property
    def key(self) -> str:
        """Stable, language-neutral id, e.g. M-2026-10, Q-2026-4, WIN-2026, SUM-2027, CAL-2027."""
        s = self.start
        if self.kind == "month":
            return f"M-{s.year}-{s.month:02d}"
        if self.kind == "quarter":
            return f"Q-{s.year}-{(s.month - 1) // 3 + 1}"
        if self.kind == "season":
            return f"{'WIN' if s.month == 10 else 'SUM'}-{s.year}"
        if self.kind == "cal":
            return f"CAL-{s.year}"
        if self.kind == "gasyear":
            return f"GY-{s.year}"
        return f"R-{s.isoformat()}-{self.end.isoformat()}"

    @property
    def label_de(self) -> str:
        s = self.start
        yy = f"{s.year % 100:02d}"
        if self.kind == "month":
            return f"{MONTHS_DE[s.month - 1]}-{yy}"
        if self.kind == "quarter":
            return f"Q{(s.month - 1) // 3 + 1}-{yy}"
        if self.kind == "season":
            return f"{'Win' if s.month == 10 else 'Sum'}-{yy}"
        if self.kind == "cal":
            return f"Cal-{yy}"
        if self.kind == "gasyear":
            return f"GY-{yy}"
        return self.label


def _last_day(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def parse_strip(label: str) -> Strip | None:
    lab = (label or "").strip()
    if m := _RE_MONTH.match(lab):
        mon = MONTHS.get(m.group(1))
        if mon:
            y = 2000 + int(m.group(2))
            return Strip(lab, "month", dt.date(y, mon, 1), _last_day(y, mon))
    if m := _RE_QUARTER.match(lab):
        q, y = int(m.group(1)), 2000 + int(m.group(2))
        m0 = 3 * (q - 1) + 1
        return Strip(lab, "quarter", dt.date(y, m0, 1), _last_day(y, m0 + 2))
    if m := _RE_SEASON.match(lab):
        y = 2000 + int(m.group(2))
        if m.group(1).startswith("W"):
            return Strip(lab, "season", dt.date(y, 10, 1), _last_day(y + 1, 3))
        return Strip(lab, "season", dt.date(y, 4, 1), _last_day(y, 9))
    if m := _RE_CAL.match(lab):
        y = 2000 + int(m.group(1))
        return Strip(lab, "cal", dt.date(y, 1, 1), dt.date(y, 12, 31))
    if m := _RE_GY.match(lab):
        y = 2000 + int(m.group(1))
        return Strip(lab, "gasyear", dt.date(y, 10, 1), _last_day(y + 1, 9))
    if m := _RE_RANGE.match(lab):
        a, b = MONTHS.get(m.group(1)), MONTHS.get(m.group(3))
        if a and b:
            ya, yb = 2000 + int(m.group(2)), 2000 + int(m.group(4))
            return Strip(lab, "range", dt.date(ya, a, 1), _last_day(yb, b))
    return None


def months_ahead(strip: Strip, today: dt.date) -> int:
    return (strip.start.year - today.year) * 12 + strip.start.month - today.month
