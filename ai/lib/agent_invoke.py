"""The one owner of an agent invocation: resolve the phase, run it, guard it.

``agent_types`` says what a phase is, ``agent_phases`` says what it resolves to
here, and ``ai_backend`` knows how to talk to a CLI. This module is what sits
between them: given a phase and a prompt, it builds the invocation from the
phase's resolved model, thinking level and provider, runs it, and hands the
result to ``agent_retry``'s guard with the phase's own retry ceiling.

Call sites used to do that assembly themselves, and each one did it slightly
differently — a hardcoded model here, a missing retry ceiling there, a usage
ledger label spelled a third way. Reaching ``ai_backend`` directly is what let
those differ; going through here is what stops them.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import agent_phases
import agent_retry
import ai_backend
from agent_types import PHASES, Phase
from review_common import Diagnosis
from workbench_config import WorkbenchConfig


@dataclass(frozen=True)
class FixPassResult:
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


def run_fix_pass(
    phase: Phase,
    prompt: str,
    *,
    cwd: str | Path,
    session_log: str,
    produced: Callable[[], bool],
    add_dirs: Sequence[str | Path] | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    label: str = "",
    hint_select: Callable[[Diagnosis], str] = agent_retry.hint_for,
    repo: str | None = None,
    pr: str | None = None,
    config: WorkbenchConfig | None = None,
) -> FixPassResult:
    """Run a phase that edits the workspace, retrying once if it produced nothing.

    ``produced()`` reports whether the pass left anything behind — a checked box
    on a tracking file, for every caller so far. ``max_turns`` and ``max_budget``
    override what the phase resolves to, which is how a caller that sized the
    pass against its own checklist keeps the number it already put in the
    prompt. ``add_dirs`` defaults to ``cwd`` alone; ``label`` to the phase's.

    Only phases whose spec sets ``edits`` may come through here: the retry hints
    and the ``produced()`` contract are both about work landing in the worktree,
    and a read-only review phase belongs to ``review_phases.PhaseRunner``.
    """
    spec = PHASES[phase]
    if not spec.edits:
        raise ValueError(
            f"{phase} does not edit the workspace; run it through PhaseRunner"
        )

    work_dir = str(cwd)
    turns = agent_phases.phase_turns(phase) if max_turns is None else max_turns
    budget = agent_phases.phase_budget(phase) if max_budget is None else max_budget
    dirs = [str(d) for d in add_dirs] if add_dirs else [work_dir]
    name = label or spec.label

    model = agent_phases.phase_model(phase, None, config)
    thinking = agent_phases.phase_thinking(phase, None, config)
    provider = agent_phases.phase_provider(config)

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
            model=model,
            thinking=thinking,
            provider=provider,
            label=name,
            task=str(phase),
            repo=repo,
            pr=pr,
        ))
        return exit_code

    unproductive = agent_retry.run_guarded(
        invoke, prompt, session_log,
        label=name, max_turns=turns,
        produced=produced, hint_select=hint_select,
        ceiling=spec.retry.ceiling,
    )
    return FixPassResult(exit_code, unproductive)
