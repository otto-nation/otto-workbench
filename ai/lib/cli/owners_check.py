"""``owners-check`` — the owner boundary, as both agent guards reach it.

    owners-check --cwd DIR -- COMMAND
    owners-check --configured
    owners-check --classify -- COMMAND

The first form is what ``claude-bash-guard`` and Pi's ``upstream-guard`` run
once their pre-filter says a command might write to GitHub. Exit 0 allows;
exit 2 refuses, with the reason on stderr; anything else — a crash, a missing
``python3`` — is read by both guards as a check that could not run, and they
refuse. COMMAND is the whole command line as one argument; several arguments
are rejoined with their quoting.

The second form exits 0 when ``github.owners`` names at least one owner and 1
otherwise, with the reason on stderr. ``ai sync`` prints it as a notice.

The third form prints exactly one line on stdout — ``write`` when COMMAND
writes to GitHub, ``read`` when it does not — and exits 0 for both. It never
reads config or cwd: both harnesses' subagent-publishing guards use it as the
one classifier so they do not keep a bash/TS copy of the write shapes. The
guards take a command as not publishing only on exit 0 with ``read``; any
other exit or output counts as a write, so a crash (which also exits 1) can
never read as an answer. An unparseable command is answered ``write``.

``WORKBENCH_ALLOW_UPSTREAM`` is not read here — see ``gh.owners``.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import shlex
import sys

import config.workbench_config
import gh.owners
import gh.write_shapes

REFUSED = 2
NOT_CONFIGURED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owners-check",
        description="Check a GitHub write command against github.owners.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cwd", help="the directory the command runs in")
    mode.add_argument("--configured", action="store_true",
                      help="exit 0 when github.owners names an owner, 1 otherwise")
    mode.add_argument("--classify", action="store_true",
                      help="print 'write' or 'read' for COMMAND; exit 0 either way")
    parser.add_argument("command", nargs="*", help="the command, after --")
    return parser


def _configured() -> int:
    try:
        owner_map = gh.owners.OwnerMap.load()
    except config.workbench_config.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return NOT_CONFIGURED
    if owner_map.configured:
        return 0
    print(gh.owners.UNCONFIGURED, file=sys.stderr)
    return NOT_CONFIGURED


def _command_line(parser: argparse.ArgumentParser, command: list[str]) -> str:
    if not command:
        parser.error("a command is required after --")
    return command[0] if len(command) == 1 else shlex.join(command)


WRITE = "write"
READ = "read"


def _classify(command: str) -> int:
    try:
        is_write = gh.write_shapes.command_is_write(command)
    except ValueError:
        is_write = True
    print(WRITE if is_write else READ)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.configured:
        return _configured()
    command = _command_line(parser, args.command)
    if args.classify:
        return _classify(command)
    verdict = gh.owners.check_command(command, args.cwd)
    if verdict.ok:
        return 0
    print(verdict.reason, file=sys.stderr)
    return REFUSED
