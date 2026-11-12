"""A timed-out suite is stopped with SIGTERM before SIGKILL.

`fix.suite._terminate_tree` walks TERM then, after a grace, KILL. A timeout
that skipped TERM and went straight to KILL would still report TIMED_OUT, and
a runner that traps TERM to flush would never get the chance.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.suite  # noqa: E402

# The command is given one second. TERM then exit is ~that; TERM, a 5s grace,
# then KILL is ~6s. The bound sits between those so a KILL-first or grace-wait
# path fails rather than looking like a clean timeout.
_TIMEOUT_S = 1
_WALL_BOUND_S = 5


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "checks"
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return str(path)


def test_a_timeout_sends_term_before_kill(tmp_path):
    marker = tmp_path / "term.seen"
    cmd = _script(
        tmp_path,
        f"trap 'printf sent > \"{marker}\"; exit 0' TERM\n"
        "while true; do sleep 1; done",
    )

    started = time.monotonic()
    result = fix.suite.run(tmp_path, cmd, _TIMEOUT_S)
    elapsed = time.monotonic() - started

    assert result.status is fix.suite.SuiteStatus.TIMED_OUT
    assert marker.exists(), (
        "SIGTERM never arrived — the tree was killed without a chance to exit"
    )
    assert elapsed < _WALL_BOUND_S, (
        f"the run took {elapsed:.1f}s to come back from a {_TIMEOUT_S}s "
        "timeout — it waited out the TERM grace rather than exiting on TERM"
    )
