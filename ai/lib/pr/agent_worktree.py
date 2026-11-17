"""Which worktree, other than its own, an agent's tool call worked in.

`ToolCall.from_payload(obj, env)` reads one post-tool hook payload — Claude's
shape, which the Pi extension writes too. Home is `$CLAUDE_PROJECT_DIR`, else
the payload `cwd`; `subagent` is true when the payload carries `agent_id`.
`candidates(call)` is pure: the paths and branches the call names, in command
order. `resolve(path, cwd)` maps a path to the worktree holding it, spelled the
way `git worktree list` spells it, or None. `target_worktree(call)` is the last
candidate that resolves to a worktree other than home's, or None. `LABEL` is the
herdr label key the answer is published under.

A candidate is one of: `git -C <p>`; `--repo-dir <p>` / `--repo-dir=<p>` and
`--worktree <p>` / `=` on any command; `cd <p>`; `wt switch [-c] <branch|path>`;
an Edit/Write `file_path`; and the payload `cwd` when it differs from home,
because Claude keeps a `cd` across calls. Relative paths resolve against the
payload `cwd`. The command is split by `gh.shell_lex`, so quoted text and
heredoc bodies are content, never candidates; a command with an unbalanced
quote yields none.

For `wt switch`, the `path` in `--format json` stdout wins when present (the
only way to place `pr:N`); then `--path <p>`; then the branch argument, which
is looked up with `git.topology.find_worktree_for_branch` after the command
ran, from `wt -C <p>` when given. worktrunk shortcuts (`^`, `-`, `@`, `pr:N`,
`mr:N`, a URL) name no branch and yield nothing without that stdout.

Resolution takes the nearest existing directory (a file an Edit is about to
create has none yet), asks `git rev-parse --show-toplevel`, and matches the
toplevel against `worktree_entries` by realpath, so a symlinked spelling of a
worktree resolves to the one the board document uses. A path outside any
checkout, or in a bare container, resolves to None.

`report(call, env)` claims the target for the session `session_of(env)` names
(`core.session_lock.claim_reached`), independent of the terminal, and publishes
the target on the agent's own herdr terminal, returning a `ReportOutcome`. The
claim is skipped when the call is a subagent's or there is no target; the label
is SKIPPED when `HC_TERMINAL_ID` is unset or empty, when the call is a
subagent's, or when there is no target. A state file per (`HC_TERMINAL_SOCKET`,
`HC_SCOPE`, terminal) under the workbench state dir debounces it: an unchanged
target is UNCHANGED and runs nothing. A changed one runs `hc label <terminal>
otto.worktree=<path>` and is LABELLED, and the state is written only when hc
exits 0, so a FAILED label is retried on the next call. `clear(env)` drops the
reached claim for that session, empties the label and removes the state file.
Neither raises for a missing hc, a timeout or an unwritable state dir. The
label is sticky by design: returning to the home checkout leaves it, so
read-only peeks do not flap it.
"""

# doc-group: pr-state

from __future__ import annotations

import enum
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import core.timeouts
import core.serde
import core.session_lock
import core.workbench_paths
import gh.shell_lex
import git.topology

LABEL = "otto.worktree"

# Options whose value names a checkout, on whatever command carries them.
_DIR_FLAGS = frozenset({"--repo-dir", "--worktree"})
# git's global options that take a separate value, so `-C` is found only
# before the subcommand.
_GIT_VALUE_OPTIONS = frozenset({"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
# `wt switch --help`: every option that takes a value, keyed to one spelling.
_WT_VALUE_FLAGS = {
    "-C": "-C",
    "--config": "--config",
    "--config-set": "--config-set",
    "-b": "--base",
    "--base": "--base",
    "--path": "--path",
    "-x": "--execute",
    "--execute": "--execute",
    "--format": "--format",
}
_WT_SHORTCUTS = frozenset({"^", "-", "@"})
_WT_REF_PREFIXES = ("pr:", "mr:", "http://", "https://")
_PATH_PREFIXES = ("/", ".", "~")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class CandidateKind(enum.Enum):
    """Whether a candidate is a filesystem path or a branch to look up."""

    PATH = "path"
    BRANCH = "branch"


@dataclass(frozen=True)
class Candidate:
    """One place a call names: an absolute path, or a branch looked up from `base`."""

    kind: CandidateKind
    value: str
    base: str = ""


@dataclass(frozen=True)
class ToolCall:
    """The parts of a post-tool payload the worktree decision reads."""

    tool_name: str
    command: str
    file_path: str
    stdout: str
    cwd: Path
    home: Path
    subagent: bool

    @classmethod
    def from_payload(cls, obj: object, env: Mapping[str, str]) -> ToolCall:
        """Read *obj* (a decoded hook payload); *env* supplies `CLAUDE_PROJECT_DIR`.

        A missing or mistyped field reads as empty rather than raising: the
        payload is another program's output and the hook must never fail on it.
        """
        payload = obj if isinstance(obj, Mapping) else {}
        tool_input = _mapping(payload.get("tool_input"))
        project = env.get("CLAUDE_PROJECT_DIR", "")
        cwd = _text(payload.get("cwd")) or project or os.getcwd()
        return cls(
            tool_name=_text(payload.get("tool_name")),
            command=_text(tool_input.get("command")),
            file_path=_text(tool_input.get("file_path")) or _text(tool_input.get("notebook_path")),
            stdout=_stdout(payload.get("tool_response")),
            cwd=Path(cwd),
            home=Path(project or cwd),
            subagent="agent_id" in payload,
        )


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _stdout(response: object) -> str:
    if isinstance(response, Mapping):
        return _text(response.get("stdout"))
    return _text(response)


def _abs(value: str, base: str) -> str:
    """*value* as a normalised absolute path, relative ones taken from *base*."""
    return os.path.normpath(os.path.join(base, os.path.expanduser(value)))


def _path(value: str, base: str) -> Candidate:
    return Candidate(CandidateKind.PATH, _abs(value, base))


def _flag_values(words: Sequence[str], names: frozenset[str]) -> list[str]:
    """Values of `--name v` and `--name=v` for every flag in *names*."""
    values = []
    for i, word in enumerate(words):
        name, eq, value = word.partition("=")
        if name not in names:
            continue
        values.append(value if eq else (words[i + 1] if i + 1 < len(words) else ""))
    return [value for value in values if value]


def _git_candidates(args: Sequence[str], cwd: str) -> list[Candidate]:
    """`git -C` before the subcommand; repeated `-C` compose as git composes them."""
    where = None
    words = iter(args)
    for word in words:
        if not word.startswith("-"):
            break
        if word == "-C":
            where = _abs(next(words, ""), where or cwd)
        elif word in _GIT_VALUE_OPTIONS:
            next(words, None)
    return [Candidate(CandidateKind.PATH, where)] if where else []


def _cd_candidates(args: Sequence[str], cwd: str) -> list[Candidate]:
    """`cd <p>`; `cd`, `cd -` and a flag-only `cd` name no worktree."""
    targets = [word for word in args if not word.startswith("-")]
    return [_path(targets[0], cwd)] if targets else []


@dataclass(frozen=True)
class _WtArgs:
    """A `wt` invocation's valued flags, by long spelling, and its positionals."""

    flags: dict[str, str]
    positional: list[str]


def _wt_parse(args: Sequence[str]) -> _WtArgs:
    flags: dict[str, str] = {}
    positional: list[str] = []
    words = iter(args)
    for word in words:
        if word == "--":
            break
        name, eq, value = word.partition("=")
        key = _WT_VALUE_FLAGS.get(name)
        if key is not None:
            flags[key] = value if eq else next(words, "")
        elif not word.startswith("-"):
            positional.append(word)
    return _WtArgs(flags, positional)


def _wt_candidates(args: Sequence[str], call: ToolCall) -> list[Candidate]:
    """`wt switch`: stdout `path`, then `--path`, then the branch argument."""
    parsed = _wt_parse(args)
    flags, positional = parsed.flags, parsed.positional
    if positional[:1] != ["switch"]:
        return []
    base = _abs(flags["-C"], str(call.cwd)) if flags.get("-C") else str(call.cwd)
    reported = git.topology.parse_wt_switch_path(call.stdout) if flags.get("--format") == "json" else None
    if reported:
        return [_path(reported, base)]
    if flags.get("--path"):
        return [_path(flags["--path"], base)]
    branch = positional[1] if len(positional) > 1 else ""
    if not branch or branch in _WT_SHORTCUTS or branch.startswith(_WT_REF_PREFIXES):
        return []
    if branch.startswith(_PATH_PREFIXES):
        return [_path(branch, base)]
    return [Candidate(CandidateKind.BRANCH, branch, base)]


def _statement_candidates(words: Sequence[str], call: ToolCall) -> list[Candidate]:
    cwd = str(call.cwd)
    command = list(words)
    while command and _ASSIGNMENT.match(command[0]):
        command.pop(0)
    if not command:
        return []
    name, args = command[0], command[1:]
    found: list[Candidate] = []
    if name == "git":
        found += _git_candidates(args, cwd)
    elif name == "cd":
        found += _cd_candidates(args, cwd)
    elif name == "wt":
        found += _wt_candidates(args, call)
    return found + [_path(value, cwd) for value in _flag_values(args, _DIR_FLAGS)]


def _command_candidates(call: ToolCall) -> list[Candidate]:
    try:
        statements = gh.shell_lex.split_statements(call.command)
    except ValueError:
        return []
    return [found for words in statements for found in _statement_candidates(words, call)]


def candidates(call: ToolCall) -> list[Candidate]:
    """Every place *call* names, in command order. Pure: touches no filesystem.

    The payload `cwd` comes first when it differs from home: it is where the
    agent already stood, and anything the command names is later than that.
    """
    found: list[Candidate] = []
    if call.cwd != call.home:
        found.append(Candidate(CandidateKind.PATH, _abs(str(call.cwd), str(call.home))))
    if call.command:
        found += _command_candidates(call)
    if call.file_path:
        found.append(_path(call.file_path, str(call.cwd)))
    return found


def _nearest_dir(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if directory.is_dir():
            return directory
    return None


def _toplevel(directory: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=core.timeouts.LOCAL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = result.stdout.strip()
    return top if result.returncode == 0 and top else None


def resolve(path: str | Path, cwd: str | Path) -> Path | None:
    """The worktree holding *path* (relative to *cwd*), in `git worktree list` spelling.

    None when no existing ancestor is inside a checkout, or when git lists no
    worktree at that toplevel.
    """
    directory = _nearest_dir(Path(_abs(str(path), str(cwd))))
    top = _toplevel(directory) if directory is not None else None
    if top is None:
        return None
    real = os.path.realpath(top)
    for entry in git.topology.worktree_entries(top):
        if os.path.realpath(entry.path) == real:
            return entry.path
    return None


def _resolve_candidate(candidate: Candidate) -> Path | None:
    if candidate.kind is CandidateKind.PATH:
        return resolve(candidate.value, candidate.value)
    found = git.topology.find_worktree_for_branch(candidate.value, candidate.base)
    return resolve(found, candidate.base) if found is not None else None


def target_worktree(call: ToolCall) -> Path | None:
    """The last candidate in command order that resolves to a worktree other than home's."""
    home = resolve(call.home, call.home)
    for candidate in reversed(candidates(call)):
        found = _resolve_candidate(candidate)
        if found is not None and found != home:
            return found
    return None


class ReportOutcome(enum.Enum):
    """What `report` did with one tool call."""

    UNCHANGED = "unchanged"
    LABELLED = "labelled"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class Session:
    """The harness session a hook speaks for: whose reached claim to move."""

    pid: int
    harness: str
    session_id: str


def session_of(env: Mapping[str, str]) -> Session | None:
    """The session to claim for, or None.

    Pi's extension sets ``WORKBENCH_SESSION_HARNESS=pi`` always and the pid only
    when the session claims trees at all; that marker stops a Pi hook from
    falling through to a ``CLAUDE_PID`` it inherited. Claude injects
    ``CLAUDE_PID`` into every hook.
    """
    if env.get("WORKBENCH_SESSION_HARNESS") == "pi":
        pid = env.get("WORKBENCH_SESSION_PID", "")
        return Session(int(pid), "pi", env.get("PI_SESSION_ID", "")) if pid.isdigit() else None
    pid = env.get("CLAUDE_PID", "")
    if pid.isdigit():
        return Session(int(pid), "claude", env.get("CLAUDE_CODE_SESSION_ID", ""))
    return None


def _terminal(env: Mapping[str, str]) -> str:
    return env.get("HC_TERMINAL_ID", "")


def _state_path(env: Mapping[str, str]) -> Path:
    """The debounce file for this (socket, scope, terminal)."""
    key = "\0".join((env.get("HC_TERMINAL_SOCKET", ""), env.get("HC_SCOPE", ""), _terminal(env)))
    name = hashlib.sha256(key.encode()).hexdigest()[:16]
    return core.workbench_paths.state_dir() / "agent-worktree" / f"{name}.json"


def _labelled(state: Path, terminal: str) -> str | None:
    """The worktree the state file says this terminal is labelled with, or None."""
    try:
        data = json.loads(state.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("terminal") != terminal or data.get("error"):
        return None
    worktree = data.get("worktree")
    return worktree if isinstance(worktree, str) else None


def _label(terminal: str, value: str, run) -> bool:
    """Run `hc label <terminal> otto.worktree=<value>`; True when hc exited 0."""
    try:
        result = run(
            ["hc", "label", terminal, f"{LABEL}={value}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=core.timeouts.LOCAL, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def report(call: ToolCall, env: Mapping[str, str], *, run=subprocess.run) -> ReportOutcome:
    """Claim *call*'s target worktree for this session, and label this terminal with it."""
    if call.subagent:
        return ReportOutcome.SKIPPED
    target = target_worktree(call)
    if target is None:
        return ReportOutcome.SKIPPED
    session = session_of(env)
    if session is not None:
        command = call.command or call.file_path or session.harness
        if not core.session_lock.claim_reached(
            target, pid=session.pid, harness=session.harness,
            session_id=session.session_id, command=command,
        ):
            print(f"agent-worktree: could not record session lock for {target}", file=sys.stderr)
    return _publish(target, env, run)


def _publish(target: Path, env: Mapping[str, str], run) -> ReportOutcome:
    terminal = _terminal(env)
    if not terminal:
        return ReportOutcome.SKIPPED
    state = _state_path(env)
    if _labelled(state, terminal) == str(target):
        return ReportOutcome.UNCHANGED
    if not _label(terminal, str(target), run):
        return ReportOutcome.FAILED
    record = {
        "worktree": str(target),
        "terminal": terminal,
        "at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    try:
        core.serde.write_json(state, record)
    except OSError:
        return ReportOutcome.FAILED
    return ReportOutcome.LABELLED


def clear(env: Mapping[str, str], *, run=subprocess.run) -> None:
    """Drop this session's reached claim, empty this terminal's label, forget the debounce state."""
    session = session_of(env)
    if session is not None:
        core.session_lock.drop_reached(session.pid)
    terminal = _terminal(env)
    if not terminal:
        return
    _label(terminal, "", run)
    try:
        _state_path(env).unlink(missing_ok=True)
    except OSError:
        return
