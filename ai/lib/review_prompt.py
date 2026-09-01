"""Prompt construction for claude-review: the byte budget and the render loop.

`PromptBuilder` collects the variables a template is rendered with, and
`PromptBuilder.fit` is what makes a prompt fit the token budget: it registers
the sections that can shrink — the pre-collected file contents, the
incremental delta, and the full diff — after everything fixed is already
accounted for, and pulls three levers in that order, only as far as the
shortfall requires. It rewrites the environment section to send the agent
after whatever it dropped, and reports the cuts in the prompt's size log. A
prompt still over budget once every lever is pulled raises `PromptTooLarge`
rather than being sent: the phase reports it before an agent starts, so it
costs nothing.

One builder per phase assembles the sections `review_prompt_sections` renders
into a `PromptBuilder`. Which phase reaches which builder, which template it
renders, and which file the agent is told to write are `review_registry`'s: it
holds the phase-to-builder table and `build_prompt`, which dispatches on it
and imports the builders from here.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from datetime import date
from pathlib import Path

import git_client
import json
import log
from agent_templates import build_output_block, build_worktree_block
from agent_types import EFFORT_PRESETS, Mode
from pr_domains import ReviewVerdict
from review_budget import (
    FileFit, MAX_PROMPT_BYTES, MIN_DIFF_BYTES, NON_PREFLIGHT_OVERHEAD_BYTES,
    fit_files,
)
from review_collect import build_project_context, format_preflight_data
from review_paths import FILENAME_PROMPT_STATS, review_artifact_path
from review_prompt_sections import (
    _build_delta_section, _build_env_section, _build_holistic_block,
    _build_issue_section, _build_omitted_guidance, _build_pr_header,
    _build_prior_section, _build_reply_threads_section,
    _build_reviews_section, _build_state_context_section,
    _build_unaccounted_section, _is_incremental,
)
from review_types import PreflightData, ReviewJob

# The verdicts the prompt offers, written from the same members the review's
# `## Verdict` line is parsed against — the wording an agent is asked for cannot
# drift from the wording that is recognised.
VERDICT_OPTIONS = " / ".join(v.prose for v in ReviewVerdict)


@dataclass(frozen=True)
class CommonSections:
    """Sections shared by every template, built once per prompt.

    The incremental delta is not among them: it is budgeted, and a phase that
    scopes itself to one group budgets a different section from a phase that
    reads the whole PR. `PromptBuilder.fit` builds and registers it instead.
    """

    today: str
    generator_version: str
    pr_header: str
    state_context: str
    reviews_section: str
    reply_threads: str
    env_section: str
    issue_section: str
    omitted_guidance: str
    max_turns: int


COMMON_SECTION_NAMES = frozenset(f.name for f in fields(CommonSections))


class PromptBuilder:
    """Collects the variables a template is rendered with.

    One registry feeds both `safe_substitute` and the byte accounting, so a
    value cannot be interpolated into a prompt without also counting against
    the diff budget and appearing in the prompt-size stats.
    """

    def __init__(self, common: CommonSections):
        self._common = common
        self._vars: dict[str, object] = {}
        self._plan: BudgetPlan | None = None

    def set(self, key: str, value) -> "PromptBuilder":
        self._vars[key] = value
        return self

    def shared(self, *keys: str) -> "PromptBuilder":
        """Register sections from `common` under their own names."""
        unknown = sorted(set(keys) - COMMON_SECTION_NAMES)
        if unknown:
            raise KeyError(
                f"not valid CommonSections fields: {', '.join(unknown)} — "
                f"valid names are {', '.join(sorted(COMMON_SECTION_NAMES))}"
            )
        for key in keys:
            self._vars[key] = getattr(self._common, key)
        return self

    def output(self, output_path: str, *, stdout_warning: bool = False) -> "PromptBuilder":
        return self.set(
            "output_block", build_output_block(output_path, stdout_warning=stdout_warning),
        )

    def worktree(self, wt_path: str) -> "PromptBuilder":
        return self.set("worktree_block", build_worktree_block(wt_path))

    def fit(
        self, job: ReviewJob, *,
        file_filter: list[str] | None = None,
        skip_file_contents: bool = False,
        skip_project_context: bool = False,
        min_diff: int = MIN_DIFF_BYTES,
    ) -> "PromptBuilder":
        """Register the budgeted sections, shrunk to whatever room is left.

        Everything registered before this call is fixed overhead; call it once,
        last, after every other variable. `file_filter` scopes the prompt to one
        group's files, `skip_file_contents` drops the pre-collected contents
        outright rather than waiting for the ladder to reach them, and
        `min_diff` is the floor the full diff will not shrink below — synthesis
        passes 0, having the findings already.

        Four sections are registered here and nowhere else, because this is the
        only place that knows what the budget cut: `preflight_data` and
        `delta_section`, which are what shrink, and `env_section` and
        `omitted_guidance`, which describe them and would otherwise promise the
        agent data the prompt no longer carries. The latter two are rewritten
        only if the caller registered them.
        """
        plan = _fit_budget(
            job, self._vars, file_filter=file_filter,
            skip_file_contents=skip_file_contents, min_diff=min_diff,
        )
        self._plan = plan
        self.set("delta_section", plan.delta_section)
        self.set("preflight_data", _build_preflight_section(
            job, file_filter=file_filter,
            files=plan.files,
            skip_project_context=skip_project_context,
            max_diff_bytes=plan.diff_bytes,
        ))
        if "env_section" in self._vars:
            self.set("env_section", _build_env_section(
                job.wt_path, preflight=job.preflight,
                files=plan.files,
            ))
        if "omitted_guidance" in self._vars:
            self.set("omitted_guidance", _build_omitted_guidance(
                job.preflight,
                skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
                files=plan.files,
            ))
        return self

    @property
    def cuts(self) -> tuple[Cut, ...]:
        """What `fit` dropped to make the prompt fit, in the order it went."""
        return self._plan.cuts if self._plan else ()

    @property
    def vars(self) -> dict[str, object]:
        return dict(self._vars)


@dataclass(frozen=True)
class BuiltPrompt:
    """A prompt ready to render, and the label the phase logs it under."""

    builder: PromptBuilder
    label: str


def _build_preflight_section(
    job: ReviewJob, file_filter: list[str] | None = None,
    files: FileFit | None = None,
    skip_project_context: bool = False,
    max_diff_bytes: int | None = None,
) -> str:
    if not job.preflight:
        return ""
    return format_preflight_data(
        job.preflight, file_filter=file_filter,
        files=files,
        skip_project_context=skip_project_context,
        max_diff_bytes=max_diff_bytes,
    )


class BudgetLever(StrEnum):
    """Which section the budget ladder cut, named in the order it pulls them."""

    FILE_CONTENTS = "file_contents"
    DELTA = "delta"
    DIFF_FLOOR = "diff_floor"


@dataclass(frozen=True)
class Cut:
    """One lever the ladder pulled, and what it bought.

    `freed_bytes` is what the section gave back. `DIFF_FLOOR` gives nothing
    back — it is the ladder refusing to shrink the diff any further — so it
    carries `shortfall_bytes`, what the prompt is still over by, and
    `floor_bytes`, the size the diff was held at. A phase reviewing from
    findings it already has passes no floor, and then there is no diff left at
    all rather than a floor to report.

    Structured rather than pre-rendered because `prompt-stats.json` is the
    artifact an over-budget run is diagnosed from, and asking it which lever
    fired on which phase should not mean parsing the sentence written for the
    log. `describe` is that sentence, and the only place it is spelled out.
    """

    lever: BudgetLever
    freed_bytes: int = 0
    shortfall_bytes: int = 0
    floor_bytes: int = 0
    dropped_files: int = 0

    def describe(self) -> str:
        """How the cut reads in the prompt's size log."""
        if self.lever is BudgetLever.FILE_CONTENTS:
            plural = "" if self.dropped_files == 1 else "s"
            return (
                f"{self.freed_bytes // 1024}KB of pre-collected file contents "
                f"({self.dropped_files} file{plural})"
            )
        if self.lever is BudgetLever.DELTA:
            return f"{self.freed_bytes // 1024}KB of incremental delta"
        still_over = f"{self.shortfall_bytes // 1024}KB still over"
        if self.floor_bytes:
            return f"the full diff, floored at {self.floor_bytes // 1024}KB and {still_over}"
        return f"the full diff entirely, {still_over}"


@dataclass(frozen=True)
class BudgetPlan:
    """How much of the prompt each variable-size section gets, and what was cut.

    `delta_section` is the rendered incremental context, already shrunk;
    `diff_bytes` is the cap the full diff is truncated to; `files` is the
    budget's fit over the pre-collected file contents — which ones survived
    and which were dropped for room. `cuts` holds one `Cut` per lever the
    ladder had to pull, in the order it pulled them, and is empty on the
    ordinary path where everything fit.
    """

    delta_section: str
    diff_bytes: int
    files: FileFit
    cuts: tuple[Cut, ...]


def _fixed_preflight_bytes(pf: PreflightData | None) -> int:
    if not pf:
        return 0
    return (
        len(pf.commit_log.encode())
        + len(pf.claude_md.encode())
        + len(pf.architecture_md.encode())
        + sum(len(v.encode()) for v in pf.review_checklists.values())
    )


def _file_contents_bytes(
    pf: PreflightData | None, file_filter: list[str] | None,
) -> int:
    if not pf:
        return 0
    filter_set = set(file_filter) if file_filter else None
    return sum(
        len(v.encode()) for k, v in pf.file_contents.items()
        if filter_set is None or k in filter_set
    )


def _scoped_contents(
    pf: PreflightData | None, file_filter: list[str] | None,
) -> dict[str, str]:
    """The pre-collected file contents `file_filter` admits, or none without a preflight."""
    if not pf:
        return {}
    filter_set = set(file_filter) if file_filter else None
    return {
        k: v for k, v in pf.file_contents.items()
        if filter_set is None or k in filter_set
    }


def _fit_budget(
    job: ReviewJob,
    known_sections: dict[str, object],
    *,
    skip_file_contents: bool = False,
    file_filter: list[str] | None = None,
    min_diff: int = MIN_DIFF_BYTES,
) -> BudgetPlan:
    """Fit the variable sections into what `known_sections` leaves of the budget.

    Three levers, pulled in this order and only as far as the shortfall
    requires: keep only the pre-collected file contents that still fit, shrink
    the incremental delta, then floor the full diff at `min_diff`. Contents go
    first because they are the only section the agent can recover on its own
    — the worktree is checked out and `fit` rewrites the environment section
    to send it there — while a diff it is not shown is a change it does not
    know happened. The first lever ranks what it keeps by `(classify_tier,
    size)` rather than dropping the whole collection, so a ceiling too low for
    everything still buys the files most worth having.

    Pulling every lever is not a guarantee of fitting: the fixed overhead alone
    can exceed the budget. The plan then reports the cuts it made and
    `build_prompt` raises `PromptTooLarge` on the rendered result, rather than
    logging past a prompt the model will reject.
    """
    # `is not None`, not truthiness — a falsy value (0, False) still renders
    # into the prompt and must count against the budget.
    known_bytes = sum(
        len(str(v).encode()) for v in known_sections.values() if v is not None
    )
    fixed = NON_PREFLIGHT_OVERHEAD_BYTES + known_bytes + _fixed_preflight_bytes(job.preflight)
    contents = 0 if skip_file_contents else _file_contents_bytes(job.preflight, file_filter)
    scoped = {} if skip_file_contents else _scoped_contents(job.preflight, file_filter)
    files = FileFit(scoped, job.preflight.file_permissions if job.preflight else {}, [])
    delta = _build_delta_section(job.preflight, file_filter=file_filter)
    cuts: list[Cut] = []

    if contents and fixed + contents + len(delta.encode()) + min_diff > MAX_PROMPT_BYTES:
        room = max(0, MAX_PROMPT_BYTES - fixed - len(delta.encode()) - min_diff)
        files = fit_files(scoped, files.permissions, room)
        kept = sum(len(v.encode()) for v in files.included.values())
        cuts.append(Cut(
            BudgetLever.FILE_CONTENTS,
            freed_bytes=contents - kept,
            dropped_files=len(files.omitted),
        ))
        contents = kept

    delta_room = max(0, MAX_PROMPT_BYTES - fixed - contents - min_diff)
    if len(delta.encode()) > delta_room:
        shrunk = _build_delta_section(
            job.preflight, file_filter=file_filter, max_bytes=delta_room,
        )
        cuts.append(Cut(
            BudgetLever.DELTA,
            freed_bytes=len(delta.encode()) - len(shrunk.encode()),
        ))
        delta = shrunk

    diff_bytes = MAX_PROMPT_BYTES - fixed - contents - len(delta.encode())
    if diff_bytes < min_diff:
        # Recorded as a shortfall rather than as bytes freed, because the floor
        # frees nothing: it is what the ladder could not absorb, and so is also
        # what the rendered prompt will be over by.
        cuts.append(Cut(
            BudgetLever.DIFF_FLOOR,
            shortfall_bytes=min_diff - diff_bytes,
            floor_bytes=min_diff,
        ))
        diff_bytes = min_diff

    return BudgetPlan(
        delta_section=delta,
        diff_bytes=diff_bytes,
        files=files,
        cuts=tuple(cuts),
    )


def _log_prompt_size(
    template_name: str, prompt: str, sections: dict[str, object], job: ReviewJob,
    label: str = "", cuts: tuple[Cut, ...] = (),
) -> str:
    prompt_bytes = len(prompt.encode())
    prompt_kb = prompt_bytes // 1024
    budget_kb = MAX_PROMPT_BYTES // 1024

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
    if prompt_bytes > MAX_PROMPT_BYTES:
        msg += f" — EXCEEDS budget by {(prompt_bytes - MAX_PROMPT_BYTES) // 1024}KB"
    log.info(msg)

    suffix = f"-{label}" if label else ""
    prompt_file = review_artifact_path(job.review_file, f"prompt-{template_name}{suffix}")
    try:
        Path(prompt_file).write_text(prompt)
    except OSError:
        pass

    stats: dict = {
        "template": f"{template_name}{suffix}",
        "prompt_bytes": prompt_bytes,
        "budget_bytes": MAX_PROMPT_BYTES,
        "utilization_pct": round(prompt_bytes / MAX_PROMPT_BYTES * 100, 1),
        "sections": section_sizes,
        "cuts": [asdict(c) for c in cuts],
    }
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
    # Read existing stats — corrupt files from concurrent writes are discarded
    stats_file = review_artifact_path(job.review_file, FILENAME_PROMPT_STATS)
    existing: list = []
    try:
        parsed = json.loads(Path(stats_file).read_text())
        existing = parsed if isinstance(parsed, list) else [parsed]
    except (OSError, json.JSONDecodeError):
        pass
    existing.append(stats)
    try:
        Path(stats_file).write_text(json.dumps(existing, indent=2))
    except OSError:
        pass

    return prompt


def _incremental_prior_ctx(job: ReviewJob, base_ctx: str) -> str:
    """Return incremental-aware prior context when delta data is available."""
    if not _is_incremental(job):
        return base_ctx
    pf = job.preflight
    prior_sha = git_client.abbrev(pf.prior_head_sha)
    head_sha = git_client.abbrev(job.pr.head_sha)
    n_files = len(pf.delta_files)
    incremental_note = (
        f"\n\n**Incremental review note:** {n_files} file(s) changed since the "
        f"prior review ({prior_sha}..{head_sha}). Focus on changes in the "
        f"'Incremental review context' section."
    )
    return base_ctx + incremental_note


# ── Per-phase prompt builders ────────────────────────────────────────────────
#
# One builder per phase, registered in `review_registry`'s table. Each is
# handed the job, the sections every prompt shares, the phase's own extras, and
# the path its agent writes to — which `review_registry.build_prompt` derives
# from the phase spec, so no caller passes an output path in. Two of them serve
# both review modes; `job.mode` is what they read to tell the modes apart.


# The re-review preamble the single-agent prompt opens its prior findings with.
# What follows it is the same either way — only what is being re-read differs,
# which is why the two are spelled out in full rather than assembled from a
# shared tail and a per-mode head.
_REREVIEW_CTX: dict[Mode, str] = {
    Mode.PR: (
        "This is a re-review. Below are the findings from the previous review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ),
    Mode.SELF: (
        "This is a re-review of your own code. Below are the findings from the previous self-review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ),
}


def _identify_review(b: PromptBuilder, job: ReviewJob, **pr_only) -> None:
    """Register what names the review — the only thing the two modes split on.

    A self-review has no PR to point at, so it is identified by its branch and
    has no reviews on it to read. `pr_only` carries whatever else belongs to the
    PR side of one template: the verdict wording for the single-agent prompt,
    the title for synthesis. A caller states that difference rather than
    restating the split it hangs off.
    """
    if job.mode is Mode.SELF:
        b.set("branch_name", job.pr.head)
        return
    b.shared("reviews_section")
    b.set("pr_number", job.pr_number)
    for key, value in pr_only.items():
        b.set(key, value)


def _prompt_single(job, common, extra, output):
    """The one-agent review, of an open PR or of the working branch.

    The modes differ in three places, all of them consequences of there being
    no PR: a self-review is identified by its branch, has no reviews on it to
    read, and is not asked for a verdict.
    """
    prior_section = _build_prior_section(
        job.prior_review,
        _incremental_prior_ctx(job, _REREVIEW_CTX[job.mode]),
        reply_threads=job.reply_threads,
    )
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context",
        "reply_threads", "env_section", "issue_section", "generator_version",
        "omitted_guidance", "max_turns",
    )
    _identify_review(b, job, verdict_options=VERDICT_OPTIONS)
    b.set("repo", job.repo)
    b.set("prior_section", prior_section)
    b.output(output, stdout_warning=True)
    b.fit(job)
    return BuiltPrompt(b, "")


def _prompt_synthesis(job, common, extra, output):
    """The group findings written up as the review document.

    Same split as `_prompt_single`, minus the verdict: synthesis asks for one in
    either mode, each template in its own words.

    The prior section here is not the prior review — the group agents were each
    shown their slice of it and their conclusions are already in
    `merged_content`. It is the remainder: the findings none of them accounted
    for, which only this agent is still in a position to decide.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "reply_threads",
        "today", "generator_version", "max_turns",
    )
    _identify_review(b, job, pr_title=job.pr.title)
    b.set("repo", job.repo)
    b.set("pr_head_sha", job.pr.head_sha)
    b.set("wt_path", job.wt_path)
    b.set("prior_section", _build_unaccounted_section(extra.get("unaccounted_prior") or []))
    b.set("group_count", extra["group_count"])
    b.set("verdict_options", VERDICT_OPTIONS)
    b.set("holistic_content", extra.get("holistic_content") or "_No holistic assessment available._")
    b.set("merged_content", extra["merged_content"])
    b.output(output)
    # Synthesis has all findings in merged_content — diff is supplementary,
    # so allow it to shrink to 0 rather than blowing the budget.
    b.fit(job, skip_file_contents=True, min_diff=0)
    return BuiltPrompt(b, "")


def _survey_prompt(job, common, extra, output):
    """The survey of the whole PR — the holistic scan and the scout both.

    The two are alternative first passes over identical inputs. Only the file
    they write differs, and that is the phase spec's answer rather than
    anything this builder has to know.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "reviews_section",
        "issue_section", "env_section", "omitted_guidance", "max_turns",
    )
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("all_files_formatted", job.pr.all_files_formatted)
    b.output(output)
    b.fit(job)
    return BuiltPrompt(b, "")


def _prompt_group(job, common, extra, output):
    group_files = extra.get("group_file_paths", [])
    file_filter = group_files or None
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the prior findings for YOUR files. "
        "Carry forward the ones still present; leave fixed ones out of the "
        "severity sections."
    ))
    prior_section = _build_prior_section(
        job.prior_review, prior_ctx,
        file_filter=file_filter,
        reply_threads=job.reply_threads,
    )
    holistic_block = _build_holistic_block(
        extra.get("holistic_content", ""), job.pr.changed_files,
    )
    b = PromptBuilder(common)
    b.shared("issue_section", "env_section", "omitted_guidance", "max_turns")
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("pr_header", _build_pr_header(
        job.pr, job.ctx, job.effort, file_filter=file_filter,
    ))
    b.set("reply_threads", _build_reply_threads_section(job.reply_threads, file_filter=file_filter))
    b.set("project_context", build_project_context(job.preflight, file_filter=file_filter) if job.preflight else "")
    b.set("holistic_block", holistic_block)
    b.set("prior_section", prior_section)
    b.set("group_idx", extra["group_idx"])
    b.set("group_count", extra["group_count"])
    b.set("group_name", extra["group_name"])
    b.set("group_files_formatted", extra["group_files_formatted"])
    b.output(output)
    b.fit(job, file_filter=file_filter, skip_project_context=True)
    return BuiltPrompt(b, str(extra["group_idx"]))


def _prompt_disprove(job, common, extra, output):
    b = PromptBuilder(common)
    b.shared("max_turns")
    b.set("review_content", extra.get("review_content", ""))
    b.output(output)
    return BuiltPrompt(b, "")


def _build_common_sections(job: ReviewJob, *, max_turns: int) -> CommonSections:
    return CommonSections(
        today=date.today().isoformat(),
        generator_version=job.generator_version,
        pr_header=_build_pr_header(
            job.pr, job.ctx, job.effort, viewer_role=job.viewer_role,
        ),
        state_context=_build_state_context_section(job),
        reviews_section=_build_reviews_section(job.ctx),
        reply_threads=_build_reply_threads_section(job.reply_threads),
        env_section=_build_env_section(job.wt_path, preflight=job.preflight),
        issue_section=_build_issue_section(job.issue_link, job.issue_context),
        omitted_guidance=_build_omitted_guidance(
            job.preflight,
            skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
        ),
        max_turns=max_turns,
    )


class PromptTooLarge(RuntimeError):
    """A rendered prompt that exceeds the budget with every lever already pulled.

    Raised by `review_registry.build_prompt` after the prompt and its stats are
    written, so the oversized prompt is on disk to look at. The alternative —
    logging "EXCEEDS budget" and sending it anyway — spends a phase's cost on a
    request the model truncates or rejects, and reports whatever comes back as
    the phase's finding.
    """

    def __init__(self, template: str, prompt_bytes: int):
        self.template = template
        self.prompt_bytes = prompt_bytes
        super().__init__(
            f"{template} prompt is {prompt_bytes // 1024}KB against a "
            f"{MAX_PROMPT_BYTES // 1024}KB budget, with every lever already pulled"
        )


