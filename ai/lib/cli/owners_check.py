"""``owners-check`` — the owner boundary, as both agent guards and git hooks reach it.

    owners-check --cwd DIR -- COMMAND
    owners-check --configured
    owners-check --classify -- COMMAND
    owners-check --message-file FILE --cwd DIR
    owners-check --push REMOTE URL --cwd DIR

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

``--message-file`` checks a commit message (commit-msg hook). ``--push``
checks commits about to be pushed (pre-push hook), reading git's ref lines
from stdin. Both exit 0 to allow, 2 to refuse, and 3 when the check could not
run. A content refusal is lifted when the user set ``WORKBENCH_ALLOW_CROSS_REF=1``,
with a warning on stderr.

``WORKBENCH_ALLOW_UPSTREAM`` is not read here — see ``gh.owners``.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

import config.workbench_config
import gh.owner_hooks
import gh.owners
import gh.write_shapes

REFUSED = 2
NOT_CONFIGURED = 1
CANNOT_RUN = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owners-check",
        description="Check a GitHub write command or hook payload against github.owners.",
    )
    parser.add_argument("--cwd", help="the directory the command or hook runs in")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configured", action="store_true",
                      help="exit 0 when github.owners names an owner, 1 otherwise")
    mode.add_argument("--classify", action="store_true",
                      help="print 'write' or 'read' for COMMAND; exit 0 either way")
    mode.add_argument("--message-file", metavar="FILE",
                      help="check a commit message file (commit-msg hook)")
    mode.add_argument("--push", nargs=2, metavar=("REMOTE", "URL"),
                      help="check commits about to be pushed (pre-push hook)")
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


def _require_cwd(parser: argparse.ArgumentParser, cwd: str | None) -> str:
    if not cwd:
        parser.error("--cwd is required")
    return cwd


def _hook_exit(verdict: gh.owner_hooks.HookVerdict) -> int:
    if verdict.reason:
        print(verdict.reason, file=sys.stderr)
    if verdict.outcome is gh.owner_hooks.HookOutcome.ALLOW:
        return 0
    if verdict.outcome is gh.owner_hooks.HookOutcome.REFUSE:
        return REFUSED
    return CANNOT_RUN


def _message_file(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    cwd = _require_cwd(parser, args.cwd)
    path = Path(args.message_file)
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return CANNOT_RUN
    return _hook_exit(gh.owner_hooks.check_message(text, cwd))


def _push(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    cwd = _require_cwd(parser, args.cwd)
    remote, url = args.push
    return _hook_exit(gh.owner_hooks.check_push(
        remote, url, sys.stdin.read().splitlines(), cwd))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.configured:
        return _configured()
    if args.classify:
        return _classify(_command_line(parser, args.command))
    if args.message_file is not None:
        return _message_file(parser, args)
    if args.push is not None:
        return _push(parser, args)
    cwd = _require_cwd(parser, args.cwd)
    command = _command_line(parser, args.command)
    verdict = gh.owners.check_command(command, cwd)
    if verdict.ok:
        return 0
    print(verdict.reason, file=sys.stderr)
    return REFUSED
