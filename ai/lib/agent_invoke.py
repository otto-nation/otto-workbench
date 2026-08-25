"""The one owner of an agent invocation: resolve the phase, run it, guard it.

``agent_types`` says what a phase is, ``agent_registry`` says which phases there
are, ``agent_phases`` says what one resolves to here, and ``ai_backend`` knows
how to talk to a CLI. This module is what sits between them: given a phase and
a prompt, it builds the invocation from the phase's resolved model, thinking
level and provider, runs it, and hands the result to ``agent_retry``'s guard.

One function per ``PhaseShape``, and a phase reaches exactly the one its spec
names — ``run_prompt`` for a stateless call, ``run_agent`` for a tool-using
agent, ``run_fix`` for one that writes to the branch.

Call sites used to do that assembly themselves, and each one did it slightly
differently — a hardcoded model here, a missing retry ceiling there, a usage
ledger label spelled a third way. Reaching ``ai_backend`` directly is what let
those differ; going through here is what stops them, and
``TestOneOwnerForBackendCalls`` is what keeps it that way.
"""

# doc-group: pipeline

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import agent_phases
import agent_retry
import ai_backend
import log
import review_agent
from agent_registry import PHASES
from agent_types import Phase, PhaseShape
from ai_backend import AgentInvocation
from review_common import Diagnosis, preserve_log, restore_preserved
from workbench_config import WorkbenchConfig


def _require_shape(phase: Phase, shape: PhaseShape, runner: str):
    """Reject a phase handed to a runner its spec does not name.

    Returns the spec, since every caller wants it next.
    """
    spec = PHASES[phase]
    if spec.shape is not shape:
        raise ValueError(
            f"{phase} is shaped {spec.shape}, not {shape} — {runner}() is not "
            "the entry point that runs it"
        )
    return spec


@dataclass(frozen=True)
class _Knobs:
    """The three settings every shape resolves the same way, whatever runs it."""

    model: str | None
    thinking: str | None
    provider: str | None


def _resolved(phase: Phase, config: WorkbenchConfig | None) -> _Knobs:
    return _Knobs(
        model=agent_phases.phase_model(phase, None, config),
        thinking=agent_phases.phase_thinking(phase, None, config),
        provider=agent_phases.phase_provider(config),
    )


# ── Prompt shape ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptResult:
    """What one stateless prompt answered.

    ``usable`` is the caller's own predicate applied to ``text`` — the same one
    the retry used to decide whether to ask again, so a caller reads the verdict
    rather than computing it a second time. It is False whenever the call
    failed: there is no answer to judge.
    """

    text: str
    exit_code: int
    usable: bool

    @property
    def ok(self) -> bool:
        """The call succeeded and its answer can be consumed."""
        return self.exit_code == 0 and self.usable


def run_prompt(
    phase: Phase,
    prompt: str,
    *,
    cwd: str | Path,
    usable: Callable[[str], bool],
    label: str = "",
    task: str = "",
    repo: str | None = None,
    pr: str | None = None,
    config: WorkbenchConfig | None = None,
) -> PromptResult:
    """Run a stateless phase, retrying once if its answer cannot be parsed.

    ``usable(text)`` says whether the answer can be consumed. These calls write
    no session log, so an unusable answer is the only evidence the agent spent a
    turn without doing the job — the same thrash the review pipeline diagnoses
    from its logs.

    ``task`` is the name this call bills to in the usage ledger, defaulting to
    the phase's own. A phase may span several of them: the ledger separates a
    conflict resolution from a lockfile command, while both are sized and
    modelled as one phase. ``label`` is what a retry warning calls this call,
    defaulting to the phase's label.

    ``cwd`` is required, as it is on the backend: without it the CLI inherits
    whichever worktree the process was launched from.
    """
    spec = _require_shape(phase, PhaseShape.PROMPT, "run_prompt")
    work_dir = str(cwd)
    knobs = _resolved(phase, config)
    ledger_task = task or str(phase)

    text, exit_code = agent_retry.retry_blank_response(
        lambda attempt: ai_backend.prompt(
            attempt, cwd=work_dir, model=knobs.model, thinking=knobs.thinking,
            provider=knobs.provider, task=ledger_task, repo=repo, pr=pr,
        ),
        prompt, label=label or spec.label, usable=usable,
    )
    return PromptResult(text, exit_code, exit_code == 0 and usable(text))


# ── Agent shape ──────────────────────────────────────────────────────────────


class QuotaThrottle:
    """Thread-safe throttle shared across pipeline agents.

    When any agent hits a 429, all pending agents wait before launching.
    """

    def __init__(self, backoff: float = 30.0, max_backoff: float = 120.0):
        self._lock = threading.Lock()
        self._resume_at: float = 0.0
        self._backoff = backoff
        self._max_backoff = max_backoff

    def report_exhausted(self, model: str) -> float:
        with self._lock:
            wait = self._backoff
            self._resume_at = time.monotonic() + wait
            self._backoff = min(self._backoff * 2, self._max_backoff)
        log.warn(f"Quota exhausted on {model} — backing off {wait:.0f}s")
        return wait

    def wait_if_needed(self) -> None:
        with self._lock:
            resume_at = self._resume_at
        remaining = resume_at - time.monotonic()
        if remaining > 0:
            log.info(f"Throttle: waiting {remaining:.0f}s for quota to recover")
            time.sleep(remaining)


def _invoke_once(inv: AgentInvocation) -> int:
    prior_log = preserve_log(inv.session_log)
    rc = ai_backend.invoke_agent(inv)
    restore_preserved(inv.session_log, prior_log)
    return rc


def run_agent(
    inv: AgentInvocation, *, throttle: QuotaThrottle | None = None,
) -> int:
    """Run one tool-using agent, retrying once through a quota backoff.

    Unlike the other two runners this takes an invocation already built rather
    than a phase, because the caller that builds it —
    ``review_phases.PhaseRunner`` — needs values no phase can supply on its own:
    the group index its session log is named for, the artifact directory it may
    read, and a per-attempt turn budget a retry has just raised. Resolving the
    phase is that runner's job; reaching the backend is this one's.

    ``throttle`` is shared across a review's parallel agents, so a 429 on any
    one of them holds the rest back. Without one the retry still happens, after
    a fixed backoff this agent waits out alone.
    """
    if throttle:
        throttle.wait_if_needed()

    rc = ai_backend.invoke_agent(inv)
    if rc != 0 and inv.model and review_agent.is_quota_error(inv.session_log):
        if throttle:
            wait = throttle.report_exhausted(inv.model)
            time.sleep(wait)
        else:
            log.warn(f"Quota exhausted on {inv.model} — retrying once after 30s backoff")
            time.sleep(30)
        rc = _invoke_once(inv)
    return rc


# ── Fix shape ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FixResult:
    """What one guarded fix pass left behind.

    ``exit_code`` is the backend's, from whichever attempt ran last.
    ``unproductive`` is the guard's diagnosis when even the retry produced
    nothing, and ``None`` once the pass did work — a pass can exit non-zero
    having still checked items off, and a caller usually cares about both.
    """

    exit_code: int
    unproductive: Diagnosis | None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_fix(
    phase: Phase,
    prompt: str,
    *,
    cwd: str | Path,
    session_log: str,
    produced: Callable[[], bool] | None,
    add_dirs: Sequence[str | Path] | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    label: str = "",
    task: str = "",
    hint_select: Callable[[Diagnosis], str] = agent_retry.hint_for,
    repo: str | None = None,
    pr: str | None = None,
    config: WorkbenchConfig | None = None,
) -> FixResult:
    """Run a phase that edits the workspace, retrying once if it produced nothing.

    ``produced()`` reports whether the pass left anything behind — a checked box
    on a tracking file, for every caller so far — and ``None`` means the caller
    has no such signal, which runs the pass once with no guard. ``max_turns``
    and ``max_budget`` override what the phase resolves to, which is how a
    caller that sized the pass against its own checklist keeps the number it
    already put in the prompt. ``add_dirs`` defaults to ``cwd`` alone; ``label``
    and ``task`` to the phase's own.

    Only phases shaped ``FIX`` may come through here: the retry hints and the
    ``produced()`` contract are both about work landing in the worktree, and a
    read-only review phase belongs to ``review_phases.PhaseRunner``.
    """
    spec = _require_shape(phase, PhaseShape.FIX, "run_fix")

    work_dir = str(cwd)
    turns = agent_phases.phase_turns(phase) if max_turns is None else max_turns
    budget = agent_phases.phase_budget(phase) if max_budget is None else max_budget
    dirs = [str(d) for d in add_dirs] if add_dirs else [work_dir]
    name = label or spec.label
    knobs = _resolved(phase, config)

    exit_code = 0

    def invoke(text: str, attempt_turns: int) -> int:
        nonlocal exit_code
        exit_code = ai_backend.invoke_fix(ai_backend.AgentInvocation(
            prompt=text,
            cwd=work_dir,
            session_log=session_log,
            add_dirs=dirs,
            max_turns=attempt_turns,
            max_budget=budget,
            model=knobs.model,
            thinking=knobs.thinking,
            provider=knobs.provider,
            label=name,
            task=task or str(phase),
            repo=repo,
            pr=pr,
        ))
        return exit_code

    if produced is None:
        invoke(prompt, turns)
        return FixResult(exit_code, None)

    unproductive = agent_retry.run_guarded(
        invoke, prompt, session_log,
        label=name, max_turns=turns,
        produced=produced, hint_select=hint_select,
        ceiling=spec.retry.ceiling,
    )
    return FixResult(exit_code, unproductive)
