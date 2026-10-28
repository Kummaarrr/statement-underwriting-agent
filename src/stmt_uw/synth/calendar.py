"""US bank business days (Federal Reserve holiday schedule).

Fed rule: a holiday on Sunday is observed on Monday; a holiday on Saturday is not moved
(banks open the Friday before). ACH, card settlements and transfers post on business days only.
"""

from __future__ import annotations

import datetime as dt
from functools import cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


@cache
def fed_holidays(year: int) -> frozenset[dt.date]:
    fixed = [
        dt.date(year, 1, 1),
        dt.date(year, 6, 19),
        dt.date(year, 7, 4),
        dt.date(year, 11, 11),
        dt.date(year, 12, 25),
    ]
    observed = [d + dt.timedelta(days=1) if d.weekday() == 6 else d for d in fixed]
    observed = [d for d in observed if d.weekday() < 5]  # Saturday holidays: not moved
    floating = [
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    ]
    return frozenset(observed + floating)


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in fed_holidays(d.year)


def next_business_day(d: dt.date) -> dt.date:
    while not is_business_day(d):
        d += dt.timedelta(days=1)
    return d


def business_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if is_business_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out
