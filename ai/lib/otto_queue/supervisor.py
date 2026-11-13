"""The worker's supervisor, and the cgroup its jobs run in: systemd on Linux, launchd on macOS.

`otto-workbench sync` installs the units (`lib/queue_worker.sh`); this module
only starts the worker and wraps a job's command.

| | Linux | macOS |
|---|---|---|
| Worker | `otto-queue-worker.service`, a systemd user service | `com.otto-workbench.queue`, a launchd agent |
| Started by `wake` | `systemctl --user start --no-block` | `launchctl kickstart gui/<uid>/…` |
| A job runs | in a transient scope in `workbench.slice` | as the worker's child, in its own session |

`wake` confirms the worker by its lock (`otto_queue.lease.worker_alive`), not
by the start command's exit status. Starting a unit that is still running out
its idle exit is a no-op that exits 0, so the start is issued again every
`RESTART_EVERY_S` until the lock is held or `confirm_s` has passed. A start
still in flight at that deadline is waited out, so the worst case is
`confirm_s` plus one start-command timeout (`core.timeouts.LOCAL`). A start
command that fails outright (no unit installed, no user bus) is reported at
once.

A job's scope is `systemd-run --user --scope`, not a transient service: a
scope's process is started by systemd-run itself, so the job runner inherits
the admitted slot's descriptor, and stopping the worker unit does not stop it.
`OOMPolicy=continue` keeps an OOM kill to the process the kernel chose, so the
runner survives to record it. systemd 252 and older reject that assignment on
a scope; those releases don't stop the scope on OOM anyway, so the wrapper
omits the property when the probe rejects it. A slice name must not contain
`-`: systemd reads `a-b.slice` as a child of `a.slice`.
"""

# doc-group: queue

from __future__ import annotations

import os
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import core.cgroup
import core.log
import core.proc
import core.timeouts
import otto_queue.lease

SLICE = "workbench.slice"
SYSTEMD_UNIT = "otto-queue-worker.service"
LAUNCHD_LABEL = "com.otto-workbench.queue"
SCOPE_PROPERTIES = ("OOMPolicy=continue",)

# Between looks at the worker lock while a start takes effect.
WAKE_POLL_S = 0.2

# How long one start command is given before it is issued again.
RESTART_EVERY_S = 1.0

# None until a probe; then whether this process's systemd-run accepted OOMPolicy=.
_oom_policy_ok: bool | None = None


@dataclass(frozen=True)
class Wake:
    """Whether a worker is now running, and why not when it is not."""

    alive: bool
    reason: str


def start_command(platform: str = sys.platform) -> list[str] | None:
    """The command that starts the worker on *platform*; None where there is no supervisor."""
    if platform.startswith("linux"):
        return ["systemctl", "--user", "start", "--no-block", SYSTEMD_UNIT]
    if platform == "darwin":
        return ["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    return None


def wake(root: Path, *, platform: str = sys.platform, confirm_s: float = core.timeouts.LOCAL,
         poll_s: float = WAKE_POLL_S) -> Wake:
    """Start the worker unless one is running; alive only once its lock is seen held.

    Gives up after *confirm_s* plus at most one start-command timeout
    (`core.timeouts.LOCAL`): a start still running when *confirm_s* elapses is
    awaited, so the worst case is that bound, not *confirm_s* alone.
    """
    if otto_queue.lease.worker_alive(root):
        return Wake(True, "a worker is running")
    command = start_command(platform)
    if command is None:
        return Wake(False, f"no worker supervisor on {platform}")
    deadline = time.monotonic() + confirm_s
    while True:
        failure = _start(command)
        if failure is not None:
            return Wake(False, failure)
        if _await_alive(root, min(deadline, time.monotonic() + RESTART_EVERY_S), poll_s):
            return Wake(True, "the worker started")
        if time.monotonic() >= deadline:
            return Wake(False, _gave_up(platform, confirm_s))


def _gave_up(platform: str, confirm_s: float) -> str:
    reason = f"the worker did not start within {confirm_s:g}s"
    if not platform.startswith("linux"):
        return reason
    return f"{reason}; check: systemctl --user status otto-queue-worker"


def _start(command: list[str]) -> str | None:
    """None when *command* ran cleanly, else what it said."""
    try:
        result = core.proc.run(command, timeout=core.timeouts.LOCAL)
    except OSError as exc:
        return f"{command[0]}: {exc}"
    if result.ok:
        return None
    return result.detail or f"{shlex.join(command)} exited {result.returncode}"


def _await_alive(root: Path, until: float, poll_s: float) -> bool:
    while time.monotonic() < until:
        if otto_queue.lease.worker_alive(root):
            return True
        time.sleep(poll_s)
    return otto_queue.lease.worker_alive(root)


def scope_argv(argv: list[str], *, slice_name: str, unit: str) -> list[str]:
    """*argv* run in a transient scope named *unit* inside *slice_name*."""
    return _systemd_run_argv(argv, slice_name=slice_name, unit=unit,
                             property_flags=_scope_properties())


def scopes_usable(slice_name: str, *, root: Path = Path("/"),
                  cgroup_root: Path | None = None) -> bool:
    """Whether `systemd-run --user --scope`, with the properties jobs use, works here."""
    fs = root / "sys/fs/cgroup" if cgroup_root is None else cgroup_root
    if not _memory_delegated(root, fs):
        return False
    return _probe_scopes(slice_name)


def _scope_properties() -> list[str]:
    # systemd 252 and older reject OOMPolicy= on a scope; those releases don't
    # stop the scope on OOM anyway, so omitting the property matches.
    if _oom_policy_ok is False:
        return []
    return [f"--property={p}" for p in SCOPE_PROPERTIES]


def _probe_unit() -> str:
    return f"otto-queue-probe-{os.getpid()}-{time.time_ns()}"


def _run_probe(argv: list[str]) -> bool:
    try:
        return core.proc.run(argv, timeout=core.timeouts.LOCAL).ok
    except OSError:
        return False


def _systemd_run_argv(argv: list[str], *, slice_name: str, unit: str,
                      property_flags: list[str]) -> list[str]:
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", *property_flags,
            f"--slice={slice_name}", f"--unit={unit}", "--", *argv]


def _probe_argv(slice_name: str, properties: tuple[str, ...]) -> list[str]:
    return _systemd_run_argv(
        ["true"], slice_name=slice_name, unit=_probe_unit(),
        property_flags=[f"--property={p}" for p in properties])


def _probe_scopes(slice_name: str) -> bool:
    global _oom_policy_ok
    if _run_probe(_probe_argv(slice_name, SCOPE_PROPERTIES)):
        _oom_policy_ok = True
        return True
    _oom_policy_ok = False
    return _run_probe(_probe_argv(slice_name, ()))


def _memory_delegated(root: Path, cgroup_root: Path) -> bool:
    """Whether the user manager's `cgroup.controllers` lists `memory`."""
    manager = core.cgroup.user_manager_cgroup(root)
    if manager is None:
        core.log.warn(
            "workbench.slice scopes need the user manager's memory controller; "
            "this process is not in a user@N.service cgroup")
        return False
    path = cgroup_root.joinpath(*manager.parts[1:]) / "cgroup.controllers"
    try:
        text = path.read_text()
    except OSError:
        text = ""
    if "memory" in text.split():
        return True
    core.log.warn(
        f"memory controller is not delegated to {manager}; "
        "jobs would not be capped by workbench.slice")
    return False
