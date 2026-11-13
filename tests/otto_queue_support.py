"""Shared helpers for otto-queue suites: argv builders, a tiny policy, raw column updates."""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.paths  # noqa: E402

# Records how many copies of itself run at once, under a flock.
PEAK = """
import fcntl, json, sys, time

def bump(path, delta):
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        state = json.loads(f.read() or '{"now": 0, "peak": 0}')
        state["now"] += delta
        state["peak"] = max(state["peak"], state["now"])
        f.seek(0)
        f.truncate()
        f.write(json.dumps(state))

bump(sys.argv[1], 1)
time.sleep(1.0)
bump(sys.argv[1], -1)
"""


class Room:
    def __init__(self, admit=True, raises=None):
        self._admit, self._raises = admit, raises

    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running):
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(admit=self._admit, reason="room" if self._admit else "no room")


def touch_argv(path):
    return (sys.executable, "-c", f"open({str(path)!r}, 'w').write('ran')")


def exit_argv(code):
    return (sys.executable, "-c", f"raise SystemExit({code})")


def set_columns(root, entry_id, **cols):
    """Set named columns on one entries row. *cols* keys are column names."""
    assignments = ", ".join(f"{name} = ?" for name in cols)
    conn = sqlite3.connect(str(otto_queue.paths.db_path(root)), isolation_level=None)
    try:
        conn.execute(f"UPDATE entries SET {assignments} WHERE id = ?", (*cols.values(), entry_id))
    finally:
        conn.close()
