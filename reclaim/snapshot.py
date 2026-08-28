"""A pre-walked demo, frozen to a file.

The deployment problem this solves: a free instance has an ephemeral disk, so
every cold boot starts with an empty database, and rebuilding the batch live
means 120 records diagnosed through a rate-limited free LLM tier — about a
hundred seconds during which the dashboard is a truthful, useless row of zeroes.
A visitor who arrives inside that window sees ₹0 recovered and leaves.

So the arc is walked ONCE, offline, by `reclaim snapshot`, and the settled result
is committed. Boot restores it in about a second, with no network call and no
quota spent, landing on exactly the numbers the README publishes — because they
were produced by the same runner, not by a hand-written fixture that can drift
away from the code.

Timestamps are rebased on restore. A snapshot built in August and restored in
November would otherwise show a batch detected three months ago and a demo clock
sitting a fortnight in the past. Every stored datetime is shifted by the age of
the snapshot, so the restored state is always as old as the arc took to walk and
no older. The clock offset needs no shifting: it is already relative to the wall
clock, so it rides along for free.
"""

import gzip
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import inspect as sa_inspect

from .config import ROOT, settings
from .money import format_inr
from .db import (
    AppStateRow,
    AtRiskRecordRow,
    AuditLogRow,
    CustomerRow,
    ExecutedActionRow,
    HumanQueueRow,
    InterventionRow,
    SessionLocal,
    WebhookEventRow,
    init_db,
    reset_database,
)
from .timeutil import now as wall_now, to_ist

log = logging.getLogger(__name__)

PATH = ROOT / "fixtures" / "demo_snapshot.json.gz"

FORMAT = 1

# Insert order, not declaration order: a child row whose parent is missing is a
# foreign key error on a database that enforces them, and this one does.
TABLES = (
    CustomerRow,
    AtRiskRecordRow,
    InterventionRow,
    ExecutedActionRow,
    WebhookEventRow,
    AuditLogRow,
    HumanQueueRow,
    AppStateRow,
)


def _columns(model) -> list[str]:
    return [c.key for c in sa_inspect(model).mapper.column_attrs]


def _datetime_columns(model) -> set[str]:
    return {
        c.key
        for c in sa_inspect(model).mapper.columns
        if c.type.__class__.__name__ == "DateTime"
    }


def _dump_row(row, columns: list[str], stamps: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in columns:
        value = getattr(row, name)
        if name in stamps and value is not None:
            value = value.isoformat()
        out[name] = value
    return out


def _load_row(model, data: dict[str, Any], stamps: set[str],
              shift: timedelta) -> Any:
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        if name in stamps and value is not None:
            kwargs[name] = to_ist(datetime.fromisoformat(value) + shift)
        else:
            kwargs[name] = value
    return model(**kwargs)


def build(*, llm=None, seed: int | None = None, extra_ticks: int = 3,
          path: Path | None = None) -> dict[str, Any]:
    """Reset, walk the entire demo arc, and freeze the result.

    Run with layer 2 on. The whole point of the snapshot is that a deployment
    which cannot reach a model still shows the diagnoses a model produced, and
    a snapshot built with `--no-llm` publishes the fallback numbers under the
    headline ones — the exact mismatch this file exists to prevent.
    """
    from . import clock
    from .runner import DEMO_ARC, run_batch, tick
    from .scoreboard import compute

    path = path or PATH
    seed = seed if seed is not None else settings.seed

    reset_database()
    clock.reset()

    run_batch(seed=seed, llm=llm, dry_run=True)
    for step in DEMO_ARC + ["+7d"] * max(0, extra_ticks):
        tick(advance=step, seed=seed, llm=llm, dry_run=True)

    board = compute()
    payload: dict[str, Any] = {
        "format": FORMAT,
        "built_at": wall_now().isoformat(),
        "seed": seed,
        "layer_2": llm is not None,
        "scoreboard": {
            "records": board.records,
            "recovered_paise": board.recovered_paise,
            "recovered_display": format_inr(board.recovered_paise),
            "records_recovered": board.recovered_records,
            "recovery_rate": round(board.recovery_rate, 4),
        },
        "tables": {},
    }

    with SessionLocal() as session:
        for model in TABLES:
            columns, stamps = _columns(model), _datetime_columns(model)
            payload["tables"][model.__tablename__] = [
                _dump_row(row, columns, stamps)
                for row in session.query(model).all()
            ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    return payload


def read(path: Path | None = None) -> dict[str, Any] | None:
    path = path or PATH
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot at %s is unreadable: %s", path, exc)
        return None
    if payload.get("format") != FORMAT:
        log.warning("snapshot at %s is format %s, expected %s",
                    path, payload.get("format"), FORMAT)
        return None
    return payload


def available(path: Path | None = None) -> bool:
    return read(path) is not None


def restore(path: Path | None = None) -> dict[str, Any] | None:
    """Replace the database with the frozen arc. Returns its header, or None.

    Never raises. A snapshot that will not load is a deployment that seeds the
    slow way, not a deployment that fails to start.
    """
    payload = read(path)
    if payload is None:
        return None

    try:
        shift = wall_now() - datetime.fromisoformat(payload["built_at"])
        reset_database()
        init_db()
        with SessionLocal() as session:
            for model in TABLES:
                rows = payload["tables"].get(model.__tablename__, [])
                stamps = _datetime_columns(model)
                session.add_all(
                    [_load_row(model, row, stamps, shift) for row in rows])
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot restore failed: %s", exc)
        return None

    log.info("restored demo snapshot: %s records, %s recovered (built %s ago)",
             payload["scoreboard"]["records"],
             payload["scoreboard"]["recovered_display"],
             _age(shift))
    return payload


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"
