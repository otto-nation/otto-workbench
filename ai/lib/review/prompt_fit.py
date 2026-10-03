"""Plan → render → verify → re-plan, until the prompt fits or the cap is hit.

`review.registry.build_prompt` is the public entry; this module is the loop
it runs. The ladder plans against a byte target, the template renders, and
the result is checked twice: bytes against the spend ceiling always, tokens
plus the backend overhead reserve against the model's window when a count
exists. A missing count is an explicit third state, never a pass and never
a fail. The byte check still applies.

A render that does not fit ratchets the ladder down by the measured
overshoot — bytes directly, tokens converted at the density just measured —
and never grows. Three renders is the cap; past that the phase raises
`PromptTooLarge` the way a single over-budget render always has.
"""

# doc-group: pipeline

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import agent.templates
import core.log
from core.phases import Backend, Phase
from core.serde import write_json
from review.budget import (
    COMPLETION_RESERVE_TOKENS, ladder_target_bytes, model_window_tokens,
    overhead_reserve_tokens, prompt_budget_bytes,
)
from review.paths import FILENAME_PROMPT_STATS, review_artifact_path
from review.prompt import (
    BudgetLever, BuiltPrompt, PromptTooLarge, build_common_sections, log_prompt_size,
    measured_tokens, prompt_stats_lock, unverified_reason,
)
from review.types import PromptVerification, ReviewJob

# Three renders is one opening guess and two corrections. Past that the
# overshoot is not a markup miss, it is a prompt that will not fit.
MAX_PROMPT_RENDERS = 3

# Shrink a little more than the overshoot so the next render is not the
# current one minus a rounding error.
_RATCHET_MARGIN = 0.05


def verify_prompt(
    prompt: str,
    *,
    model: str,
    phase: Phase | None,
    backend: Backend | None,
    budget_bytes: int,
) -> PromptVerification:
    """Bytes always; tokens when countable; missing count is unverified."""
    prompt_bytes = len(prompt.encode())
    byte_overshoot = max(0, prompt_bytes - budget_bytes)
    # Counted even when the bytes already overshoot: the render is known not to
    # fit, but the count still feeds `prompt-stats.json` and the token-density
    # ratchet, which is worth the round trip.
    counted = measured_tokens(prompt, phase, model)
    if counted is None:
        return PromptVerification(
            prompt_bytes=prompt_bytes,
            budget_bytes=budget_bytes,
            tokens=None,
            reason=unverified_reason(phase),
            ok=byte_overshoot == 0,
            byte_overshoot=byte_overshoot,
            token_overshoot=0,
        )
    overhead = overhead_reserve_tokens(backend)
    charged = counted + overhead
    limit = model_window_tokens(model) - COMPLETION_RESERVE_TOKENS
    token_overshoot = max(0, charged - limit)
    return PromptVerification(
        prompt_bytes=prompt_bytes,
        budget_bytes=budget_bytes,
        tokens=counted,
        reason="",
        ok=byte_overshoot == 0 and token_overshoot == 0,
        byte_overshoot=byte_overshoot,
        token_overshoot=token_overshoot,
    )


def ratchet_target(
    current: int,
    verification: PromptVerification,
) -> int:
    """A strictly smaller ladder target, shrunk by the measured overshoot.

    Token overshoot is converted at the density this render just measured.
    Bytes overshoot is used directly. The larger of the two is the shrink,
    plus a small margin, and the result never grows.
    """
    overshoot = verification.byte_overshoot
    counted = verification.tokens
    if counted and verification.token_overshoot:
        density = verification.prompt_bytes / counted
        overshoot = max(
            overshoot, int(verification.token_overshoot * density),
        )
    shrink = int(overshoot * (1 + _RATCHET_MARGIN))
    if shrink < 1:
        shrink = 1
    return max(0, current - shrink)


def fit_rendered_prompt(
    phase: Phase,
    job: ReviewJob,
    *,
    max_turns: int,
    template_name: str,
    output: str,
    builder: Callable[..., BuiltPrompt],
    extra: dict,
    model: str,
    backend: Backend | None,
    prefix: str = "",
    ladder_bytes: int | None = None,
) -> str:
    """Render, verify, and re-plan until the prompt fits or the cap is hit.

    One token count per render: the verification's count is what
    `prompt-stats.json` records, so a fit on the first try costs one round
    trip and a miss costs one per attempt.
    """
    budget_bytes = prompt_budget_bytes(model, backend)
    default_ladder = ladder_target_bytes(model, backend)
    target = default_ladder if ladder_bytes is None else min(ladder_bytes, default_ladder)

    last_verification: PromptVerification | None = None
    for render_i in range(MAX_PROMPT_RENDERS):
        common = build_common_sections(
            job, max_turns=max_turns, budget_bytes=target,
        )
        built = builder(job, common, extra, output)
        rendered = agent.templates.render(template_name, **built.builder.vars)
        prompt = prefix + rendered
        verification = verify_prompt(
            prompt, model=model, phase=phase, backend=backend,
            budget_bytes=budget_bytes,
        )
        last_verification = verification
        log_prompt_size(
            template_name, prompt, built.builder.vars, job,
            label=built.label, cuts=built.builder.cuts, phase=phase,
            accounting=built.builder.accounting,
            budget_bytes=budget_bytes, model=model,
            verification=verification,
            renders=render_i + 1,
            ladder_bytes=target,
        )
        if verification.ok:
            return prompt
        # At the diff floor every lever is already spent, so a smaller target
        # renders the same prompt: stop before paying for another count.
        if any(c.lever is BudgetLever.DIFF_FLOOR for c in built.builder.cuts):
            break
        next_target = ratchet_target(target, verification)
        # `ratchet_target` always steps down by at least 1, so this only fires
        # when `target` is already 0 and cannot go lower; it is not a general
        # stalled-ratchet check.
        if next_target >= target or render_i == MAX_PROMPT_RENDERS - 1:
            break
        core.log.info(
            f"Prompt [{template_name}] did not verify "
            f"(bytes +{verification.byte_overshoot}, "
            f"tokens +{verification.token_overshoot}) — "
            f"re-planning at {next_target} bytes "
            f"(render {render_i + 2}/{MAX_PROMPT_RENDERS})"
        )
        target = next_target

    assert last_verification is not None
    raise PromptTooLarge(
        template_name, last_verification.prompt_bytes,
        budget_bytes=budget_bytes, model=model,
        token_overshoot=last_verification.token_overshoot,
    )


def _note_overhead(row: dict, first, template: str) -> None:
    counted = row.get("prompt_tokens")
    if not isinstance(counted, int):
        return
    overhead = first.input_tokens - counted
    row["overhead_tokens"] = overhead
    reserve = overhead_reserve_tokens()
    if overhead <= reserve:
        return
    core.log.warn(
        f"First-turn overhead {overhead} tokens exceeds the "
        f"backend reserve ({reserve}) for {template}"
    )


def _write_overhead_into_stats(path: Path, template: str, first) -> None:
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    rows = parsed if isinstance(parsed, list) else [parsed]
    matched = None
    for row in reversed(rows):
        if row.get("template") == template:
            matched = row
            break
    if matched is None:
        return
    matched["served_model"] = first.served_model
    _note_overhead(matched, first, template)
    try:
        write_json(path, rows)
    except OSError as exc:
        core.log.warn(f"{path} could not be written ({exc})")


def record_prompt_overhead(
    job: ReviewJob,
    session_log: str,
    phase: Phase,
    index: int | None = None,
) -> None:
    """Patch the latest prompt-stats record with served model and overhead.

    First-turn billed input minus counted prompt tokens is the CLI overhead
    the reserve exists to cover. Warns when it exceeds the backend's reserve,
    which is the signal to raise the reserve rather than to fail the review.
    A session with no first-turn usage, or a prompt that was never counted,
    leaves the record as it was.
    """
    from agent.registry import PHASES
    from agent.session import read_jsonl
    from agent.usage import first_turn_usage

    if not session_log or not Path(session_log).is_file():
        return
    try:
        records = read_jsonl(session_log)
    except OSError:
        return
    first = first_turn_usage(records)
    if first is None:
        return

    try:
        template = PHASES[phase].template_for(job.mode)
    except (KeyError, ValueError):
        return
    if index is not None:
        template = f"{template}-{index}"

    stats_file = review_artifact_path(job.review_file, FILENAME_PROMPT_STATS)
    path = Path(stats_file)
    with prompt_stats_lock:
        _write_overhead_into_stats(path, template, first)
