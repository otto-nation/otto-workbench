"""Cheap syntax checks for text that has just been merged.

`pr rebase --fix` used to treat a marker-valid resolution as success, so a Go
file that ended with an extra `}` reported the rebase complete and printed a
force-push command. The pre-push hook was the first thing that objected, after
the user had already been handed that command.

This module answers one question: does this text still parse as the language
its path claims? It is not a formatter, a linter, or a build. `gofmt`
whitespace differences do not fail. Unknown suffixes pass. The checkers that
exist are the cheap ones — `gofmt -e` on stdin, `ast.parse`, `bash -n`,
`json.loads`, `yaml.safe_load_all`, `tomllib.loads` — and anything they cannot
run is a pass, because a rebase blocked on a missing parser is worse than one
that ships a file the next hook will catch.
"""

# doc-group: platform

from __future__ import annotations

import ast
import json
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import core.proc
import core.timeouts

_SHELL = frozenset({".sh", ".bash"})
_YAML = frozenset({".yml", ".yaml"})
_SH_NAMES = frozenset({"bash", "sh"})


@dataclass(frozen=True)
class SyntaxResult:
    """Whether *text* parses as the language *path* claims."""

    ok: bool
    detail: str = ""


def check(path: str, text: str) -> SyntaxResult:
    """Parse *text* as the language *path* names, without writing the file.

    Fail-open: an unknown suffix, a missing checker binary, a timed-out
    checker, and an import the machine does not have all return `ok`. A
    checker that ran and rejected the text is the only failure.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".go":
        return _tool(["gofmt", "-e"], text)
    if suffix == ".py":
        return _python(text)
    if suffix in _SHELL:
        # A shebang naming another interpreter outranks the suffix: `bash -n`
        # would reject a valid zsh or dash script.
        if _foreign_shebang(text):
            return SyntaxResult(True)
        return _tool(["bash", "-n"], text)
    if suffix == ".json":
        return _json(text)
    if suffix in _YAML:
        return _yaml(text)
    if suffix == ".toml":
        return _toml(text)
    if _bash_shebang(text):
        return _tool(["bash", "-n"], text)
    # ceiling-permanent: unknown suffixes pass. Closing this would need a
    # parser for every language a rebase might touch, and refusing a
    # resolution because this machine has no checker for it is worse — the
    # rebase would stop for a tool problem, not a merge problem.
    return SyntaxResult(True)


def _python(text: str) -> SyntaxResult:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return SyntaxResult(False, f"{exc.msg} (line {exc.lineno})")
    except (RecursionError, MemoryError):
        # Pathologically nested input exhausts the parser, not the file.
        return SyntaxResult(True)
    return SyntaxResult(True)


def _json(text: str) -> SyntaxResult:
    try:
        # json.loads rejects a leading BOM; editors and Windows tools write one.
        json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        return SyntaxResult(False, str(exc))
    except RecursionError:
        return SyntaxResult(True)
    return SyntaxResult(True)


def _yaml(text: str) -> SyntaxResult:
    try:
        import yaml
    except ImportError:
        return SyntaxResult(True)
    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        return SyntaxResult(False, str(exc))
    except RecursionError:
        return SyntaxResult(True)
    return SyntaxResult(True)


def _toml(text: str) -> SyntaxResult:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return SyntaxResult(False, str(exc))
    except RecursionError:
        return SyntaxResult(True)
    return SyntaxResult(True)


def _shebang_prog(text: str) -> str:
    """The interpreter the first line's shebang names, or "" when it has none."""
    first = text.lstrip("\ufeff").splitlines()[:1]
    if not first or not first[0].startswith("#!"):
        return ""
    parts = first[0][2:].strip().split()
    if not parts:
        return ""
    prog = Path(parts[0]).name
    if prog == "env" and len(parts) >= 2:
        return Path(parts[1]).name
    return prog


def _bash_shebang(text: str) -> bool:
    """True when the first line is a bash or sh shebang.

    `.sh` / `.bash` dispatch on suffix unless `_foreign_shebang` says
    otherwise; this covers extensionless scripts. `env bash` / `env sh` count;
    `zsh`, `dash`, and `bats` do not — `bash -n` would reject valid files in
    those languages.
    """
    return _shebang_prog(text) in _SH_NAMES


def _foreign_shebang(text: str) -> bool:
    """True when the first line is a shebang for something other than bash or sh."""
    prog = _shebang_prog(text)
    return bool(prog) and prog not in _SH_NAMES


def _tool(cmd: list[str], text: str) -> SyntaxResult:
    """Run *cmd* against *text* on stdin. Missing or silent tools pass."""
    binary = cmd[0]
    if shutil.which(binary) is None:
        return SyntaxResult(True)
    try:
        result = core.proc.run(
            cmd, timeout=core.timeouts.LOCAL, input_text=text,
        )
    except FileNotFoundError:
        return SyntaxResult(True)
    if result.returncode in (
        core.proc.TIMEOUT_RETURNCODE, core.proc.MISSING_RETURNCODE,
    ):
        return SyntaxResult(True)
    if result.ok:
        return SyntaxResult(True)
    detail = result.detail or result.stderr.strip() or f"{binary} rejected the text"
    return SyntaxResult(False, detail)
