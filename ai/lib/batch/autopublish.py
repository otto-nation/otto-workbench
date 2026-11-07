"""Run a publish child: `default_runner` and the log header it writes.

`default_runner` writes a `$ <argv>` header before each child's output so a
failure's reason is read from that command alone (`last_command`).
"""

# doc-group: batch

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import core.timeouts

# What default_runner writes before each child command's output in a publish log.
COMMAND_HEADER = "$ "


def default_runner(argv: list[str], log_path: Path | None = None, *,
                   env: Mapping[str, str] | None = None) -> int:
    """Run one publish command as a child.

    With *log_path*, a `$ <argv>` header and everything it prints are appended
    there. *env* is the child's environment, this process's when None.
    """
    if log_path is None:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                              start_new_session=True, timeout=core.timeouts.UNBOUNDED,
                              env=env).returncode
    with log_path.open("a") as log:
        log.write(f"{COMMAND_HEADER}{shlex.join(argv)}\n")
        log.flush()
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              timeout=core.timeouts.UNBOUNDED, env=env).returncode


def last_command(lines: list[str]) -> list[str]:
    """The lines the failing command printed: those after the last `$ ` header.

    `default_runner` writes a `$ <argv>` header before each child command, so
    an earlier command that succeeded cannot lend the failure its reason. A
    log with no header is read whole.
    """
    starts = [i for i, line in enumerate(lines) if line.startswith(COMMAND_HEADER)]
    return lines[starts[-1] + 1:] if starts else lines
