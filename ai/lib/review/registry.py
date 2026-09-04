"""Which prompt each phase builds, and which scan reads its output.

One table. A phase that prompts names its builder here and nowhere else, and a
phase whose output is read before the next one starts names its scan here too.

It cannot live on `PhaseSpec`: `agent_types` imports nothing but `phases` and
the standard library, and the builders live in `review_prompt`, which imports
`agent_registry` — putting them on the spec is a cycle as well as a layering
break. A table one layer down is the same declaration made once, and this is
that layer.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent import templates as agent_templates
from core import log
from agent.registry import PHASES
from core.phases import Phase
from review.budget import MAX_PROMPT_BYTES
from review.paths import phase_output_path
from review.prompt import (
    BuiltPrompt, PromptTooLarge, _build_common_sections, _log_prompt_size,
    _prompt_disprove, _prompt_group, _prompt_synthesis, _prompt_single,
    _survey_prompt,
)
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
    log.info(f"Scout found {len(leads)} investigation leads, {len(no_scrutiny)} no-scrutiny files")
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


def build_prompt(phase: Phase, job: ReviewJob, *, max_turns: int, **extra) -> str:
    """Render ``phase``'s prompt for ``job``, with ``max_turns`` turns to spend.

    The template and the file the agent is told to write both come off the
    phase's registry entry, so a caller names the phase and nothing else about
    it. ``extra`` carries only what the phase cannot derive — the group's
    identity and the content a later phase reasons over.

    Raises `PromptTooLarge` when the result exceeds `MAX_PROMPT_BYTES` even
    after the budget ladder has cut everything it can.
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

    common = _build_common_sections(job, max_turns=max_turns)
    built = entry.build(job, common, extra, output)
    template_vars = built.builder.vars
    rendered = agent_templates.render(template_name, **template_vars)
    prompt = _log_prompt_size(
        template_name, rendered, template_vars, job,
        label=built.label, cuts=built.builder.cuts,
    )
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise PromptTooLarge(template_name, len(prompt.encode()))
    return prompt
