"""THE single rule loader.

Every threshold and every policy row in the system enters through this module.
Diagnosis, policy and guardrail code never read a YAML file, an environment
variable or a magic number directly — they ask here.

That indirection was V1's bet, and this file is where it paid. The source moved
from a YAML file to a database table and NOT ONE CALLER CHANGED: `policies()`,
`guardrail_config()`, `policy_for()` and `threshold()` have the same signatures
and the same return shapes they had when they read a file. The decision layers
below never learned that a merchant can now edit their own rules.

How it resolves, in order:

  1. the database, when it has been seeded and a row exists
  2. the YAML file, which remains the DEFAULT — it is what seeds the database,
     what `reset_to_defaults()` restores, and what a fresh clone runs on with no
     database at all

Caching is a generation counter rather than lru_cache, because lru_cache cannot
be invalidated per-source and hot reload has to be exact: a write bumps the
generation, the next read misses, and there is no window in which half a batch
reads the old ceiling and half reads the new one.
"""

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
POLICIES_PATH = _HERE / "policy" / "policies.yaml"
GUARDRAILS_PATH = _HERE / "guardrails" / "guardrails.yaml"

_lock = threading.Lock()
_generation = 0
_cache: dict[str, tuple[int, Any]] = {}

# Set False to ignore the database entirely. The what-if replay leaves it alone
# — it runs against a scratch database that has its own rules — but a test that
# wants the shipped defaults regardless of what a previous test wrote has to be
# able to say so.
USE_DB = True


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"rule source missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def default_policies() -> dict[str, Any]:
    """The shipped table. Never read from the database — this is what the
    database is compared against and reset to."""
    return _load_yaml(POLICIES_PATH)


def default_guardrails() -> dict[str, Any]:
    return _load_yaml(GUARDRAILS_PATH)


def _cached(key: str, build):
    global _generation
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == _generation:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (_generation, value)
    return value


def _db_policies() -> dict[str, Any] | None:
    """Overlay of stored rows onto the defaults, or None if unavailable.

    An OVERLAY, deliberately, not a replacement. A merchant editing the value
    of one row must not silently drop every row they have not edited, and a
    version of the code that adds a policy row must not need a migration before
    the new row is visible. Stored rows win where they exist; the file supplies
    everything else.
    """
    if not USE_DB:
        return None
    try:
        from ..db import PolicyRuleRow, SessionLocal

        with SessionLocal() as session:
            rows = session.query(PolicyRuleRow).all()
            if not rows:
                return None
            table = {k: dict(v) for k, v in default_policies().items()}
            for row in rows:
                table.setdefault(row.leak_type, {})[row.root_cause] = row.row
            return table
    except Exception as exc:  # noqa: BLE001
        # No database yet, or a broken one. Falling back to the file is the
        # correct failure: the system runs on its shipped defaults rather than
        # on nothing, and a batch never dies because an admin table is missing.
        log.debug("policy rules unavailable from DB, using defaults: %s", exc)
        return None


def _db_guardrails() -> dict[str, Any] | None:
    if not USE_DB:
        return None
    try:
        from ..db import GuardrailConfigRow, SessionLocal

        with SessionLocal() as session:
            rows = session.query(GuardrailConfigRow).all()
            if not rows:
                return None
            config = {k: v for k, v in default_guardrails().items()}
            for row in rows:
                config[row.name] = row.config
            return config
    except Exception as exc:  # noqa: BLE001
        log.debug("guardrail config unavailable from DB, using defaults: %s", exc)
        return None


def policies() -> dict[str, Any]:
    """leak_type -> root_cause -> policy row."""
    return _cached("policies", lambda: _db_policies() or default_policies())


def guardrail_config() -> dict[str, Any]:
    return _cached("guardrails", lambda: _db_guardrails() or default_guardrails())


def policy_for(leak_type: str, root_cause: str) -> dict[str, Any] | None:
    """Exact row, or the leak type's UNKNOWN row, or None. Falling back to
    UNKNOWN means an unmapped combination escalates to a human rather than
    silently doing nothing."""
    table = policies().get(leak_type, {})
    return table.get(root_cause) or table.get("UNKNOWN")


def threshold(*path: str, default: Any = None) -> Any:
    node: Any = guardrail_config()
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def reload() -> None:
    """Invalidate every cached rule.

    Called after any admin write, and by tests that swap rule sources. Bumping a
    counter rather than clearing a dict means a read already in flight finishes
    against the generation it started on, instead of seeing a half-populated
    cache.
    """
    global _generation
    with _lock:
        _generation += 1


# --- the admin side ---------------------------------------------------------


def seed_from_yaml(force: bool = False) -> int:
    """Copy the shipped defaults into the database so they can be edited.

    Idempotent, and a no-op once seeded unless forced — a restart must not
    overwrite what a merchant changed, which is the single most annoying thing
    a config system can do.
    """
    from ..db import GuardrailConfigRow, PolicyRuleRow, SessionLocal, init_db

    init_db()
    written = 0
    with SessionLocal() as session:
        if force:
            session.query(PolicyRuleRow).delete()
            session.query(GuardrailConfigRow).delete()
        elif session.query(PolicyRuleRow).count():
            return 0

        for leak_type, table in default_policies().items():
            for root_cause, row in table.items():
                session.add(PolicyRuleRow(leak_type=leak_type,
                                          root_cause=root_cause, row=row))
                written += 1
        for name, config in default_guardrails().items():
            session.add(GuardrailConfigRow(name=name, config=config))
            written += 1
        session.commit()

    reload()
    return written


def reset_to_defaults() -> int:
    """Back to the shipped table. The escape hatch that makes editing safe to
    try: a merchant who has tuned themselves into a corner has one button."""
    return seed_from_yaml(force=True)


def is_modified(scope: str, key: str) -> bool:
    """Whether a stored rule differs from the shipped default. Drives the
    'default → new' marker in the admin UI, so an edited value is visible as
    edited rather than looking like it always said that."""
    if scope == "guardrail":
        stored = guardrail_config().get(key)
        return stored != default_guardrails().get(key)
    leak_type, _, root_cause = key.partition(".")
    stored = policies().get(leak_type, {}).get(root_cause)
    return stored != default_policies().get(leak_type, {}).get(root_cause)
