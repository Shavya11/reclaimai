"""THE single rule loader.

Every threshold and every policy row in the system enters through this module.
Diagnosis, policy and guardrail code never read a YAML file, an environment
variable or a magic number directly — they ask here.

That indirection is the whole V2 story: swapping these two functions for DB reads
turns a static config into a merchant-editable admin panel without touching a
line of decision logic.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
POLICIES_PATH = _HERE / "policy" / "policies.yaml"
GUARDRAILS_PATH = _HERE / "guardrails" / "guardrails.yaml"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"rule source missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def policies() -> dict[str, Any]:
    """leak_type -> root_cause -> policy row."""
    return _load(POLICIES_PATH)


@lru_cache(maxsize=1)
def guardrail_config() -> dict[str, Any]:
    return _load(GUARDRAILS_PATH)


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
    """V2 hot-reload seam. Tests use it to swap rule sources."""
    policies.cache_clear()
    guardrail_config.cache_clear()
