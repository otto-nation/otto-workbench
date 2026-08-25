"""AI backend abstraction layer.

Dispatches preflight(), prompt(), invoke_agent(), and invoke_fix() to the
correct backend (Claude Code CLI or Pi CLI) based on AI_BACKEND env var.

Every entry point takes a required `cwd`, because a backend CLI inherits the
launching process's working directory unless it is told otherwise. An agent
given write access would then edit whichever worktree the session happened to
start in rather than the one being operated on. `add_dirs` is not a substitute —
it maps to `--add-dir`, which widens the set of directories the agent may touch
and has no way to narrow it. `prompt()` rejects the call at the signature,
`invoke_agent`/`invoke_fix` raise on an empty or non-existent `cwd`, and a test
fails the build on a new call site that omits it.

Every call made through here appends one record to the usage ledger, so what a
run cost is answerable without instrumenting the call site — see `ai_usage`.
"""

# doc-group: backend

from __future__ import annotations

import os
import shutil
import sys
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import ai_usage
from agent_types import AgentKind

ENV_AI_BACKEND = "AI_BACKEND"


class Backend(StrEnum):
    """Which CLI serves AI calls, selected by the AI_BACKEND env var."""

    CLAUDE = "claude"
    PI = "pi"


def _backend() -> Backend:
    """The selected backend; an unrecognised AI_BACKEND falls back to Claude."""
    try:
        return Backend(os.environ.get(ENV_AI_BACKEND, Backend.CLAUDE))
    except ValueError:
        return Backend.CLAUDE


def _script_name() -> str:
    return Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "unknown"


def _record(
    *, entry_point: str, usage: ai_usage.SessionUsage | None, exit_code: int,
    model: str | None, task: str | None, repo: str | None, pr: str | None,
) -> None:
    """Append one ledger record. A missing usage source records nothing —
    an absent measurement is more honest than a zeroed one."""
    if usage is None:
        return
    try:
        ai_usage.record(
            script=_script_name(), entry_point=entry_point, backend=_backend().value,
            model=model, usage=usage, exit_code=exit_code,
            task=task, repo=repo, pr=pr,
        )
    except Exception:  # noqa: BLE001 - telemetry must never break the measured call
        pass


def _usage_from_log(session_log: str) -> ai_usage.SessionUsage | None:
    """Read usage from a session log, or None when the log carries no result record.

    A log that exists but reports nothing — the agent died before its result record,
    or the backend does not emit one — is unmeasured, not free. Returning zeros here
    would put a $0.00 row in the ledger and understate what the pipeline costs.
    """
    if not session_log or not Path(session_log).is_file():
        return None
    usage = ai_usage.parse_session_log(session_log)
    return usage if usage != ai_usage.SessionUsage() else None


def _get_module() -> types.ModuleType:
    if _backend() is Backend.PI:
        import ai_backend_pi as mod
    else:
        import ai_backend_claude as mod
    return mod


def preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """Verify the backend can serve the requested models before any run.

    ``models`` maps each resolved model id to the phases requesting it.
    Returns False to abort — backends fail open when they cannot tell.
    """
    return _get_module().preflight(models, trail)


def _require_cwd(cwd: str, entry_point: str) -> None:
    """Reject a call that would let the backend CLI pick its own directory.

    Every AI subprocess inherits the interpreter's working directory unless it is
    told otherwise, and this repo is a bare repo with ~23 sibling worktrees — the
    inherited directory is reliably another live branch with uncommitted work that
    the agent can write to. `add_dirs` is not a substitute: it widens the allowed
    set and has no way to narrow it.
    """
    if not cwd:
        raise ValueError(f"ai_backend.{entry_point}() requires a non-empty cwd")
    if not Path(cwd).is_dir():
        raise ValueError(
            f"ai_backend.{entry_point}() cwd is not a directory: {cwd}"
        )


def prompt(
    text: str, *, cwd: str, model: str | None = None,
    task: str | None = None, repo: str | None = None, pr: str | None = None,
) -> tuple[str, int]:
    """Stateless text-in/text-out. Returns (response_text, exit_code).

    `cwd` is required — see _require_cwd for why it has no safe default.
    """
    _require_cwd(cwd, "prompt")
    result = _get_module().prompt(text, cwd=cwd, model=model)
    # Backends report (text, code, usage); tolerate the older pair so a backend that
    # has not adopted the triple degrades to unmeasured rather than crashing dispatch.
    if len(result) == 3:
        reply, code, usage = result
    else:
        (reply, code), usage = result, None
    _record(
        entry_point="prompt", usage=usage, exit_code=code,
        model=model, task=task, repo=repo, pr=pr,
    )
    return reply, code


@dataclass(frozen=True)
class AgentInvocation:
    """Everything a backend needs to run one agent.

    ``provider`` is honoured by the Pi backend and ignored by Claude Code,
    which has no --provider flag; ``thinking`` is likewise ignored there.
    Both stay on the object so callers do not branch on the backend.

    ``task``, ``repo``, and ``pr`` are not passed to the backend at all: they
    only label the usage ledger record for this call.

    ``cwd`` is the directory the backend CLI runs in. It carries a default only
    so the ~37 tests that exercise the command builders — where it has no effect
    — need not supply one; ``invoke_agent`` and ``invoke_fix`` reject an empty
    value, and TestAgentCallSitesPassCwd rejects a call site that omits it.
    ``env`` replaces the backend subprocess's environment when set; the eval
    fixture harness uses it to put recording shims ahead of the real CLIs.
    """

    prompt: str
    cwd: str = ""
    # A complete environment for the backend subprocess, or None to inherit the
    # parent's. Not a delta: callers that need one variable changed build the
    # whole mapping, because a partial env silently strips PATH and HOME.
    env: dict[str, str] | None = None
    session_log: str = ""
    add_dirs: list[str] = field(default_factory=list)
    agent: AgentKind | None = None
    max_turns: int | None = None
    max_budget: float | None = None
    model: str = ""
    # Not the closed `Thinking` enum: this is read from the environment via
    # _resolve_thinking_level() and can carry values outside that set (e.g.
    # "xhigh"), same as `model` can carry values outside `ModelAlias`.
    thinking: str | None = None
    provider: str | None = None
    label: str = ""
    task: str | None = None
    repo: str | None = None
    pr: str | None = None


def _record_invocation(inv: AgentInvocation, *, entry_point: str, exit_code: int) -> None:
    _record(
        entry_point=entry_point, usage=_usage_from_log(inv.session_log),
        exit_code=exit_code, model=inv.model or None,
        task=inv.task, repo=inv.repo, pr=inv.pr,
    )


def invoke_agent(inv: AgentInvocation) -> int:
    """Full agent with tool use and JSONL streaming. Returns exit code."""
    _require_cwd(inv.cwd, "invoke_agent")
    code = _get_module().invoke_agent(inv)
    _record_invocation(inv, entry_point="agent", exit_code=code)
    return code


def invoke_fix(inv: AgentInvocation) -> int:
    """Agent with workspace write access, raw output echoed. Returns exit code."""
    _require_cwd(inv.cwd, "invoke_fix")
    code = _get_module().invoke_fix(inv)
    _record_invocation(inv, entry_point="fix", exit_code=code)
    return code


def is_available() -> bool:
    """Check if the selected backend binary exists on PATH."""
    return shutil.which(_backend()) is not None
