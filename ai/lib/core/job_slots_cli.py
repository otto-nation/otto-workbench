"""Command-line face of the test-parallelism slot pool.

Separate from job_slots.py so the library stays importable without argparse
ceremony, and so bash has one file to invoke. The slots must wrap a child
process rather than be claimed and returned from: a flock lives only as long
as the process holding its descriptor, so a script that claimed and exited
would hand its caller a number backed by nothing.

The grant is passed to the child in the environment rather than printed,
because stdout belongs to the suite — the pre-push hook parses run-tests'
TAP stream, and a number on it would be read as a test result.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import timeouts
from core.job_slots import claim, holders

GRANT_ENV = "WORKBENCH_TEST_SLOTS_GRANTED"


def _show() -> int:
    """Print the slot records. Always exit 0 — this is a report, not a probe."""
    records = holders()
    if not records:
        print("test slots: no record of any run")
        return 0
    print("test slots (a record is the last holder, not a live one):")
    for record in records:
        # `or`, not a dict default: a claim taken through the library rather
        # than this CLI has no label to give, and writes the empty string —
        # which a default never replaces, leaving a blank column.
        command = record.get("command") or "unknown command"
        print(f"  slot {record.get('slot', '?'):>2}  pid {record.get('pid', '?')}"
              f"  {command}"
              f"  (started {record.get('started', 'unknown time')})")
    return 0


def _run_child(child: list[str], granted: int) -> int:
    """Run *child* in its own process group and wait until that group is gone.

    SIGINT, SIGTERM and SIGHUP are forwarded to the group rather than killing
    this process: the slots are held for as long as we wait, so dropping them
    while descendants still run is the failure this wrapper exists to prevent.
    A terminal Ctrl-C no longer reaches the child by process-group membership
    (it is in a new session), so the handler is what delivers it. SIGHUP is the
    same gap for a closed terminal — without a handler the wrapper dies, the
    slots free, and the suite keeps running outside the pool.
    """
    env = os.environ.copy()
    env[GRANT_ENV] = str(granted)
    proc = subprocess.Popen(child, start_new_session=True, env=env)

    def _forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signum)
        except ProcessLookupError:
            pass

    previous = {
        signum: signal.signal(signum, _forward)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        code = proc.wait(timeout=timeouts.UNBOUNDED)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    # A negative returncode is -signal. sys.exit(-N) becomes 256-N;
    # callers expect the shell convention 128+N (SIGTERM -> 143).
    return 128 + (-code) if code < 0 else code


def main(argv: list[str], child: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--want", type=int)
    parser.add_argument("--floor", type=int, default=1)
    parser.add_argument("--cores", type=int)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    if args.show:
        return _show()

    if args.want is None or args.cores is None or not child:
        print("job_slots_cli: need --want, --cores and a command", file=sys.stderr)
        return 2

    label = args.label or " ".join(child)
    with claim(args.want, args.floor, args.cores, command=label) as granted:
        return _run_child(child, granted)


if __name__ == "__main__":
    # The child command is split off before argparse sees anything. Passing it
    # through as a positional would have argparse claim its flags as our own:
    # `-- sh -c 'exit 7'` dies on "unrecognized arguments: -c exit 7".
    argv = sys.argv[1:]
    child: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, child = argv[:split], argv[split + 1:]
    sys.exit(main(argv, child))
