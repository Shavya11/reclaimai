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


def pytest_report_header(config):
    return f"reclaim test database: {_TEST_DB}"
