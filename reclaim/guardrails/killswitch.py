"""Guardrail 1's state, persisted.

`POST /api/kill-switch` used to set `settings.autopilot_enabled` in memory.
Flip it off, restart the process - or let a free-tier instance restart itself
after fifteen idle minutes - and the agent is armed again with nobody having
armed it. A panic button that un-presses itself is not one.

Stored in `app_state` next to the demo clock, and for the same reason: a
runtime flip has to outlive the process that made it. The env var stays the
default for a database that has never been flipped.
"""

from reclaim.config import settings
from reclaim.db import AppStateRow, SessionLocal, init_db
from reclaim.timeutil import now as wall_now

KEY = "autopilot_enabled"


def enabled() -> bool:
    try:
        with SessionLocal() as session:
            row = session.get(AppStateRow, KEY)
            if row is None:
                return settings.autopilot_enabled
            return row.value == "1"
    except Exception:  # no database yet: the env var is the answer
        return settings.autopilot_enabled


def set_enabled(value: bool) -> bool:
    init_db()
    with SessionLocal() as session:
        row = session.get(AppStateRow, KEY)
        if row is None:
            session.add(AppStateRow(key=KEY, value="1" if value else "0",
                                    updated_at=wall_now()))
        else:
            row.value = "1" if value else "0"
            row.updated_at = wall_now()
        session.commit()
    # Kept in step so anything still reading settings sees the same answer.
    settings.autopilot_enabled = value
    return value
