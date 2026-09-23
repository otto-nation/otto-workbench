"""AI backend abstraction layer.

Dispatches preflight(), prompt(), invoke_agent(), and invoke_fix() to the
correct backend (Claude Code CLI or Pi CLI), selected by the AI_BACKEND env var
or the ``agent.backend`` config key.

There is no default. Both CLIs are installable and either is a plausible choice,
so a machine that has not said which one it runs is unknown rather than assumed
— the same reasoning that leaves ``issues.provider`` unset rather than guessing
Linear. A default here is not a convenience: it silently sends every review, fix
and rebase-resolve to one vendor's CLI, with its flags, its auth and its billing,
and the only symptom is that the other one was never called. Dispatch raises
instead, naming both the env var and the config key.

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
from pathlib import Path

from agent import usage as ai_usage
from git import client as git_client
# Backend is defined in core.phases so the config layer can type agent.backend
# without importing this module, and re-exported here because this is where
# callers have always read it from.
from core.phases import AgentKind, Backend

ENV_AI_BACKEND = "AI_BACKEND"
CONFIG_KEY_BACKEND = "agent.backend"


class BackendNotSelected(RuntimeError):
    """Raised when nothing says which CLI to run."""


def _configured_backend() -> Backend | None:
    """The backend named by config, or None when the file does not say.

    Read through load_config_or_default: an unreadable config.yml must not turn
    every AI call into a parse error when the env var already answers the
    question.
    """
    from config.workbench_config import load_config_or_default

    return load_config_or_default().agent.backend


def _raw_configured_backend() -> str | None:
    """The unvalidated ``agent.backend`` value from config, or None when
    absent or the config could not be read at all.

    ``_configured_backend()`` goes through ``load_config_or_default()``,
    whose type coercion treats an unrecognised enum value the same as a
    missing key — both come back as the bare-default config, so a typo'd
    ``backend: cluade`` reaches ``_require_backend()`` looking identical to a
    config that never mentioned ``backend``. This reads the merged dict
    directly, before serde's enum coercion has a chance to discard the raw
    string, so the invalid value can be named in the error the same way an
    invalid ``AI_BACKEND`` already is.
    """
    from config.workbench_config import ConfigError, config_scopes, deep_merge, read_yaml

    merged: dict = {}
    try:
        for scope in config_scopes():
            merged = deep_merge(merged, read_yaml(scope.path))
    except ConfigError:
        return None
    agent = merged.get("agent")
    return agent.get("backend") if isinstance(agent, dict) else None


def selected_backend() -> Backend | None:
    """The selected backend, or None when neither layer names a valid one.

    Returns rather than raises so the callers that only *describe* the
    selection — the usage ledger, the PATH probe in ``is_available``, and the
    prompt builder choosing a write recipe — do not acquire a new failure mode.
    Dispatch is where the absence becomes an error.

    Public because that third caller is outside this module: ``agent.templates``
    has to know which CLI's tools an agent will actually have.

    An unrecognised value is None, not a fallback: a typo'd AI_BACKEND is a
    machine that meant something specific and did not get it.
    """
    raw = os.environ.get(ENV_AI_BACKEND)
    if raw:
        try:
            return Backend(raw)
        except ValueError:
            return None
    return _configured_backend()


def selected_backend_or_claude() -> Backend:
    """``selected_backend()``, falling back to Claude when nothing names one.

    For the callers that describe a write mechanism rather than dispatch one —
    ``agent.templates.build_output_block`` and ``agent.retry.no_write_hint`` —
    and so cannot raise on an unselected backend the way dispatch does. Both
    used to inline this fallback and its lazy import; kept here once so the
    two write-recipe callers cannot drift on which backend an unset one means.
    """
    return selected_backend() or Backend.CLAUDE


def _require_backend() -> Backend:
    """The selected backend, or a failure naming both ways to set it.

    An AI_BACKEND set to an unrecognised value is not the same as one left
    unset: the operator did configure something, just not one of the valid
    names, and the raw value is what tells them what to fix. The same holds
    for agent.backend in config.yml, which _configured_backend() cannot
    surface on its own — see _raw_configured_backend().
    """
    selected = selected_backend()
    if selected is None:
        valid = ", ".join(b.value for b in Backend)
        raw = os.environ.get(ENV_AI_BACKEND)
        if raw:
            raise BackendNotSelected(
                f"{ENV_AI_BACKEND}={raw!r} is not a valid backend "
                f"(expected one of {valid})"
            )
        raw_config = _raw_configured_backend()
        if raw_config:
            raise BackendNotSelected(
                f"{CONFIG_KEY_BACKEND}={raw_config!r} is not a valid backend "
                f"(expected one of {valid})"
            )
        raise BackendNotSelected(
            f"no AI backend selected: set {ENV_AI_BACKEND} or {CONFIG_KEY_BACKEND} "
            f"to one of {valid}"
        )
    return selected


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
            script=_script_name(), entry_point=entry_point,
            # "unknown" rather than a raise: this is inside the swallow-all
            # below, so raising here would drop the ledger row silently instead
            # of failing loudly, which is the opposite of the intent.
            backend=(sel.value if (sel := selected_backend()) else "unknown"),
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
    if _require_backend() is Backend.PI:
        from agent import backend_pi as mod
    else:
        from agent import backend_claude as mod
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
        raise ValueError(f"agent.backend.{entry_point}() requires a non-empty cwd")
    if not Path(cwd).is_dir():
        raise ValueError(
            f"agent.backend.{entry_point}() cwd is not a directory: {cwd}"
        )


def prompt(
    text: str, *, cwd: str, model: str | None = None,
    thinking: str | None = None, provider: str | None = None,
    task: str | None = None, repo: str | None = None, pr: str | None = None,
) -> tuple[str, int]:
    """Stateless text-in/text-out. Returns (response_text, exit_code).

    `cwd` is required — see _require_cwd for why it has no safe default.

    `thinking` and `provider` follow the same rule as on `AgentInvocation`: Pi
    honours both, Claude Code has a flag for neither and drops them.
    """
    _require_cwd(cwd, "prompt")
    result = _get_module().prompt(
        text, cwd=cwd, model=model, thinking=thinking, provider=provider,
    )
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
    #
    # Not the last word either: `agent_env` pins the editor variables over
    # whatever this holds, because an agent that can be handed an editor can
    # block forever on one. Nothing else is imposed.
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


def agent_env(inv: AgentInvocation) -> dict[str, str]:
    """The environment an agent subprocess runs under: ``inv.env``, editors pinned.

    An agent holds a shell, so it reaches git with argv this process never
    chose — `-c core.editor=true` protects the git calls the rebase driver
    makes and none of the ones an agent makes for itself. A `git commit`,
    `git rebase --edit-todo` or `git tag -a` from inside a tool call opens the
    operator's editor on a pipe with no terminal and blocks there, and the
    backends run unbounded, so nothing ends it. Pinning the variables is the
    only lever that reaches a command this process does not write.

    Applied to both backends and to every entry point, because which agent
    happens to shell out to git is not a property either backend can know.
    ``None`` still means inherit, as the field documents — the parent's
    environment with the pins over it, not a scrub. That inheritance is a
    snapshot of ``os.environ`` taken when this function runs, not the
    exec-time environment ``Popen(env=None)`` would use; both backends call
    this immediately before ``Popen``, so the two are indistinguishable in
    practice.
    """
    return git_client.unattended_env(os.environ if inv.env is None else inv.env)


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
    """Check if the selected backend binary exists on PATH.

    False when nothing selects a backend, rather than raising: the rebase paths
    call this to decide whether to offer AI conflict resolution at all, and they
    already handle "no backend" by carrying on without it. An unselected backend
    is unavailable in exactly the sense they are asking about.
    """
    selected = selected_backend()
    return selected is not None and shutil.which(selected) is not None
