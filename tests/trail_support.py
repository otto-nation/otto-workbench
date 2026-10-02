"""Trail fixtures shared by the `core.trail_query` and `core.trail_view` suites.

Both read the records these write, so the writers are spelled once here. A
helper only one suite uses stays with that suite.
"""

import json
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.workbench_paths  # noqa: E402
from conftest import reset_trail_root  # noqa: E402
from core.trail import Trail  # noqa: E402


def make_trail(script: str, events: list[tuple[str, str]]) -> str:
    """Write a trail with the given action/detail pairs, return invocation ID."""
    trail = Trail.start(script=script, context={"repo": "org/repo", "pr": 42})
    for action, detail in events:
        trail.info(action, detail)
    trail.finish()
    return trail.invocation


def make_command(*scripts: str) -> list[str]:
    """One user command as the process tree it really is; return its invocations.

    Each trail is opened while the one before it is still the published root, so
    the records land exactly as `pr` → `review` → `review-orchestrate`
    writes them — without paying for three subprocesses per test. The spawn
    itself is covered in `trail_test.py`, which is where that mechanism lives.

    `reset_trail_root` is what makes each call a separate command, the way the
    outermost process exiting does in a real tree: without it the next call
    would adopt this one's root and the two would read as one command.
    """
    with reset_trail_root():
        trails = []
        for script in scripts:
            trail = Trail.start(script=script,
                                context={"repo": "org/repo", "pr": 42})
            trail.info("work", f"{script} ran")
            trails.append(trail)
        for trail in reversed(trails):
            trail.finish()
    return [t.invocation for t in trails]


# The command every correlation test is about: what `pr review` really runs.
# Named once so the three-process shape is a single source of truth rather than
# a literal repeated down the class.
PR_REVIEW = ("pr", "review", "review-orchestrate")


def raw_event(**fields) -> dict:
    """One trail record, with every required field defaulted.

    For the tests that cannot go through `Trail` — history from before a field
    existed, or a stamp hours in the past. Each names only what it is about and
    inherits the rest, so a new required field is added here rather than in
    every literal that predates it.

    The record rather than its line, for the readers that take events as they
    come off the loader. `raw_record` is the same thing serialized.
    """
    return {
        "ts": "2026-01-01T00:00:00Z", "script": "old-run",
        "invocation": "a1b2c3d4", "level": "info", "event_type": "action",
        "action": "x", "detail": "", "context": {},
        **fields,
    }


def raw_record(**fields) -> str:
    """One trail record as a line, for the tests that write a trail file."""
    return json.dumps(raw_event(**fields)) + "\n"


def write_raw(name: str, *records: str) -> Path:
    """Put pre-built *records* in the trail root under *name*."""
    root = core.workbench_paths.trail_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text("".join(records))
    return path
