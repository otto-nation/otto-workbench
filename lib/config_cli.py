"""Read and write the workbench config, checked against the keys it reads.

Usage: python3 lib/config_cli.py set KEY VALUE [--project|--container]
       otto-workbench config set KEY VALUE [--project|--container]
       otto-workbench config status
       otto-workbench config get KEY [DIR ...]

`get` is the read side for a caller that is not Python.  `ai/lib/
workbench_config.py` owns which files a key is resolved from; bash has no
container-aware resolver of its own, and the two partial ones it used to carry
disagreed with the typed loader about the same repo in the same session — the
machine profile printed `unset` for a tracker recorded above the worktrees
while the SessionStart line named it.  So there is one resolver and this is how
the other language reaches it.

One line per DIR, in the order given, three tab-separated fields:

    SCOPE <TAB> VALUE <TAB> DIR

SCOPE is the name of the file that answered — `project`, `container`, `global`
— or `default` when no file did.  VALUE is the resolved value, empty when
nothing is set; it is collapsed onto one line so a caller reading records with
`read` cannot have a row split under it.  DIR is echoed back last so a batch
caller matches answers to repos by name rather than by position, and so a value
holding a tab could still only ever confuse itself.

Every record has both tabs, including the one whose VALUE is empty.  That is
the shape a caller has to split on rather than tokenise: a tab is an IFS
whitespace character, so `IFS=$'\t' read -r scope value dir` folds the two
adjacent tabs of an unset key into one and silently reads the directory as the
value.  `lib/config.sh`'s `wb_config_split_record` is the bash side done
correctly, and is what a bash caller should use.

Each DIR is a repo's work-tree root, and each is resolved on its own: a
directory that is gone, holds no config, or holds one nothing can parse
resolves to the built-in default rather than failing the batch.  A report over
other people's repos cannot stop because one of them has a bad file; `config
status`, run in that repo, is what names it.  With no DIR the caller's own
work-tree root is used, which is what `wb_config_get` asks for.

`status` answers the two questions the files themselves cannot: what is the
value right now, and which file supplied it.  The loader deep-merges every
scope and returns the result, so a value inherited from the machine-wide file
and a value set by the repo in front of you look identical afterwards — and a
key written under a name nothing reads looks exactly like a key nobody ever
set, in both directions, for as long as it takes somebody to notice the rule
it was meant to turn on is not applying.

`set` writes one of the three scopes `status` reports: the machine-wide file by
default, `--project` for the checkout's committed `.workbench.yml`, and
`--container` for the file beside a bare repo's worktrees, which every one of
them reads and `wt remove` cannot delete.  Every scope the report can show has
a flag that writes it — a scope a reader can see and not set is a diagnosis
with no fix at the end of it.

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

Exit codes: 0 on a completed write, a report that read every scope, or a `get`
that answered for every DIR; 1 on a refused key, a failed write, or a scope
`status` could not read; 2 on a usage error (argparse's own).

`get` refuses a key this checkout does not define and answers for every other
one, including a DIR whose files it could not read — the two halves of the same
rule.  A key nothing reads is a caller asking a question with no answer, and a
fallback there hides a typo forever; an unreadable file already resolves to the
built-in default everywhere else in the workbench, so reporting that is the
truth rather than a degrade.  Unlike `set`, only this checkout's key surface is
consulted: see `workbench_config.defines_key`.

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
    scope = setter.add_mutually_exclusive_group()
    scope.add_argument(
        "--project", action="store_true",
        help="write the current repo's .workbench.yml instead of the global file",
    )
    scope.add_argument(
        "--container", action="store_true",
        help="write the .workbench.yml above a bare repo's worktrees",
    )
    setter.set_defaults(run=_write)
    reporter = sub.add_parser(
        "status", help="show every scope, every value, and the file it came from",
    )
    reporter.set_defaults(run=_status)
    reader = sub.add_parser(
        "get", help="resolve one dotted key, for this repo or for each named one",
    )
    reader.add_argument("key", help="dotted key, e.g. issue_tracker.provider")
    reader.add_argument(
        "dir", nargs="*", metavar="DIR",
        help="a repo's work-tree root; defaults to the caller's own",
    )
    reader.set_defaults(run=_get)
    return parser.parse_args(argv)


# ``render_value`` writes "nothing is set" as an em dash, which is a reading for
# a person looking at a table. A record another program parses says it with an
# empty value field, so the only marker in the output is the caller's own — the
# machine profile's "unset", a bash caller's fallback.
_NO_VALUE = workbench_config.render_value(None)


def _one_line(value: str) -> str:
    """One value as a field of a one-line, tab-separated record.

    Nothing the config surface types can hold a newline or a tab today, so the
    collapse is what keeps that from being load-bearing: a key that grows into
    a free-text string later cannot split the row a caller is reading.
    """
    return " ".join(value.split())


def _resolve(key: str, project_root) -> tuple[str, str]:
    """The scope that answered for ``key`` at ``project_root``, and its value.

    ``config_status`` already collects a file it could not read rather than
    raising, and returns no rows at all when the merged config will not type.
    Both leave the built-in default standing, which is what every other reader
    on the machine gets from ``load_config_or_default`` — so both report
    ``DEFAULT_SCOPE`` here rather than an error the batch would have to carry.
    """
    try:
        status = workbench_config.config_status(project_root)
    except (workbench_config.ConfigError, OSError):
        return workbench_config.DEFAULT_SCOPE, ""
    row = next((entry for entry in status.keys if entry.key == key), None)
    if row is None:
        return workbench_config.DEFAULT_SCOPE, ""
    value = _one_line(row.value)
    if value == _NO_VALUE:
        value = ""
    if row.is_default:
        return workbench_config.DEFAULT_SCOPE, value
    return row.scope.name, value


def _get(args: argparse.Namespace) -> int:
    """Print one record per DIR: the scope that answered, the value, the DIR."""
    if not workbench_config.defines_key(args.key):
        raise workbench_config.ConfigKeyError(
            f"{args.key} is not a key WorkbenchConfig defines",
        )
    for target in args.dir or [_project_root()]:
        scope, value = _resolve(args.key, target)
        print(f"{scope}\t{value}\t{target if target is not None else ''}")
    return 0


def _status(_args: argparse.Namespace) -> int:
    """Print every scope, every resolved value, and the file each came from.

    All of it on stdout, including the problems. The exit code is what a script
    reads, and splitting the report across two streams only guarantees that the
    line explaining why a section is missing lands somewhere other than where
    the section would have been.
    """
    status = workbench_config.config_status(_project_root())

    print(f"{BOLD}Scopes{NC} {DIM}— highest precedence first{NC}")
    name_width = max(len(scope.name) for scope in status.scopes)
    for scope in status.scopes:
        mark = f"{GREEN}✓{NC}" if scope.exists else f"{DIM}·{NC}"
        note = "" if scope.exists else f"  {DIM}(no file){NC}"
        print(f"  {mark} {scope.name:<{name_width}} {scope.path}{note}")

    for problem in status.problems:
        print(f"  {RED}✗{NC} {problem}")

    if status.keys:
        key_width = max(len(row.key) for row in status.keys)
        value_width = max(len(row.value) for row in status.keys)
        print(f"\n{BOLD}Values{NC}")
        for row in status.keys:
            source = (workbench_config.DEFAULT_SCOPE if row.is_default
                      else row.scope.name)
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
    if not (args.project or args.container):
        workbench_config.set_value(args.key, args.value)
        return _wrote(args, workbench_config.global_config_path(), "every repo")

    root = _project_root()
    if root is None:
        flag = "--container" if args.container else "--project"
        print(f"{RED}✗{NC} {flag} needs a git repo, and this is not one", file=sys.stderr)
        return 1
    if args.container:
        return _write_container(args, root)
    workbench_config.set_project_value(args.key, args.value, root)
    return _wrote(args, workbench_config.project_config_path(root),
                  "commit it so the repo keeps the answer")


def _write_container(args: argparse.Namespace, root: Path) -> int:
    """Write the file above a bare repo's worktrees, or say there is none.

    The path is resolved here rather than left to ``set_container_value``'s own
    refusal so the message can name the flag the caller passed. The write still
    goes through that function, which is where the scope's rules live.
    """
    target = workbench_config.container_config_path(root)
    if target is None:
        print(f"{RED}✗{NC} --container needs a bare-repo worktree — {root} is a "
              f"plain checkout, whose repo has no container", file=sys.stderr)
        return 1
    workbench_config.set_container_value(args.key, args.value, root)
    return _wrote(args, target, "every worktree of this repo")


def _wrote(args: argparse.Namespace, target: Path, reach: str) -> int:
    print(f"{GREEN}✓{NC} {args.key} = {args.value}")
    print(f"  {DIM}{target} — {reach}{NC}")
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
