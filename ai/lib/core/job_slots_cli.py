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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.signal_relay
from core.job_slots import GRANT_ENV, claim, holders


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
        # Every field through str() before it is formatted. holders() tolerates
        # a malformed record rather than raising, and stopping one layer short
        # of its consumers would have that tolerance end in a TypeError here:
        # `:>2` rejects a list where it accepts an int or a str.
        slot = str(record.get("slot", "?"))
        pid = str(record.get("pid", "?"))
        started = str(record.get("started", "unknown time"))
        print(f"  slot {slot:>2}  pid {pid}  {command}  (started {started})")
    return 0


def _run_child(child: list[str], granted: int) -> int:
    """Run *child* under the shared relay, with the grant in its environment."""
    env = os.environ.copy()
    env[GRANT_ENV] = str(granted)
    return core.signal_relay.run_child(
        child, env=env, origin="job_slots_cli",
    )


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
