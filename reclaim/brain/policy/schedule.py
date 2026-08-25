"""Schedule token resolver.

Tokens are strings in policies.yaml so a merchant can edit them in V2 without a
deploy. Every token resolves against an explicit `from` time rather than the
wall clock, which is what makes the Day 3 time-travel demo possible: pretend it
is the 1st and the salary-window retries become observable in five minutes.
"""

import re
from datetime import datetime, timedelta

from ...timeutil import IST, next_salary_window, to_ist

_RELATIVE = re.compile(r"^\+?(\d+)([mhd])$")
_AT_TIME = re.compile(r"^\+?(\d+)d@(\d{1,2}):(\d{2})$")

_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class ScheduleError(ValueError):
    pass


def resolve(token: str, frm: datetime) -> datetime:
    """'20m' | '+7d' | '+1d@10:00' | 'immediate' | 'next_salary_window'."""
    frm = to_ist(frm)
    token = str(token).strip()

    if token == "immediate":
        return frm
    if token == "next_salary_window":
        return next_salary_window(frm)

    at = _AT_TIME.match(token)
    if at:
        days, hour, minute = int(at.group(1)), int(at.group(2)), int(at.group(3))
        target = (frm + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, tzinfo=IST
        )
        return target

    rel = _RELATIVE.match(token)
    if rel:
        return frm + timedelta(**{_UNITS[rel.group(2)]: int(rel.group(1))})

    raise ScheduleError(f"unrecognised schedule token: {token!r}")


def nth(schedule: list[str], attempt_number: int, frm: datetime) -> datetime | None:
    """The time for attempt N, or None when the schedule is exhausted — which is
    itself a stopping rule."""
    if attempt_number < 1 or attempt_number > len(schedule):
        return None
    return resolve(schedule[attempt_number - 1], frm)
