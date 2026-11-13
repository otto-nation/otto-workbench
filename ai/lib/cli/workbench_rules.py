"""`workbench-rules`: manage this machine's coding-rule layers.

Argument parsing, output and exit codes. Writes live in `core.workbench_rules`.
Flags, exit codes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import core.cli_reference
import core.command_contract
import core.project_context
import core.timeouts
import core.tool_parser
import core.version
import core.workbench_rules
from core.exit_codes import ExitCode

SCRIPT = "workbench-rules"

CONTRACT = core.command_contract.CommandContract(
    summary="Manage this machine's coding-rule layers, for whichever harnesses "
            "are installed.",
    groups=(
        core.command_contract.FlagGroup("global", ("-V",)),
        core.command_contract.FlagGroup("sync", ()),
        core.command_contract.FlagGroup("add", ("domain", "rule")),
        core.command_contract.FlagGroup("list", ()),
        core.command_contract.FlagGroup("status", ()),
        core.command_contract.FlagGroup("open", ()),
        core.command_contract.FlagGroup("project add", ()),
        core.command_contract.FlagGroup("project show", ()),
    ),
    remote_effects=(),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "sync wrote workbench.md; add/list/status/open/project ran"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "the rules tree is missing, AGENTS.md is missing, the cwd is not a "
            "repo, no worktree resolved, the instructions resolver failed, or "
            "$EDITOR could not be started"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "no command, unknown command, missing args to add or project add, "
            "or unknown project subcommand"),
    ),
    examples=(
        core.command_contract.Example(
            "workbench-rules sync", "regenerate workbench.md in the state root"),
        core.command_contract.Example(
            "workbench-rules add go use-errors.As",
            "append a rule to go.local.md under the config root"),
        core.command_contract.Example(
            "workbench-rules list", "list local rule files with line counts"),
        core.command_contract.Example(
            "workbench-rules status", "show untracked local rule bodies"),
        core.command_contract.Example(
            "workbench-rules open go", "open go.local.md in $EDITOR"),
        core.command_contract.Example(
            "workbench-rules project add prefer-AGENTS.md",
            "append a convention to the current repo's AGENTS.md"),
        core.command_contract.Example(
            "workbench-rules project show", "print the current repo's AGENTS.md"),
    ),
    notes=(
        "Writes nothing remotely.",
        "add writes <domain>.local.md under the config root "
        "(WORKBENCH_CONFIG_DIR/overrides/ai/guidelines/rules, default "
        "~/.config/workbench/overrides/ai/guidelines/rules).",
        "sync writes workbench.md under the state root "
        "(WORKBENCH_STATE_DIR/rules, default ~/.local/state/workbench/rules).",
        "project add appends to the current repo's AGENTS.md.",
        "Any domain name is accepted (e.g. general, go). Aliases: ts/js → "
        "typescript, py → python, sh/shell → bash, yml → yaml.",
    ),
    reference="docs/ai-automation.md#workbench-rules-contract",
    # MCP's flag-only mapping cannot express positionals or nested
    # subcommands:
    #   add — domain and rule are positionals (`--domain`/`--rule` unrecognized)
    #   open — domain is a positional (`--domain` unrecognized); launches $EDITOR
    #   project — nested add|show, not a flag
    mcp_exclude=("add", "open", "project"),
)


def _ansi() -> dict[str, str]:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        empty = ""
        return {"green": empty, "blue": empty, "yellow": empty, "red": empty,
                "cyan": empty, "dim": empty, "nc": empty}
    return {
        "green": "\033[0;32m",
        "blue": "\033[0;34m",
        "yellow": "\033[1;33m",
        "red": "\033[0;31m",
        "cyan": "\033[0;36m",
        "dim": "\033[2m",
        "nc": "\033[0m",
    }


def _paint(color: str, text: str) -> str:
    table = _ansi()
    return f"{table[color]}{text}{table['nc']}"


def _info(message: str) -> None:
    print(f"{_paint('blue', '→')} {message}")


def _success(message: str) -> None:
    print(f"{_paint('green', '✓')} {message}")


def _err(message: str) -> None:
    print(f"{_paint('red', '✗')} {message}", file=sys.stderr)


def _dim(message: str) -> str:
    return _paint("dim", message)


def _cyan(message: str) -> str:
    return _paint("cyan", message)


def _syncing() -> bool:
    return os.environ.get("WORKBENCH_SYNC") == "true"


def _append_install_log(kind: str, message: str) -> None:
    log = os.environ.get("WORKBENCH_INSTALL_LOG")
    if not log:
        return
    component = os.environ.get("WORKBENCH_CURRENT_COMPONENT", "")
    prefix = f"[{component}] " if component else ""
    with open(log, "a") as handle:
        handle.write(f"{kind}:{prefix}{message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SCRIPT)
    parser.add_argument("-V", "--version", action="store_true", help="Show version")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("sync", help="Regenerate the machine's generated rule layer")
    add_p = sub.add_parser(
        "add", help="Append a rule to the override layer's <domain>.local.md")
    add_p.add_argument("domain", nargs="?", default="", help="Rule domain")
    add_p.add_argument("rule", nargs="?", default="", help="Rule text")
    sub.add_parser("list", help="List all local rule files with line counts")
    sub.add_parser("status", help="Show local rules not tracked in workbench")
    open_p = sub.add_parser("open", help="Open a local rule file in $EDITOR")
    open_p.add_argument("domain", nargs="?", default="", help="Domain to open")
    project_p = sub.add_parser("project", help="Manage the current repo's AGENTS.md")
    project_sub = project_p.add_subparsers(dest="project_command")
    add_proj = project_sub.add_parser(
        "add", help="Append a convention to the current repo's AGENTS.md")
    add_proj.add_argument("rule", nargs="?", default="", help="Rule text")
    project_sub.add_parser("show", help="Display the current repo's AGENTS.md")
    core.command_contract.attach_help(parser, CONTRACT)
    # Subcommand flags live on the subparsers; regrouping would leave empty
    # titled sections on the top-level help.
    parser._action_groups[:] = [g for g in parser._action_groups if g._group_actions]
    return parser


def reference_shape() -> core.cli_reference.CLIShape:
    """Everything workbench-rules accepts, with CONTRACT on the CLI as a whole."""
    shape = core.cli_reference.shape_of(build_parser(), SCRIPT)
    return core.cli_reference.CLIShape(
        prog=shape.prog, globals=shape.globals, commands=shape.commands,
        contract=CONTRACT,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(core.version.version_string(SCRIPT))
        return ExitCode.OK
    if args.command is None:
        parser.print_help()
        return ExitCode.USAGE
    if args.command == "sync":
        return cmd_sync()
    if args.command == "add":
        return cmd_add(args.domain, args.rule)
    if args.command == "list":
        return cmd_list()
    if args.command == "status":
        return cmd_status()
    if args.command == "open":
        return cmd_open(args.domain)
    if args.project_command is None:
        core.tool_parser.subparsers(parser)["project"].print_help()
        return ExitCode.USAGE
    if args.project_command == "add":
        return cmd_project_add(args.rule)
    return cmd_project_show()


def cmd_sync() -> int:
    source = core.workbench_rules.guidelines_rules_src_dir()
    if not source.is_dir():
        _err(f"Rules not found at {source}")
        return ExitCode.FAILURE
    try:
        core.workbench_rules.user_rules_dir().mkdir(parents=True, exist_ok=True)
        if not _syncing():
            _info(f"Generating workbench.md → {core.workbench_rules.generated_rules_dir()}")
        core.workbench_rules.install_workbench_rule()
    except OSError as exc:
        _err(f"Could not write workbench rules: {exc}")
        return ExitCode.FAILURE
    if not _syncing():
        _success("workbench.md")
    _check_local_rules()
    return ExitCode.OK


def cmd_add(domain: str, rule: str) -> int:
    if not domain or not rule:
        _err(f'Usage: {SCRIPT} add <domain> "rule text"')
        print("Any domain name is accepted (e.g. general, go, linear, workflow).",
              file=sys.stderr)
        print("Common: general, kotlin, java, go, typescript, python, bash, docker, yaml, sql",
              file=sys.stderr)
        return ExitCode.USAGE
    path = core.workbench_rules.add_local_rule(domain, rule)
    _success(f"Added to {path.name}")
    print(f"  {_dim(str(path))}")
    return ExitCode.OK


def cmd_list() -> int:
    directory = core.workbench_rules.user_rules_dir()
    if not directory.is_dir():
        print(f"  {_dim(f'No rules directory found at {directory}')}")
        return ExitCode.OK
    rules = core.workbench_rules.list_local_rules()
    if not rules:
        print(f"  {_dim(f'No local rule files in {directory}')}")
        return ExitCode.OK
    for rule in rules:
        print(f"  {_cyan(rule.name)}  {_dim(f'{rule.line_count} lines')}")
    return ExitCode.OK


def cmd_status() -> int:
    print()
    _info("Local rule additions (not tracked in workbench)")
    directory = core.workbench_rules.user_rules_dir()
    if not directory.is_dir():
        print(f"\n  {_dim(f'No rules directory found at {directory}')}")
        return ExitCode.OK
    rules = core.workbench_rules.list_local_rules()
    if not rules:
        print(f"\n  {_dim('No untracked local rules')}")
        return ExitCode.OK
    for rule in rules:
        print()
        print(f"  {_cyan(rule.name)}  {_dim(f'{rule.line_count} lines')}")
        for line in core.workbench_rules.body_lines(rule.path):
            print(f"  {_dim(f'  {line}')}")
    print()
    source = core.workbench_rules.guidelines_rules_src_dir()
    print(f"  {_dim(f'To formalize: edit {source}/<domain>.md')}")
    return ExitCode.OK


def cmd_open(domain: str) -> int:
    directory = core.workbench_rules.user_rules_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if domain:
        path = directory / f"{domain}{core.workbench_rules.LOCAL_SUFFIX}"
        core.workbench_rules.write_frontmatter(path, domain)
        target = path
    else:
        target = directory
    editor = os.environ.get("EDITOR") or "nano"
    cmd = [*shlex.split(editor), str(target)]
    try:
        completed = subprocess.run(cmd, timeout=core.timeouts.UNBOUNDED)
    except OSError:
        _err(f"cannot run {cmd[0]}")
        return ExitCode.FAILURE
    return completed.returncode


def cmd_project_add(rule: str) -> int:
    if not rule:
        _err(f'Usage: {SCRIPT} project add "rule text"')
        return ExitCode.USAGE
    root = _require_repo_root()
    if isinstance(root, int):
        return root
    loaded = _load_context(root)
    if loaded.failed:
        _err(f"Could not determine the instructions file for {root} — is the repo readable?")
        return ExitCode.FAILURE
    ctx = loaded.ctx
    if ctx is None or not ctx.found:
        return _missing_agents(root, ctx)
    path = root / ctx.path
    core.workbench_rules.append_project_rule(path, rule)
    _success(f"Added to {path}")
    return ExitCode.OK


def _missing_agents(root: Path, ctx: core.project_context.ProjectContext | None) -> int:
    _err(f"No AGENTS.md found at {root}")
    print("  Create one first (otto-workbench ai init), then use this command to append rules.",
          file=sys.stderr)
    if ctx is None:
        return ExitCode.FAILURE
    hint = core.project_context.migration_hint(ctx)
    if hint:
        print(hint, file=sys.stderr)
    return ExitCode.FAILURE


def cmd_project_show() -> int:
    root = _require_repo_root()
    if isinstance(root, int):
        return root
    loaded = _load_context(root)
    if loaded.failed:
        _err(f"Could not determine the instructions file for {root} — is the repo readable?")
        return ExitCode.FAILURE
    ctx = loaded.ctx
    if ctx is None or not ctx.found:
        print(f"  {_dim(f'No AGENTS.md at {root}')}")
        return ExitCode.OK
    sys.stdout.write((root / ctx.path).read_text())
    return ExitCode.OK


def _require_repo_root() -> Path | int:
    found = core.workbench_rules.resolve_repo_root()
    if found.ok and found.path is not None:
        return found.path
    if found.error is core.workbench_rules.RepoRootError.NOT_GIT:
        _err("Not inside a git repository")
        return ExitCode.FAILURE
    _err(f"No worktree resolved for {os.getcwd()} — its AGENTS.md is not reachable from here")
    return ExitCode.FAILURE


@dataclass(frozen=True)
class _ContextLoad:
    ctx: core.project_context.ProjectContext | None
    failed: bool


def _load_context(root: Path) -> _ContextLoad:
    try:
        return _ContextLoad(core.project_context.resolve(root), failed=False)
    except Exception:
        return _ContextLoad(None, failed=True)


def _check_local_rules() -> None:
    rules = core.workbench_rules.list_local_rules()
    if not rules:
        return
    details = ", ".join(f"{rule.name} ({rule.line_count} lines)" for rule in rules)
    announced = False
    for rule in rules:
        if not announced and not _syncing():
            print()
            print(f"{_paint('yellow', '⚠')}  Local rule additions not tracked in workbench:")
            announced = True
        if not _syncing():
            print(f"  {_dim(f'  • {rule.name} ({rule.line_count} lines)')}")
    _append_install_log(
        "WARN", f"Local rule additions not tracked in workbench: {details}")
    if not _syncing():
        source = core.workbench_rules.guidelines_rules_src_dir()
        hint = (
            f"  Run 'workbench-rules status' to review, "
            f"or edit {source}/ to formalize"
        )
        print(f"  {_dim(hint)}")
