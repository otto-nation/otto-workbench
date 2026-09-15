"""Command-line face of the tree validation lock.

Separate from tree_lock.py so the library stays importable without argparse
ceremony, and so bash has one file to invoke. See that module for why the lock
must wrap a child process rather than be claimed and returned from.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import datetime
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import timeouts
from core.tree_lock import acquire, holders, is_locked


def _check(tree_root: Path) -> int:
    """Report holders. Exit 0 when the tree is being validated, 1 when free."""
    if not is_locked(tree_root):
        print(f"{tree_root}: not being validated")
        return 1
    print(f"{tree_root}: validating")
    for record in holders(tree_root):
        pid = record.get("pid", "?")
        command = record.get("command", "unknown command")
        started = record.get("started", "unknown time")
        print(f"  pid {pid}  {command}  (started {started})")
    print("stop it with: kill <pid>")
    return 0


def _run_child(child: list[str]) -> int:
    """Run *child* in its own process group and wait until that group is gone.

    SIGINT and SIGTERM are forwarded to the group rather than killing this
    process. The lock is held for as long as we wait, so dropping it while
    descendants are still running is the failure this wrapper exists to
    prevent. A terminal Ctrl-C no longer reaches the child by process-group
    membership (it is in a new session), so the SIGINT handler is what
    delivers it.
    """
    proc = subprocess.Popen(child, start_new_session=True)

    def _forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signum)
        except ProcessLookupError:
            pass

    previous = {
        signum: signal.signal(signum, _forward)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            code = proc.wait(timeout=timeouts.UNBOUNDED)
        except KeyboardInterrupt:
            # Popen.wait translates SIGINT into KeyboardInterrupt and re-raises
            # after a brief wait, assuming the child got the terminal's ^C too.
            # The child is in a new session, so it did not; forward and wait.
            _forward(signal.SIGINT, None)
            code = proc.wait(timeout=timeouts.UNBOUNDED)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    # A negative returncode is -signal. sys.exit(-N) becomes 256-N;
    # callers expect the shell convention 128+N (SIGTERM -> 143).
    return 128 + (-code) if code < 0 else code


def main(argv: list[str], child: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--check", dest="check")
    parser.add_argument("--tree")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    if args.check:
        return _check(Path(args.check))

    if not args.tree or not child:
        print("tree_lock_cli: need --tree and a command", file=sys.stderr)
        return 2

    label = args.label or " ".join(child)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    with acquire(Path(args.tree), command=label, started=started):
        return _run_child(child)


if __name__ == "__main__":
    # The child command is split off before argparse sees anything. Passing it
    # through as a positional would have argparse claim its flags as our own:
    # `-- sh -c 'exit 7'` dies on "unrecognized arguments: -c exit 7".
    argv = sys.argv[1:]
    child: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, child = argv[:split], argv[split + 1 :]
    sys.exit(main(argv, child))
