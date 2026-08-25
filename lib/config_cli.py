"""Read and write the workbench config, checked against the keys it reads.

Usage: python3 lib/config_cli.py set KEY VALUE [--project]
       otto-workbench config set KEY VALUE [--project]
       otto-workbench config status

`status` answers the two questions the files themselves cannot: what is the
value right now, and which file supplied it.  The loader deep-merges every
scope and returns the result, so a value inherited from the machine-wide file
and a value set by the repo in front of you look identical afterwards — and a
key written under a name nothing reads looks exactly like a key nobody ever
set, in both directions, for as long as it takes somebody to notice the rule
it was meant to turn on is not applying.

The config files are hand-authorable and the schema modeline is there so an
editor completes them, so for a person this command is a convenience.  For an
agent it is the difference between recording an answer and losing one.

An agent asked to record something — where a repo files its issues, which reuse
level to run at — reads the key out of the checkout it is working in.  A
worktree is routinely weeks behind `main`; that is what worktrees are for.  So
the key it reads is correct for the code in front of it and may be a name the
config moved off two hours ago, and the file it writes is shared by every repo
on the machine.  Nothing about the write says so: `serde` drops a key it does
not know, so the value is simply absent the next time anything looks, and the
rule that depended on it quietly stops applying.

Invoked by its bare name this command *is* the installed workbench — the
launcher on `PATH` is a symlink into the checkout that installed it — so the
key surface it validates against is the current one no matter which worktree
the caller is standing in.  Run out of a stale checkout instead, it still
refuses: `workbench_config.check_key` asks the installed schema as well as its
own.  See `ai/lib/workbench_config.py`.

Exit codes: 0 on a completed write or a report that read every scope, 1 on a
refused key, a failed write, or a scope that could not be read, 2 on a usage
error (argparse's own).

A stray key is reported and still exits 0.  It is the finding this command
exists for, but the command's job is to show the config, and a report that
fails because the thing it reported is bad is one nobody can put in a script.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
_WORKBENCH_DIR = os.path.dirname(_LIB_DIR)
for _path in (_LIB_DIR, os.path.join(_WORKBENCH_DIR, 'ai', 'lib')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import workbench_config  # noqa: E402
from ansi import BOLD, DIM, GREEN, NC, RED, YELLOW  # noqa: E402


def _project_root() -> Path | None:
    """The work-tree root of the repo the caller is in, or ``None`` outside one."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="otto-workbench config",
        description="Write one key of the workbench config, checked before it lands.",
    )
    sub = parser.add_subparsers(required=True, metavar="command")
    setter = sub.add_parser("set", help="write one dotted key")
    setter.add_argument("key", help="dotted key, e.g. issue_tracker.provider")
    setter.add_argument("value", help="the value to record")
    setter.add_argument(
        "--project", action="store_true",
        help="write the current repo's .workbench.yml instead of the global file",
    )
    setter.set_defaults(run=_write)
    reporter = sub.add_parser(
        "status", help="show every scope, every value, and the file it came from",
    )
    reporter.set_defaults(run=_status)
    return parser.parse_args(argv)


def _status(_args: argparse.Namespace) -> int:
    """Print every scope, every resolved value, and the file each came from.

    All of it on stdout, including the problems. The exit code is what a script
    reads, and splitting the report across two streams only guarantees that the
    line explaining why a section is missing lands somewhere other than where
    the section would have been.
    """
    status = workbench_config.config_status(_project_root())

    print(f"{BOLD}Scopes{NC} {DIM}— highest precedence first{NC}")
    for scope in status.scopes:
        mark = f"{GREEN}✓{NC}" if scope.exists else f"{DIM}·{NC}"
        note = "" if scope.exists else f"  {DIM}(no file){NC}"
        print(f"  {mark} {scope.name:<8} {scope.path}{note}")

    for problem in status.problems:
        print(f"  {RED}✗{NC} {problem}")

    if status.keys:
        key_width = max(len(row.key) for row in status.keys)
        value_width = max(len(row.value) for row in status.keys)
        print(f"\n{BOLD}Values{NC}")
        for row in status.keys:
            source = "default" if row.is_default else row.scope.name
            tint = DIM if row.is_default else NC
            print(f"  {row.key:<{key_width}}  {row.value:<{value_width}}  "
                  f"{tint}{source}{NC}")

    if status.strays:
        print(f"\n{BOLD}Keys nothing reads{NC} {DIM}— the value is dropped on load{NC}")
        width = max(len(stray.key) for stray in status.strays)
        for stray in status.strays:
            print(f"  {YELLOW}✗{NC} {stray.key:<{width}}  {DIM}{stray.scope.path}{NC}")

    return 0 if status.ok else 1


def _write(args: argparse.Namespace) -> int:
    if not args.project:
        workbench_config.set_value(args.key, args.value)
        print(f"{GREEN}✓{NC} {args.key} = {args.value}")
        print(f"  {DIM}{workbench_config.global_config_path()} — every repo{NC}")
        return 0

    root = _project_root()
    if root is None:
        print(f"{RED}✗{NC} --project needs a git repo, and this is not one", file=sys.stderr)
        return 1
    workbench_config.set_project_value(args.key, args.value, root)
    target = workbench_config.project_config_path(root)
    print(f"{GREEN}✓{NC} {args.key} = {args.value}")
    print(f"  {DIM}{target} — commit it so the repo keeps the answer{NC}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return args.run(args)
    except workbench_config.ConfigKeyError as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        print(
            f"  {DIM}every key the config accepts is listed at "
            f"{workbench_config.SCHEMA_URL}{NC}",
            file=sys.stderr,
        )
        return 1
    except workbench_config.ConfigError as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
