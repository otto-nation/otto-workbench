"""Which prompt each phase builds, and which scan reads its output.

One table. A phase that prompts names its builder here and nowhere else, and a
phase whose output is read before the next one starts names its scan here too.

It cannot live on `PhaseSpec`: `agent.types` imports nothing but `phases` and
the standard library, and the builders live in `review.prompt`, which imports
`agent.registry` — putting them on the spec is a cycle as well as a layering
break. A table one layer down is the same declaration made once, and this is
that layer.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import core.log
from agent.backend import selected_backend
from agent.registry import PHASES
from core.phases import Phase
from agent.phases import phase_model
from review.paths import phase_output_path
from review.prompt import (
    BuiltPrompt,
    _prompt_disprove, _prompt_group, _prompt_synthesis, _prompt_single,
    _survey_prompt,
)
from review.prompt_fit import fit_rendered_prompt
from review.scout import format_leads_block, parse_scout_output
from review.types import ReviewJob


@dataclass(frozen=True)
class PhaseScan:
    """How a phase's output is read before the run moves on.

    `without` is what the log says when the output is missing and the run
    continues anyway; `read` extracts whatever the next phase needs, or None
    when the raw output is what it needs.
    """

    without: str
    read: Callable[[str], str] | None = None


@dataclass(frozen=True)
class ReviewPhase:
    build: Callable[..., BuiltPrompt]
    scan: PhaseScan | None = None


def _scout_leads(raw: str) -> str:
    """A scout scan as the leads block a group prompt embeds.

    Reports the tally as it goes, so a resumed run says what it recovered from
    the file rather than reaching the group phases having logged nothing.
    """
    leads, no_scrutiny = parse_scout_output(raw)
    core.log.info(f"Scout found {len(leads)} investigation leads, {len(no_scrutiny)} no-scrutiny files")
    return format_leads_block(leads, no_scrutiny)


_PHASES: dict[Phase, ReviewPhase] = {
    Phase.SINGLE: ReviewPhase(build=_prompt_single),
    Phase.HOLISTIC: ReviewPhase(
        build=_survey_prompt,
        scan=PhaseScan("continuing without it"),
    ),
    Phase.SCOUT: ReviewPhase(
        build=_survey_prompt,
        scan=PhaseScan("continuing without leads", read=_scout_leads),
    ),
    Phase.GROUP: ReviewPhase(build=_prompt_group),
    Phase.SYNTHESIS: ReviewPhase(build=_prompt_synthesis),
    Phase.DISPROVE: ReviewPhase(
        build=_prompt_disprove,
        scan=PhaseScan("keeping all findings"),
    ),
}


def for_phase(phase: Phase) -> ReviewPhase | None:
    """How `phase` builds its prompt and reads its output, or None if it does neither."""
    return _PHASES.get(phase)


def registered() -> frozenset[Phase]:
    """Every phase this table declares."""
    return frozenset(_PHASES)


def build_prompt(
    phase: Phase, job: ReviewJob, *,
    max_turns: int,
    prefix: str = "",
    ladder_bytes: int | None = None,
    **extra,
) -> str:
    """Render ``phase``'s prompt for ``job``, with ``max_turns`` turns to spend.

    The template and the file the agent is told to write both come off the
    phase's registry entry, so a caller names the phase and nothing else about
    it. ``extra`` carries only what the phase cannot derive — the group's
    identity and the content a later phase reasons over.

    ``prefix`` is prepended before verification so the counted prompt is the
    one the agent is sent (the group retry hint). ``ladder_bytes`` is an
    opening ladder target for in-phase overflow recovery.

    The byte ceiling is derived from the phase's own model, so a phase pointed
    at a 200k-window model budgets against that rather than against whatever
    the default happens to be. Raises `review.budget.UnknownModelWindow` when
    the model has no recorded window — including an unresolved tier alias —
    and `PromptTooLarge` when the result exceeds the ceiling even after the
    budget ladder has cut everything it can.
    """
    entry = for_phase(phase)
    if entry is None:
        raise ValueError(f"{phase} renders no review prompt")

    spec = PHASES[phase]
    # A phase that names an artifact of its own is told that path; the rest
    # write the review document. `group_idx` is the only index in play, and
    # `phase_output_path` rejects it for a phase that writes one artifact.
    output = (
        phase_output_path(job.review_file, phase, extra.get("group_idx"))
        if spec.output_filename else job.review_file
    )
    template_name = spec.template_for(job.mode)
    model = phase_model(phase, job.model or None, job.config)
    return fit_rendered_prompt(
        phase, job, max_turns=max_turns,
        template_name=template_name, output=output,
        builder=entry.build, extra=extra, model=model,
        backend=selected_backend(), prefix=prefix,
        ladder_bytes=ladder_bytes,
    )
