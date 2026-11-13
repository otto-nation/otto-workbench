"""Each rendered prompt's exact input-token count is appended to `prompt-stats.json`
in the review directory (model it was counted against, bytes-per-token), via
`agent.token_count.count_tokens`. Set `WORKBENCH_AI_MEASURE_TOKENS=0` to opt out.
On by default because it shipped opt-in and was never once switched on. The
round trip is mostly fixed latency — about 0.29s for a 6KB prompt, 0.55s for a
374KB one — against a phase that then runs for minutes. A machine with no Vertex
credentials skips it and records nothing rather than recording a guess. The
count is the rendered prompt alone; the system prompt and tool schemas `claude
-p` assembles internally are charged to the same request and are not visible
here — see `agent.token_count` for the observed margin.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import os
import threading

import core.log
from core.serde import write_json
from agent.token_count import count_tokens
from core.phases import Phase
from review.budget import model_window_tokens
from review.paths import FILENAME_PROMPT_STATS, review_artifact_path
from review.prompt import BudgetAccounting, Cut
from review.types import PromptVerification, ReviewJob

# On by default, because a measurement nobody takes calibrates nothing: this
# shipped behind an opt-in and, across 1,758 recorded renders, was never once
# switched on — every density figure the budget work has to reason about is
# therefore inferred rather than measured. Set the variable to 0 to opt out.
#
# The cost is a round trip that is mostly fixed latency: measured here at 0.29s
# for 6KB and 0.55s for 374KB, against a phase that then runs for minutes. An
# unreachable counter is free — `count_tokens` returns None without raising —
# so a machine with no Vertex credentials pays nothing and records nothing.
_MEASURE_TOKENS_ENV = "WORKBENCH_AI_MEASURE_TOKENS"


def _measuring_disabled() -> bool:
    return os.environ.get(_MEASURE_TOKENS_ENV, "1") == "0"


def unverified_reason(phase: Phase | None) -> str:
    """Why `measured_tokens` has no count: the one owner of that vocabulary.

    `disabled` is the opt-out, `no_phase` is a caller that named no phase, and
    `unavailable` is everything else — the counter was asked and had no answer.
    """
    if _measuring_disabled():
        return "disabled"
    if phase is None:
        return "no_phase"
    return "unavailable"


def measured_tokens(prompt: str, phase: Phase | None, model: str | None) -> int | None:
    """The prompt's exact token count against ``model``.

    ``model`` is the one the caller already resolved to derive the budget, not
    a second resolution of it: the count, the density and the ceiling all have
    to describe the same model, and a density is uninterpretable without its
    tokenizer. The caller keeps the model it passed in rather than being handed
    it back.

    This is the rendered prompt only. The system prompt and tool schemas the
    CLI adds are charged to the same request and are not visible from here, so
    the request costs more than this says — see `agent.token_count`, which
    carries the observed margin.

    None means no measurement was taken or none was available, and the two are
    deliberately not distinguished here: both leave the stats record without a
    token count, which is the only thing a reader can act on.
    """
    if _measuring_disabled() or phase is None:
        return None
    return count_tokens(prompt, model)


# ceiling: process-local lock, upgrade to fcntl.flock on a sidecar if
# writers become separate processes. Group agents share a process today
# (ThreadPoolExecutor, see _run_parallel_reviews in phases.py); a sidecar
# lock is the extra file gc would then have to know about. If that call
# site ever moves to subprocess-per-group or multiprocessing, this lock
# stops protecting anything and needs to move with it.
prompt_stats_lock = threading.Lock()


def _append_prompt_stats(stats_file: str, stats: dict) -> None:
    """Append one record to prompt-stats.json, atomically and under a lock.

    `write_json` renames a temp file into place, so a reader never sees a
    truncated file. The lock covers the read-modify-write around that rename:
    two group agents can otherwise both snapshot the same list, each append
    one record, and the later rename drop the other's.

    A missing or unreadable file starts a fresh list — the first write of a
    run, or a truncated file from before this was atomic.
    """
    path = Path(stats_file)
    with prompt_stats_lock:
        existing: list = []
        try:
            parsed = json.loads(path.read_text())
            existing = parsed if isinstance(parsed, list) else [parsed]
        except (OSError, json.JSONDecodeError):
            pass
        existing.append(stats)
        try:
            write_json(path, existing)
        except OSError as exc:
            core.log.warn(f"{path} could not be written ({exc})")


def log_prompt_size(
    template_name: str, prompt: str, sections: dict[str, object], job: ReviewJob,
    *,
    budget_bytes: int,
    model: str,
    label: str = "", cuts: tuple[Cut, ...] = (), phase: Phase | None = None,
    accounting: BudgetAccounting | None = None,
    verification: PromptVerification | None = None,
    renders: int = 1,
    ladder_bytes: int | None = None,
) -> str:
    """Log the rendered prompt's size and record it; return ``prompt`` unchanged.

    Not a pure formatter. It writes the prompt to ``prompt-{template}[-label]``
    beside the review file and appends one record to ``prompt-stats.json`` as
    side effects; the return value is only there for call chains that pass the
    prompt along, and ``fit_rendered_prompt`` ignores it. ``verification`` is
    the loop's count for this render; without it the prompt is counted here,
    so a direct call still records a complete row.
    """
    prompt_bytes = len(prompt.encode())
    prompt_kb = prompt_bytes // 1024
    budget_kb = budget_bytes // 1024

    section_sizes = {}
    parts = []
    for name, value in sections.items():
        size = len(str(value).encode()) if value is not None else 0
        section_sizes[name] = size
        if size > 1024:
            parts.append(f"{name}={size // 1024}KB")
    section_summary = ", ".join(parts) if parts else "all <1KB"

    msg = f"Prompt [{template_name}]: {prompt_kb}KB / {budget_kb}KB ({section_summary})"
    if cuts:
        msg += " — dropped " + ", ".join(c.describe() for c in cuts)
    over_budget = prompt_bytes > budget_bytes
    if over_budget:
        msg += f" — EXCEEDS budget by {(prompt_bytes - budget_bytes) // 1024}KB"
    core.log.info(msg)

    suffix = f"-{label}" if label else ""
    prompt_file = review_artifact_path(job.review_file, f"prompt-{template_name}{suffix}")
    try:
        Path(prompt_file).write_text(prompt)
    except OSError:
        pass

    stats: dict = {
        # The file is append-only across runs and kept by gc, so a row from
        # an earlier run in the same directory is still here. Stamp the run
        # and head so those rows can be told apart. An older file without
        # these keys still loads; a missing stamp is "not recorded".
        "started_at": job.started_at,
        "head_sha": job.pr.head_sha,
        "template": f"{template_name}{suffix}",
        "prompt_bytes": prompt_bytes,
        "budget_bytes": budget_bytes,
        "utilization_pct": round(prompt_bytes / budget_bytes * 100, 1),
        "sections": section_sizes,
        "cuts": [asdict(c) for c in cuts],
        # The budget is only interpretable against the model it was derived
        # from: the window is per-model and the tokenizer is generation-
        # specific, so a density recorded without its model says nothing.
        # `model_window_tokens` rather than a table lookup, because a phase
        # with no model budgets against the unset-model floor and a reader of this
        # record needs the window the budget actually used — a 0 beside a
        # nonzero `budget_bytes` reads as a bug rather than as that path.
        "budget_model": model,
        "budget_window_tokens": model_window_tokens(model, cwd=job.wt_path),
    }
    # `is not None`, not truthiness: a dataclass without `__bool__` is always
    # truthy, so the shorter form would read as a check it is not.
    if accounting is not None:
        # What the render can be charged for, against what it cost. The diff is
        # charged at what it rendered to rather than at the cap it was handed,
        # because the cap is several times the spend on the ordinary path and a
        # residual taken against it is hugely negative on every healthy render
        # — which buries the small positive excess that is the only thing worth
        # detecting. `unaccounted_bytes` is the template's own text and the
        # block markup no lever sizes, a few KB; tens of KB means a section
        # reaches the prompt that nothing measured, and a budget that does not
        # measure everything it sends bounds nothing.
        #
        # ceiling: recorded, not asserted — the honest threshold is a question
        # for the data and there is none yet. Upgrade to a hard check once
        # #1221 has read enough records to name one.
        stats["allowance_bytes"] = accounting.allowance_bytes
        stats["accounted_bytes"] = accounting.accounted_bytes
        stats["unaccounted_bytes"] = prompt_bytes - accounting.accounted_bytes
    # The verify loop counts once per render and hands the result in. A
    # direct call (tests, a path that logged without verifying) still
    # measures here so the record is complete either way — never a second
    # round trip on the loop's path.
    if verification is not None:
        counted = verification.tokens
        unverified = None if verification.token_verified else verification.reason
    else:
        counted = measured_tokens(prompt, phase, model)
        unverified = None if counted is not None else unverified_reason(phase)
    # One record shape for both paths, so they cannot drift on which fields
    # they write.
    stats["token_verified"] = unverified is None
    if unverified is not None:
        stats["token_unverified_reason"] = unverified
    stats["renders"] = renders
    if ladder_bytes is not None:
        stats["ladder_bytes"] = ladder_bytes
    if counted is not None:
        # Both the count and the model are recorded: a density is meaningless
        # without the tokenizer it was measured against, and sonnet-5 counts the
        # same text ~27% denser than sonnet-4-5.
        stats["prompt_tokens"] = counted
        stats["token_model"] = model
        # A zero-token prompt has no density to report, and dividing by it would
        # fail the render over a statistic. Unreachable for a real prompt; the
        # guard is here because the field's absence is already how a reader is
        # told there is no density, and that path is worth keeping honest.
        if counted:
            stats["bytes_per_token"] = round(prompt_bytes / counted, 3)
    if job.preflight:
        pf = job.preflight
        stats["file_contents"] = {
            "included": {p: len(c.encode()) for p, c in pf.file_contents.items()},
            "omitted": pf.omitted_files,
        }
        stats["file_count"] = {
            "included": len(pf.file_contents),
            "omitted": len(pf.omitted_files),
        }
    stats_file = review_artifact_path(job.review_file, FILENAME_PROMPT_STATS)
    _append_prompt_stats(stats_file, stats)

    return prompt
