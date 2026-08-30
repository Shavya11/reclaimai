"""Committed proof: the claims the README makes, measured and written down.

The problem this solves is the one PLAN.md recorded after Day 4 — *"proof on an
ephemeral disk has an expiry date; proof in git does not."* Five verified webhook
deliveries were lost with Render's `/tmp`, and the ablation has the same shape: a
number that took minutes of live model calls to produce, held nowhere a reader
can check it.

So each proof is run once, offline, and its result committed under `evidence/`.
This is the `snapshot.py` argument applied to measurements rather than to a
batch, and it carries the same honesty note: **these are real runs of the real
harness, committed — not fixtures.** The Evidence tab renders them instantly and
offers a live re-run for anyone who doubts the committed figure.

Every artifact records how it was produced — the seed, the date, the git commit
if there is one — because a number without its provenance is a number nobody can
argue with, and arguing with them is the point.

**An artifact is never written from a void or failed run.** `cli ablation`
refuses to print a comparison when too many model calls went unanswered; writing
that refusal to disk as though it were a measurement would launder exactly the
number the void condition exists to suppress.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DIR = Path(__file__).resolve().parent.parent / "evidence"

# What the Evidence tab knows how to render. The key is the filename stem and
# the URL segment; the claim is what a reader is being asked to believe.
CLAIMS: dict[str, dict[str, str]] = {
    "ablation": {
        "claim": "Layer 2 earns its calls.",
        "detail": "The same batch, run with and without the model. Two arms, "
                  "two scratch databases, the same seed, the real runner and "
                  "the real guardrails throughout. Diagnoses are NOT frozen — "
                  "here diagnosis IS the independent variable.",
        "test": "tests/test_ablation.py",
        "command": "reclaim ablation --json",
    },
    "baseline": {
        "claim": "The machinery beats retrying everything three times.",
        "detail": "The naive strategy on the identical batch, drawing its coin "
                  "flips from the same seeded stream keyed on (record, "
                  "attempt). Nothing separates the two runs except what each "
                  "chose to do, when, and to whom.",
        "test": "tests/test_scoreboard.py",
        "command": "reclaim baseline --json",
    },
    "verify": {
        "claim": "The structural guarantees hold.",
        "detail": "Every check is a property this project claims somewhere in "
                  "prose. A guarantee nobody can run is a guarantee nobody "
                  "should believe.",
        "test": "reclaim verify",
        "command": "reclaim verify --json",
    },
}


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=DIR.parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 — provenance is nice to have, never required
        return ""


def write(name: str, payload: dict[str, Any], *, seed: int | None = None) -> Path:
    """Commit one measurement, stamped with how it was produced."""
    if name not in CLAIMS:
        raise ValueError(f"unknown evidence artifact: {name}")
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{name}.json"
    path.write_text(json.dumps({
        "name": name,
        **CLAIMS[name],
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "git_commit": _git_commit(),
        "result": payload,
    }, indent=2, default=str), encoding="utf-8")
    return path


def read(name: str) -> dict[str, Any] | None:
    path = DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("evidence %s is unreadable: %s", name, exc)
        return None


def available() -> list[dict[str, Any]]:
    """Every claim, with its measurement when there is one.

    A claim with no artifact is listed as missing rather than hidden. A judge
    who cannot see that the ablation has not been run is worse off than one who
    can, and hiding it would be the same failure the void conditions exist to
    prevent — quietly showing less than the whole picture.
    """
    out = []
    for name, meta in CLAIMS.items():
        artifact = read(name)
        out.append({
            "name": name,
            **meta,
            "present": artifact is not None,
            "produced_at": (artifact or {}).get("produced_at"),
            "seed": (artifact or {}).get("seed"),
            "git_commit": (artifact or {}).get("git_commit"),
            "result": (artifact or {}).get("result"),
        })
    return out
