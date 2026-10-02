"""Supervise a test-suite child and say what it is doing.

A parallel bats run with GNU ``--keep-order`` buffers TAP until the current
head file finishes, so a slow suite and a stuck one look the same: silence.
This wrapper leaves the child's stdout alone — the TAP / pytest stream the
pre-push hook parses — writes one heartbeat line to stderr every
``TEST_HEARTBEAT_SECS``, and exits with the child's status.

Waiting on the child is :data:`core.timeouts.UNBOUNDED` because the suite *is*
the work. A bound would convert a large or contended run into a false failure;
the heartbeat is what makes that wait observable rather than a hang.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.signal_relay
import core.timeouts

ENV_INTERVAL = "TEST_HEARTBEAT_SECS"
STATUS_ENV = "WORKBENCH_SUITE_STATUS_DIR"
DEFAULT_INTERVAL = 60.0

# Heartbeat lines must never look like TAP: the pre-push hook greps stdout for
# `^ok`, `^not ok`, `^# ` and `^1..`, and a captured-stdout regression would
# parse a progress line as a test result.
_HEARTBEAT_PREFIX = "⏱"


@dataclass(frozen=True)
class Inflight:
    """One piece of work still running, with how long it has been."""

    name: str
    age_s: int


class PsRow(NamedTuple):
    """One ``ps`` line: a process, its parent, how long it has run, its argv."""

    pid: int
    ppid: int
    etime: str
    command: str


def heartbeat_interval(raw: str | None = None) -> float:
    """Seconds between heartbeats. ``0`` disables the heartbeat.

    *raw* is ``TEST_HEARTBEAT_SECS`` when omitted. Empty or unset is the
    default, not zero: an exported empty string is a real environment, and
    treating it as disable would silence the heartbeat for a caller that
    meant to inherit.
    """
    if raw is None:
        raw = os.environ.get(ENV_INTERVAL)
    if raw is None or raw == "":
        return DEFAULT_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{ENV_INTERVAL} must be a number, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{ENV_INTERVAL} must be >= 0, got {raw!r}")
    return value


def parse_etime(text: str) -> int:
    """``ps`` elapsed time (``[[dd-]hh:]mm:ss``) as whole seconds."""
    text = text.strip()
    days = 0
    if "-" in text:
        day_part, text = text.split("-", 1)
        days = int(day_part)
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 1:
        hours = minutes = 0
        seconds = parts[0]
    else:
        raise ValueError(f"unrecognised etime: {text!r}")
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def format_duration(seconds: int) -> str:
    """Compact elapsed time: ``41s``, ``3m02s``, ``4m10s``, ``1h03m00s``."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


def format_heartbeat(
    suite: str,
    elapsed_s: int,
    jobs: int | None,
    running: list[Inflight],
) -> str:
    """One stderr line naming the suite, elapsed time, jobs, and in-flight work."""
    jobs_part = f", {jobs} jobs" if jobs is not None else ""
    if running:
        items = ", ".join(
            f"{item.name} ({format_duration(item.age_s)})" for item in running
        )
        running_part = f" — running: {items}"
    else:
        running_part = " — running: (none)"
    return (
        f"{_HEARTBEAT_PREFIX} {suite}: {format_duration(elapsed_s)} elapsed"
        f"{jobs_part}{running_part}"
    )


def parse_ps_line(line: str) -> PsRow | None:
    """``pid ppid etime command`` from ``ps -A -o pid=,ppid=,etime=,command=``."""
    parts = line.split(None, 3)
    if len(parts) < 4:
        return None
    try:
        return PsRow(int(parts[0]), int(parts[1]), parts[2], parts[3])
    except ValueError:
        return None


def _ps_rows() -> list[PsRow]:
    """Every process, or an empty list if ``ps`` cannot answer.

    ceiling: text from ``ps`` rather than a libc iterator. Upgrade if a
    platform we run on truncates ``command`` enough to drop the bats file
    path, or if Windows becomes a runner.
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=core.timeouts.QUICK,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        parsed = parse_ps_line(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def descendants(root_pid: int, rows: list[PsRow]) -> list[PsRow]:
    """Processes whose parent chain leads to *root_pid*, excluding it."""
    by_parent: dict[int, list[PsRow]] = {}
    for row in rows:
        by_parent.setdefault(row.ppid, []).append(row)
    out: list[PsRow] = []
    stack = [root_pid]
    seen = {root_pid}
    while stack:
        _push_children(stack.pop(), by_parent, seen, stack, out)
    return out


def _push_children(
    current: int,
    by_parent: dict[int, list[PsRow]],
    seen: set[int],
    stack: list[int],
    out: list[PsRow],
) -> None:
    """Enqueue unseen children of *current*."""
    for child in by_parent.get(current, []):
        pid = child.pid
        if pid in seen:
            continue
        seen.add(pid)
        out.append(child)
        stack.append(pid)


def bats_file_from_command(command: str) -> str | None:
    """Basename of the file a ``bats-exec-file`` process is running, if any.

    bats-core puts flags and the file's index before the file —
    ``bats-exec-file [flags] 3 /t/x.bats /tmp/list`` — so the token right after
    the executable is never the file. The first ``.bats`` token after it is.
    ``ps`` flattens argv, so a path with a space arrives in pieces; its last
    piece still carries the file's name, which is all this reports.
    """
    parts = command.split()
    for index, part in enumerate(parts):
        if Path(part).name != "bats-exec-file":
            continue
        for candidate in parts[index + 1:]:
            if candidate.endswith(".bats"):
                return Path(candidate).name
        return None
    return None


def bats_inflight(root_pid: int, rows: list[PsRow] | None = None) -> list[Inflight]:
    """In-flight bats files under *root_pid*, longest-running first."""
    if rows is None:
        rows = _ps_rows()
    found: list[Inflight] = []
    for row in descendants(root_pid, rows):
        name = bats_file_from_command(row.command)
        if name is None:
            continue
        try:
            age = parse_etime(row.etime)
        except ValueError:
            continue
        found.append(Inflight(name=name, age_s=age))
    found.sort(key=lambda item: item.age_s, reverse=True)
    return found


def pytest_inflight(status_dir: str | os.PathLike[str], now: float | None = None) -> list[Inflight]:
    """In-flight pytest nodeids from per-worker files under *status_dir*."""
    root = Path(status_dir)
    if not root.is_dir():
        return []
    if now is None:
        now = time.time()
    found: list[Inflight] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix == ".tmp":
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        lines = text.splitlines()
        if len(lines) < 2:
            continue
        try:
            started = float(lines[0])
        except ValueError:
            continue
        nodeid = lines[1].strip()
        if not nodeid:
            continue
        age = max(0, int(now - started))
        found.append(Inflight(name=nodeid, age_s=age))
    found.sort(key=lambda item: item.age_s, reverse=True)
    return found


def inflight_for(suite: str, child_pid: int, status_dir: str | None) -> list[Inflight]:
    """Discover in-flight work the way *suite* reports it."""
    if suite == "pytest" and status_dir:
        return pytest_inflight(status_dir)
    if suite == "bats":
        return bats_inflight(child_pid)
    return []


def child_status(code: int) -> int:
    """Translate Popen's negative signal death to the shell's ``128+N``."""
    return 128 + (-code) if code < 0 else code


def _spawn(child: list[str], env: dict[str, str]) -> subprocess.Popen | None:
    """Start *child* in its own session, so a signal reaches it exactly once.

    A group-wide stop sent to this process's group (``claim-job-slots`` does
    that to ``run-tests``'s) would otherwise hit the child directly and then
    again through the relay. In its own session the child hears only what the
    relay forwards, to its own group, as ``job_slots_cli`` does.
    """
    try:
        return subprocess.Popen(child, env=env, start_new_session=True)
    except OSError as exc:
        print(f"suite_watch: cannot run {child[0]}: {exc}", file=sys.stderr)
        return None


def _wait_slice(proc: subprocess.Popen, interval: float) -> int | None:
    """Child exit code, or None if this heartbeat slice expired."""
    try:
        return proc.wait(timeout=interval)
    except subprocess.TimeoutExpired:
        return None


def _heartbeat_until_done(
    proc: subprocess.Popen,
    suite: str,
    jobs: int | None,
    interval: float,
    started: float,
    status_dir: str | None,
) -> int:
    """Wait forever, printing a heartbeat each *interval* until the child exits."""
    while True:
        code = _wait_slice(proc, interval)
        if code is not None:
            return child_status(code)
        elapsed = int(time.monotonic() - started)
        running = inflight_for(suite, proc.pid, status_dir)
        print(
            format_heartbeat(suite, elapsed, jobs, running),
            file=sys.stderr,
            flush=True,
        )


def run_supervised(
    child: list[str],
    *,
    suite: str,
    jobs: int | None,
    interval: float,
) -> int:
    """Spawn *child* with inherited stdio and heartbeat until it exits.

    ``interval == 0`` skips the heartbeat but still waits, so a signal death
    is reported as ``128+N`` the way ``job_slots_cli`` does. Exec-ing the
    child would leave a Python parent seeing ``-N`` instead.

    The child runs in its own session and :mod:`core.signal_relay` forwards
    SIGINT/SIGTERM/SIGHUP to its group, so a stop sent to this process's whole
    group (as ``claim-job-slots`` does) reaches the child once, not twice.
    """
    status_dir = None
    try:
        env = os.environ.copy()
        # Passed to the child alone, never set on this process: the directory
        # is deleted below, and a disabled heartbeat must not let an outer
        # supervisor's value through to pytest's workers.
        env.pop(STATUS_ENV, None)
        if interval > 0:
            status_dir = tempfile.mkdtemp(prefix="suite-watch-")
            env[STATUS_ENV] = status_dir
        started = time.monotonic()
        # Entered before the spawn, which is what makes a signal landing early
        # deferred rather than dropped.
        with core.signal_relay.forwarding_signals() as relay:
            proc = _spawn(child, env)
            if proc is None:
                return 127
            relay.forward_to(proc)
            if interval == 0:
                # The suite is the work; a bound here would convert a long run
                # into a false failure. See the module docstring.
                return child_status(proc.wait(timeout=core.timeouts.UNBOUNDED))
            return _heartbeat_until_done(
                proc, suite, jobs, interval, started, status_dir,
            )
    finally:
        if status_dir is not None:
            shutil.rmtree(status_dir, ignore_errors=True)


def main(argv: list[str], child: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--suite", default="")
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)

    if args.help:
        print(
            "suite-watch — heartbeat a test-suite child on stderr\n"
            "\n"
            "Usage:\n"
            "  suite-watch [--suite NAME] [--jobs N] -- <command> [args...]\n"
            "\n"
            f"Environment:\n"
            f"  {ENV_INTERVAL}  Seconds between heartbeats "
            f"(default {int(DEFAULT_INTERVAL)}; 0 disables the heartbeat).\n",
            end="",
        )
        return 0

    if not child:
        print("suite_watch: no command given after --", file=sys.stderr)
        return 2

    suite = args.suite or Path(child[0]).name
    try:
        interval = heartbeat_interval()
    except ValueError as exc:
        print(f"suite_watch: {exc}", file=sys.stderr)
        return 2
    return run_supervised(child, suite=suite, jobs=args.jobs, interval=interval)


if __name__ == "__main__":
    argv = sys.argv[1:]
    child: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, child = argv[:split], argv[split + 1:]
    sys.exit(main(argv, child))
