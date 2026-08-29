"""Tests get their own database, in a temp directory, always.

They used to share `data/reclaim.db` with everything else. That is fine right
up until `reclaim serve` is running in another terminal — which is exactly the
state the machine is in while rehearsing a demo. The server holds the file open,
`reset_database()` cannot unlink it on Windows, it falls back to dropping tables
out from under the server's live connections, and eighteen unrelated tests fail
with "no such table". The suite looked broken; nothing was.

Setting DATABASE_URL here, before `reclaim.config` is ever imported, means the
suite cannot be disturbed by a running server and cannot disturb the demo data
either. Environment variables outrank `.env` in pydantic-settings, so this wins
over whatever the developer has configured.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "reclaim_tests" / "reclaim_test.db"
_TEST_DB.parent.mkdir(parents=True, exist_ok=True)

# Must happen at import time: pytest imports conftest before any test module,
# and `reclaim.db` binds its engine at import.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"

# Deterministic regardless of what is in .env. A suite whose assertions depend
# on the developer's local credentials is not a suite.
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("AUTOPILOT_ENABLED", "true")

# Layer 2 is tested against fake clients, never a live one. Without this, adding
# a real key to .env silently turns the suite into something that calls a paid
# API a few hundred times, takes minutes, and fails on someone else's rate
# limit. Not setdefault: an inherited key must be overridden, not respected.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""


# One database for the whole suite means the suite is NOT safe to run twice at
# once. Two pytest processes against this file fight over the SQLite lock, and
# they do not fail fast — they retry, block, and eventually error somewhere
# unrelated with a lock timeout, having taken hours. If you want parallel runs,
# give each worker its own path here; do not just launch pytest twice.


def pytest_report_header(config):
    return f"reclaim test database: {_TEST_DB}"
