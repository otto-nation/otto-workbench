"""Helpers for suites that open `pr batch` runs through `batch.store` sessions.

A run reaches disk only through a session, so a test that needs a saved run
seeds it the way `pr batch run` does: `create`, one `commit`, a normal exit.
`NullInbox` is the policy those sessions drain with. It applies requests
through the real `batch.inbox.apply_one`, with a runner whose every command
succeeds without running, so a drain behaves exactly as a live one would,
minus the children. `seed_raw` is the one exception to "only through a
session", for fixtures a session would settle.
"""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.inbox  # noqa: E402
import batch.store  # noqa: E402
import core.serde  # noqa: E402
from batch.model import RunStatus  # noqa: E402
from batch.store import Role  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402

_HOLDING_COMMANDS = {
    "driver": "pr batch resume",
    "applier": "pr batch resolve",
    "gc": "pr gc",
}


def _no_command(*args, **kwargs) -> int:
    """A resolve runner whose every command succeeds without running."""
    return 0


class NullInbox:
    """An InboxPolicy that applies through `batch.inbox` and keeps every outcome it reports."""

    def __init__(self) -> None:
        self.outcomes = []
        self._ctx = batch.inbox.ApplyContext(pr_bin="pr", runner=_no_command)

    def apply(self, run, envelope):
        return batch.inbox.apply_one(run, envelope.request, self._ctx, name=envelope.name)

    def settle(self, run):
        return batch.inbox.settle_idle(run)

    def report(self, run, outcome):
        self.outcomes.append(outcome)


def seed(run):
    """Save *run* as a new run would be: `create`, commit, exit. Returns *run*.

    The exit runs `finish`, so a run whose items are all terminal is settled
    to DONE, and one carrying a cancel is settled to CANCELLED, as it would
    be by any session.
    """
    with batch.store.create(run, inbox=NullInbox(), command="test seed") as session:
        session.commit()
    return run


def seed_raw(run):
    """Write *run*'s `state.json` directly, bypassing the session on purpose. Returns *run*.

    Exists only for fixtures a session would change on the way in: an empty
    run, which `finish` settles to DONE, or a terminal run whose refs a
    terminal commit drops. It takes no lock and drains nothing; production
    code never writes a run this way.
    """
    path = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    core.serde.write_json(path, core.serde.to_dict(run))
    return run


@contextmanager
def open_session(run_id, role=Role.DRIVER, inbox=None):
    """`batch.store.session` with a NullInbox unless *inbox* is given."""
    policy = inbox if inbox is not None else NullInbox()
    with batch.store.session(run_id, role, inbox=policy, command="test session") as session:
        yield session


def holding(run_id, role, command=None):
    """Hold *run_id*'s lock as a live *role* in another process.

    *role* is a `Role` or its value. The parent's pid is alive and is not
    ours, which is what makes the record a live holder rather than a vacated
    one. The record is left behind when the block ends, so a test that reads
    `holder()` afterwards must first let a session overwrite it.
    """
    value = role.value if isinstance(role, Role) else role
    if command is None:
        command = _HOLDING_COMMANDS.get(value, "pr batch x")
    record = {"pid": os.getppid(), "command": command, "started": "t0",
              "role": value, "run": run_id}
    return held_elsewhere(batch.store.run_lock(run_id).path, record)


def stops_at(status: RunStatus):
    """A `Scheduler.run_until_blocked` stand-in that settles the run at *status* without driving it.

    It sets `run.status` as the real loop's settle does. `pr batch run` and
    `resume` read their exit code from the committed run, so a stand-in that
    only returned a status would leave the run RUNNING and exit 1.
    """

    def run_until_blocked(self):
        self.run.status = status
        return status

    return run_until_blocked
