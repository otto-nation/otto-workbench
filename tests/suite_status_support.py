"""Which test each pytest process is running, for `core.suite_watch` to report.

`bin/local/suite-watch` exports WORKBENCH_SUITE_STATUS_DIR and reads one file
per process from it on every heartbeat, so a slow suite can name the tests in
flight and how long each has been running. Registered from `conftest.py` as a
plugin; every hook here is a no-op when the variable is unset.
"""

import os
import time
from pathlib import Path

# The session's config, kept so the hooks below — whose signatures carry no
# config — can tell an xdist controller from a worker.
_CONFIG = None


def pytest_configure(config):
    """Remember the session's config for `_status_path`."""
    global _CONFIG
    _CONFIG = config


def _status_path():
    """Where this process records its in-flight test, or None when it should not.

    None unless the supervisor exported WORKBENCH_SUITE_STATUS_DIR. Also None in
    the xdist controller: it re-emits every worker's logstart/logfinish to these
    same hooks, and with no PYTEST_XDIST_WORKER it would write one shared `main`
    file that the last-started worker overwrites and the first-finished unlinks.
    Only a process that actually runs a test writes a record.
    """
    root = os.environ.get("WORKBENCH_SUITE_STATUS_DIR")
    if not root:
        return None
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        if _CONFIG is not None and _CONFIG.pluginmanager.hasplugin("dsession"):
            return None
        worker = "main"
    return Path(root) / worker


def pytest_runtest_logstart(nodeid, location):
    """Tell the suite heartbeat which test is in flight, when it is watching.

    The file is per xdist worker so parallel tests do not overwrite each other.
    Written atomically: a reader that opened a half-written file would drop the
    nodeid.
    """
    path = _status_path()
    if path is None:
        return
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(f"{time.time()}\n{nodeid}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def pytest_runtest_logfinish(nodeid, location):
    """Clear the in-flight record so a finished test is not reported as running."""
    path = _status_path()
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
