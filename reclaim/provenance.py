"""Where a record came from: the seeded batch, or a visitor typing into the demo.

The dashboard lets anyone submit an error string and commit it as a real record —
same runner, same gate, same executor. That is the point, and it is also the one
way a stranger can move a number the README publishes.

**The reproducibility digest was never the exposure.** `verify._batch_is_reproducible`
compares `generate(seed=42)` against itself, and `generate` is a pure function of its
seed — nothing committed to a database can reach it. The ablation seeds scratch
databases the same way. The baseline reads live rows but only ever looks them up by
an id drawn from the seeded batch, so an unknown id contributes zero.

The scoreboard is the exposure, because it is the one figure computed by asking the
live database what is in it. So `USR_` records are counted separately there and
nowhere else, and the published headline stays the published headline.

The prefix is the discriminator rather than a column because it needs no migration
and because `LIKE 'USR_%'` reads the same in SQL, in a log line and to a person
scanning the audit trail. `raw_signals["origin"]` carries the same fact for anyone
reading a record rather than a query.
"""

from __future__ import annotations

from typing import Any

USER_PREFIX = "USR_"

# Far above REC_5000 and INV_7000 so the three spaces can never collide, and
# visibly a different order of magnitude when read off a screen.
_FIRST_USER_ID = 9000


def is_user_record(record_id: str | None) -> bool:
    return bool(record_id) and record_id.startswith(USER_PREFIX)


def mark(raw_signals: dict[str, Any] | None) -> dict[str, Any]:
    """Stamp provenance onto a record's signals. `AtRiskRecord` stays generic —
    this is a key in the bag it already carries, not a new field on it."""
    signals = dict(raw_signals or {})
    signals["origin"] = "user"
    return signals


def next_user_id(session) -> str:
    """The next free `USR_` id.

    The high-water mark is read from the AUDIT LOG as well as from the records
    table, and the audit log is the half that matters. Records can be deleted —
    a reset, a cleanup, a merchant tidying up — and reading only that table hands
    the next submission an id the trail has already used, silently grafting a
    stranger's words onto an older record's history. `audit_log` is append-only,
    so an id that has ever been issued stays issued.

    (A test deleted a committed record and got the same id back, which is how
    this stopped being a docstring that described the wrong function.)
    """
    from .db import AtRiskRecordRow, AuditLogRow

    highest = _FIRST_USER_ID - 1
    columns = ((AtRiskRecordRow.id, AtRiskRecordRow.id),
               (AuditLogRow.record_id, AuditLogRow.record_id))
    for column, filter_on in columns:
        for (rid,) in (session.query(column)
                       .filter(filter_on.like(f"{USER_PREFIX}%"))
                       .distinct().all()):
            try:
                highest = max(highest, int(rid[len(USER_PREFIX):]))
            except (TypeError, ValueError):
                continue  # PREVIEW_ID or a hand-edited id; skip, never fail
    return f"{USER_PREFIX}{highest + 1}"


def seeded_only(query, column):
    """Restrict a query to the seeded batch — the population every published
    figure was measured over."""
    return query.filter(~column.like(f"{USER_PREFIX}%"))


def user_only(query, column):
    return query.filter(column.like(f"{USER_PREFIX}%"))
