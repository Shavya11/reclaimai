"""The demo clock.

A salary-window retry is up to a month away and a 48-hour follow-up is two days
away. Neither is watchable, so the system reads "now" from here and the demo
moves it. Every policy schedule resolves against an explicit `frm`, which is
what makes this possible without a single `sleep` or a mocked datetime module.

The offset is persisted, not held in memory: `tick` is a separate process from
`run-batch`, and the API restarts. Real time still passes underneath — the
offset is added to the wall clock rather than replacing it, so ordering never
goes backwards mid-run.

Nothing outside a demo needs this. `reset()` puts the clock back on the wall.
"""

from datetime import datetime, timedelta

from .brain.policy.schedule import resolve
from .db import AppStateRow, SessionLocal, init_db
from .timeutil import now as wall_now, to_ist

OFFSET_KEY = "clock_offset_seconds"


def _read_offset() -> float:
    try:
        with SessionLocal() as session:
            row = session.get(AppStateRow, OFFSET_KEY)
            return float(row.value) if row is not None else 0.0
    except Exception:  # no database yet is an offset of zero, not a crash
        return 0.0


def _write_offset(seconds: float) -> None:
    init_db()
    with SessionLocal() as session:
        row = session.get(AppStateRow, OFFSET_KEY)
        if row is None:
            session.add(AppStateRow(key=OFFSET_KEY, value=str(seconds),
                                    updated_at=wall_now()))
        else:
            row.value = str(seconds)
            row.updated_at = wall_now()
        session.commit()


def offset() -> timedelta:
    return timedelta(seconds=_read_offset())


def now() -> datetime:
    """Wall clock plus whatever the demo has advanced."""
    return wall_now() + offset()


def advance(token: str) -> datetime:
    """Move the clock by a policy schedule token — '24h', '+7d',
    'next_salary_window'. Reusing the policy resolver means the demo advances
    to exactly the moments the policy table talks about, rather than to
    round numbers that only approximately line up with them."""
    current = now()
    target = to_ist(resolve(token, current))
    if target < current:
        target = current
    _write_offset(_read_offset() + (target - current).total_seconds())
    return now()


def set_to(when: datetime) -> datetime:
    _write_offset((to_ist(when) - wall_now()).total_seconds())
    return now()


def reset() -> None:
    _write_offset(0.0)


def is_travelled() -> bool:
    return abs(_read_offset()) > 1.0
