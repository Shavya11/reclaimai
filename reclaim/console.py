"""Windows consoles default to cp1252, which cannot encode the rupee sign. Every
CLI entry point calls init() before printing anything."""

import sys


def init() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
