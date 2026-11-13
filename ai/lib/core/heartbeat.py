"""How often a long wait reports itself, read from the environment.

One parser for every heartbeat interval — the test-suite watcher's
`TEST_HEARTBEAT_SECS` and the push's `WORKBENCH_PUSH_HEARTBEAT_SECS` — so the
two agree on what empty, zero and garbage mean.
"""

# doc-group: platform

from __future__ import annotations

import os


def interval_from_env(env: str, default: float, raw: str | None = None) -> float:
    """Seconds between heartbeats from *env*; ``0`` disables, empty or unset is *default*.

    An exported empty string is a real environment that meant to inherit, so it
    is the default rather than "disable".
    """
    if raw is None:
        raw = os.environ.get(env)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{env} must be a number, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{env} must be >= 0, got {raw!r}")
    return value
