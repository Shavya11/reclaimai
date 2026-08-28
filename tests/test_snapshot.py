"""The snapshot is what a cold deployment actually serves, so it is worth the
same scrutiny as the code that built it.

The failure this guards against is not a crash. It is a deployment that boots,
answers every request, and shows numbers that quietly disagree with the ones
published about it — which is worse than being down, because nobody notices.
"""

import gzip
import json
from datetime import datetime, timedelta

import pytest

from reclaim import snapshot
from reclaim.db import reset_database
from reclaim.repository import count_records
from reclaim.scoreboard import compute
from reclaim.timeutil import now as wall_now


@pytest.fixture
def frozen():
    payload = snapshot.read()
    if payload is None:
        pytest.skip("no committed snapshot — run `reclaim snapshot`")
    return payload


def test_the_committed_snapshot_is_readable(frozen):
    assert frozen["format"] == snapshot.FORMAT
    assert frozen["scoreboard"]["records"] > 0
    assert frozen["layer_2"], (
        "a snapshot built without layer 2 publishes the fallback numbers "
        "under the headline ones"
    )


def test_restore_reproduces_the_scoreboard_it_claims(frozen):
    reset_database()
    assert count_records() == 0

    assert snapshot.restore() is not None

    board = compute()
    claimed = frozen["scoreboard"]
    assert board.records == claimed["records"]
    assert board.recovered_paise == claimed["recovered_paise"]
    assert board.recovered_records == claimed["records_recovered"]


def test_restore_leaves_a_settled_batch_not_an_untouched_one():
    """A restored deployment must show the arc walked, not just the batch
    detected — an undiagnosed batch is the ₹0 dashboard this file exists to
    prevent."""
    snapshot.restore()
    board = compute()

    assert board.interventions > 0, "no action was ever taken"
    assert sum(board.guardrails_fired.values()) > 0, "no guardrail ever fired"
    assert board.recovered_records > 0, "nothing was ever recovered"
    causes = {line.cause for line in board.by_root_cause}
    assert causes - {"UNKNOWN"}, "every record is UNKNOWN — layer 2 never ran"


def test_an_old_snapshot_restores_as_fresh_as_a_new_one(tmp_path, frozen):
    """Timestamps are rebased on restore, so a snapshot committed in August and
    opened in November still shows a batch detected hours ago rather than a
    demo that visibly rotted on the shelf."""
    age = timedelta(days=90)
    stamps = {m.__tablename__: snapshot._datetime_columns(m)
              for m in snapshot.TABLES}

    aged = json.loads(json.dumps(frozen))
    aged["built_at"] = (
        datetime.fromisoformat(aged["built_at"]) - age).isoformat()
    for table, rows in aged["tables"].items():
        for row in rows:
            for column in stamps[table]:
                if row.get(column):
                    row[column] = (
                        datetime.fromisoformat(row[column]) - age).isoformat()

    path = tmp_path / "aged.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(aged, fh)

    assert snapshot.restore(path) is not None

    board = compute()
    assert board.records == frozen["scoreboard"]["records"]
    assert board.recovered_paise == frozen["scoreboard"]["recovered_paise"]

    from reclaim.db import AtRiskRecordRow, SessionLocal

    with SessionLocal() as session:
        newest = max(r.detected_at for r in session.query(AtRiskRecordRow))
    detected_ago = wall_now().replace(tzinfo=None) - newest
    assert detected_ago < timedelta(days=7), (
        f"restored batch is {detected_ago.days} days stale — the rebase "
        f"did not happen"
    )


def test_a_missing_snapshot_is_none_not_an_exception(tmp_path):
    """A snapshot that will not load is a deployment that seeds the slow way,
    never one that fails to start."""
    assert snapshot.read(tmp_path / "absent.json.gz") is None
    assert snapshot.restore(tmp_path / "absent.json.gz") is None

    corrupt = tmp_path / "corrupt.json.gz"
    corrupt.write_bytes(b"this is not gzip")
    assert snapshot.read(corrupt) is None
    assert snapshot.restore(corrupt) is None
