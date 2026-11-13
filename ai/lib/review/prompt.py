"""Prompt construction for review: the byte budget and the render loop.

`PromptBuilder` collects the variables a template is rendered with, and
`PromptBuilder.fit` is what makes a prompt fit the token budget: it registers
the sections that can shrink — the pre-collected file contents, the
incremental delta, the commit log, and the full diff — after everything fixed
is already accounted for, and pulls four levers in that order, only as far as
the shortfall requires. It rewrites the environment section to send the agent
after whatever it dropped, and reports the cuts in the prompt's size log. A
prompt still over budget once every lever is pulled raises `PromptTooLarge`
rather than being sent: the phase reports it before an agent starts, so it
costs nothing.

One builder per phase assembles the sections `review.prompt_sections` and
`review.prompt_prior` render into a `PromptBuilder`. Which phase reaches which
builder, which template it renders, and which file the agent is told to write
are `review.registry`'s: it holds the phase-to-builder table and
`build_prompt`, which dispatches on it and imports the builders from here.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

import git.client
from agent.templates import build_execution_claim_guard, build_output_block
from agent.types import EFFORT_PRESETS
from core.phases import Mode, Phase
from review.artifact_format import output_format
from review.budget import (
    FileFit, MIN_DIFF_BYTES,
    fit_files, fixed_preflight_bytes,
)
from review.collect import (
    PreflightBlock, build_project_context, format_preflight_data, trim_commit_log,
)
from review.prompt_prior import (
    _build_prior_section, _build_unaccounted_section,
)
import review.behaviours
import review.document
from review.claims import has_claim_source
from review.focus import areas_for_group, needs_derived, user_areas
from review.grouping import hint_subjects, unpaired_subjects
from review.prompt_sections import (
    OmittedReads, _build_delta_section, _build_env_section, _build_focus_section,
    _build_issue_section, _build_omitted_guidance, _build_pr_header,
    _build_reply_threads_section, _build_reviews_section,
    _build_state_context_section, _build_test_hint_section, _is_incremental,
)
from review.types import PreflightData, ReviewJob

@dataclass(frozen=True)
class CommonSections:
    """Sections shared by every template, built once per prompt.

    The incremental delta is not among them: it is budgeted, and a phase that
    scopes itself to one group budgets a different section from a phase that
    reads the whole PR. `PromptBuilder.fit` builds and registers it instead.

    `budget_bytes` is the one field that is not a section. It rides here
    because it is per-prompt context in exactly the way the sections are —
    resolved once from the phase's model, then read by the ladder, the stats
    record and the refusal check, which must all quote the same number.
    """

    pr_header: str
    state_context: str
    reviews_section: str
    reply_threads: str
    env_section: str
    issue_section: str
    focus_section: str
    omitted_guidance: str
    max_turns: int
    budget_bytes: int


# Every field a template may interpolate. `budget_bytes` is context rather than
# content, so naming it in `shared()` is the error the exclusion makes it.
COMMON_SECTION_NAMES = frozenset(f.name for f in fields(CommonSections)) - {"budget_bytes"}


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
        self._rendered_diff_bytes = 0

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

    def output(
        self, output_path: str, *, output_format: str, stdout_warning: bool = False,
    ) -> "PromptBuilder":
        """How the agent saves its file, then the JSON shape it writes.

        The write recipe is `agent.templates`' and is not changed here. The
        format follows it so the agent reads where to write before what to write.
        """
        block = build_output_block(output_path, stdout_warning=stdout_warning)
        return self.set("output_block", f"{block}\n\n{output_format}")

    def execution_claim_guard(self, *, cross_cutting_step: int | None = None) -> "PromptBuilder":
        """The ban on claiming a command was run, for a write-first template.

        Every template whose turn budget says to write before investigating
        renders this. Substituted rather than written into each template for
        the reason `output_block` is: one owner, so the five cannot drift.
        """
        return self.set(
            "execution_claim_guard",
            build_execution_claim_guard(cross_cutting_step),
        )

    def fit(
        self, job: ReviewJob, *,
        file_filter: list[str] | None = None,
        skip_file_contents: bool = False,
        skip_project_context: bool = False,
        min_diff: int = MIN_DIFF_BYTES,
        reads: OmittedReads = OmittedReads.REVIEW,
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
        only if the caller registered them. `reads` is which phase is asking
        about the omitted files, and is forwarded to `_build_omitted_guidance`.
        """
        plan = _fit_budget(
            job, self._vars, file_filter=file_filter,
            budget_bytes=self._common.budget_bytes,
            skip_file_contents=skip_file_contents,
            skip_project_context=skip_project_context,
            min_diff=min_diff,
        )
        self._plan = plan
        self.set("delta_section", plan.delta_section)
        block = _build_preflight_section(
            job, file_filter=file_filter,
            files=plan.files,
            skip_project_context=skip_project_context,
            max_diff_bytes=plan.diff_allowance_bytes,
            commit_log=plan.commit_log,
        )
        self.set("preflight_data", block.text)
        self._rendered_diff_bytes = block.rendered_diff_bytes
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
                reads=reads,
            ))
        return self

    @property
    def cuts(self) -> tuple[Cut, ...]:
        """What `fit` dropped to make the prompt fit, in the order it went."""
        return self._plan.cuts if self._plan else ()

    @property
    def accounting(self) -> "BudgetAccounting | None":
        """The plan reconciled against the render, or None if `fit` never ran.

        None rather than a zero sentinel for the phases that never call `fit`
        — disprove builds no budgeted section — so no accounting is recorded
        for a prompt no ladder planned.
        """
        return self._plan.reconcile(self._rendered_diff_bytes) if self._plan else None

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
    commit_log: str | None = None,
) -> PreflightBlock:
    if not job.preflight:
        return PreflightBlock("", 0)
    return format_preflight_data(
        job.preflight, file_filter=file_filter,
        files=files,
        skip_project_context=skip_project_context,
        max_diff_bytes=max_diff_bytes,
        commit_log=commit_log,
    )


class BudgetLever(StrEnum):
    """Which section the budget ladder cut, named in the order it pulls them."""

    FILE_CONTENTS = "file_contents"
    DELTA = "delta"
    COMMIT_LOG = "commit_log"
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
        if self.lever is BudgetLever.COMMIT_LOG:
            return f"{self.freed_bytes // 1024}KB of commit log"
        still_over = f"{self.shortfall_bytes // 1024}KB still over"
        if self.floor_bytes:
            return f"the full diff, floored at {self.floor_bytes // 1024}KB and {still_over}"
        return f"the full diff entirely, {still_over}"


@dataclass(frozen=True)
class BudgetAccounting:
    """What the ladder authorised for a render, against what it can charge it.

    `allowance_bytes` is the room the ladder handed out; `accounted_bytes` is
    the same measured sections with the diff charged at what it actually
    rendered to. The gap between them is unspent allowance, which is ordinary
    and says nothing. The gap between `accounted_bytes` and the rendered
    prompt is the figure worth reading — see `review.prompt_stats.log_prompt_size`.

    A separate type rather than two more fields on `BudgetPlan`: a plan that is
    sometimes reconciled and sometimes not would leave `accounted_bytes`
    silently equal to the allowance on the unreconciled path, which is the one
    number with two meanings this replaced.
    """

    allowance_bytes: int
    accounted_bytes: int


@dataclass(frozen=True)
class BudgetPlan:
    """How much of the prompt each variable-size section gets, and what was cut.

    `delta_section` is the rendered incremental context, already shrunk;
    `diff_allowance_bytes` is the cap the full diff is truncated to — an upper
    bound the ordinary render spends a fraction of, not an estimate of its
    cost; `files` is the budget's fit over the pre-collected file contents —
    which ones survived and which were dropped for room. `cuts` holds one `Cut`
    per lever the ladder had to pull, in the order it pulled them, and is empty
    on the ordinary path where everything fit.

    `measured_bytes` is every section the ladder sized, the diff excluded: the
    part of the plan that renders at the size it was measured at. `reconcile`
    pairs it with what the diff cost, and `BudgetAccounting` is where the two
    resulting figures are described.
    """

    delta_section: str
    diff_allowance_bytes: int
    files: FileFit
    cuts: tuple[Cut, ...]
    measured_bytes: int = 0
    commit_log: str = ""

    @property
    def allowance_bytes(self) -> int:
        """Everything the ladder measured, plus the room it handed the diff."""
        return self.measured_bytes + self.diff_allowance_bytes

    def reconcile(self, rendered_diff_bytes: int) -> BudgetAccounting:
        """The same accounting, with the diff charged at what it rendered to."""
        return BudgetAccounting(
            allowance_bytes=self.allowance_bytes,
            accounted_bytes=self.measured_bytes + rendered_diff_bytes,
        )


def _fixed_preflight_bytes(
    pf: PreflightData | None, *, skip_project_context: bool = False,
) -> int:
    """The preflight bytes the ladder must reserve for `pf`.

    `skip_project_context` is for the caller that rendered the project context
    itself and registered it as its own template variable — `_prompt_group`
    does, scoped to its group's files. Those bytes are already in
    `known_bytes`, so reserving them here as well charges the phase twice for
    one section and takes the difference out of the diff. Nothing is reserved in
    that case: everything `fixed_preflight_bytes` counts is inside the context
    the caller already rendered, and the commit log is a lever in `_fit_budget`
    rather than a reserve.
    """
    if not pf:
        return 0
    if skip_project_context:
        # The commit log is a lever, so skipping the project context leaves
        # nothing in this reserve — the caller already registered it.
        return 0
    return fixed_preflight_bytes(
        pf.instructions_md, pf.architecture_md, pf.review_checklists,
        pf.review_profiles,
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


def _contents_bytes(contents: dict[str, str]) -> int:
    """The encoded size of a file-contents mapping.

    The budget weighs the same mapping twice — once to decide whether the
    FILE_CONTENTS lever has to fire, and again on what `fit_files` kept — so
    both readings come from here rather than from a second walk of the
    unfiltered preflight. A size derived from different files than the ones
    sent is a budget that agrees with the prompt only by coincidence.
    """
    return sum(len(v.encode()) for v in contents.values())


def _fit_budget(
    job: ReviewJob,
    known_sections: dict[str, object],
    *,
    budget_bytes: int,
    skip_file_contents: bool = False,
    skip_project_context: bool = False,
    file_filter: list[str] | None = None,
    min_diff: int = MIN_DIFF_BYTES,
) -> BudgetPlan:
    """Fit the variable sections into what `known_sections` leaves of the budget.

    Four levers, pulled in this order and only as far as the shortfall
    requires: keep only the pre-collected file contents that still fit, shrink
    the incremental delta, trim the commit log (newest first), then floor the
    full diff at `min_diff`. Contents go first because they are the only
    section the agent can recover on its own — the worktree is checked out and
    `fit` rewrites the environment section to send it there — while a diff it
    is not shown is a change it does not know happened. The commit log sits
    after the delta so scout and synthesis have a non-diff lever when the
    fixed sections overflow. The first lever ranks what it keeps by
    `(classify_tier, size)` rather than dropping the whole collection, so a
    ceiling too low for everything still buys the files most worth having.

    `skip_project_context` says the caller rendered the project context itself
    and registered it, so it is already in `known_sections` and must not be
    reserved for a second time.

    Pulling every lever is not a guarantee of fitting: the fixed overhead alone
    can exceed the budget. The plan then reports the cuts it made and
    `build_prompt` raises `PromptTooLarge` on the rendered result, rather than
    logging past a prompt the model will reject.

    `budget_bytes` is the ceiling for this phase's model, resolved by
    `review.budget.prompt_budget_bytes`. It is a parameter rather than a
    module constant because the window it derives from is a property of the
    model, and a phase can be pointed at a different one.
    """
    # `is not None`, not truthiness — a falsy value (0, False) still renders
    # into the prompt and must count against the budget.
    known_bytes = sum(
        len(str(v).encode()) for v in known_sections.values() if v is not None
    )
    # Everything the ladder can account for, and the whole of what it reserves.
    # A flat overhead reserve used to be added on top of this, covering the
    # template and the PR header — the same sections `known_bytes` measures
    # exactly, so it double-counted them and held back ~116KB no render spent.
    # What a prompt costs beyond its own text is reserved in tokens by
    # `OVERHEAD_RESERVE_TOKENS` before `budget_bytes` is derived, and the
    # markup the render adds by `RENDER_MARKUP_RESERVE_BYTES`.
    measured = known_bytes + _fixed_preflight_bytes(
        job.preflight, skip_project_context=skip_project_context,
    )
    scoped = {} if skip_file_contents else _scoped_contents(job.preflight, file_filter)
    contents = _contents_bytes(scoped)
    files = FileFit(scoped, job.preflight.file_permissions if job.preflight else {}, [])
    delta = _build_delta_section(job.preflight, file_filter=file_filter)
    commit_log = job.preflight.commit_log if job.preflight else ""
    log_bytes = len(commit_log.encode())
    delta_bytes = len(delta.encode())
    cuts: list[Cut] = []

    if contents and measured + contents + delta_bytes + log_bytes + min_diff > budget_bytes:
        room = max(0, budget_bytes - measured - delta_bytes - log_bytes - min_diff)
        files = fit_files(scoped, files.permissions, room)
        kept = _contents_bytes(files.included)
        cuts.append(Cut(
            BudgetLever.FILE_CONTENTS,
            freed_bytes=contents - kept,
            dropped_files=len(files.omitted),
        ))
        contents = kept

    delta_room = max(0, budget_bytes - measured - contents - log_bytes - min_diff)
    if delta_bytes > delta_room:
        shrunk = _build_delta_section(
            job.preflight, file_filter=file_filter, max_bytes=delta_room,
        )
        cuts.append(Cut(
            BudgetLever.DELTA,
            freed_bytes=delta_bytes - len(shrunk.encode()),
        ))
        delta = shrunk
        delta_bytes = len(delta.encode())

    commit_room = max(0, budget_bytes - measured - contents - delta_bytes - min_diff)
    if log_bytes > commit_room:
        trimmed = trim_commit_log(commit_log, commit_room)
        cuts.append(Cut(
            BudgetLever.COMMIT_LOG,
            freed_bytes=log_bytes - len(trimmed.encode()),
        ))
        commit_log = trimmed
        log_bytes = len(commit_log.encode())

    diff_bytes = budget_bytes - measured - contents - delta_bytes - log_bytes
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
        diff_allowance_bytes=diff_bytes,
        files=files,
        cuts=tuple(cuts),
        measured_bytes=measured + contents + delta_bytes + log_bytes,
        commit_log=commit_log,
    )


def _incremental_prior_ctx(
    job: ReviewJob, base_ctx: str, independent_ctx: str,
) -> str:
    """Preamble for a prior section: independent framing, or the incremental note.

    A prior on a full (non-incremental) review is continuity, not evidence.
    The incremental path appends a delta note onto the re-review preamble.
    """
    if not _is_incremental(job):
        if job.prior is None:
            return base_ctx
        if review.document.covers(job.prior.meta.head_sha, job.pr.head_sha):
            return f"{independent_ctx} {_SAME_HEAD_CLAUSE}"
        return independent_ctx
    pf = job.preflight
    prior_sha = git.client.abbrev(pf.prior_head_sha)
    head_sha = git.client.abbrev(job.pr.head_sha)
    n_files = len(pf.delta_files)
    incremental_note = (
        f"\n\n**Incremental review note:** {n_files} file(s) changed since the "
        f"prior review ({prior_sha}..{head_sha}). Focus on changes in the "
        f"'Incremental review context' section."
    )
    return base_ctx + incremental_note


# ── Per-phase prompt builders ────────────────────────────────────────────────
#
# One builder per phase, registered in `review.registry`'s table. Each is
# handed the job, the sections every prompt shares, the phase's own extras, and
# the path its agent writes to — which `review.registry.build_prompt` derives
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
        "- If the issue has been fixed, leave it out of `findings`\n"
        "- Add any new findings from changes since the last review"
    ),
    Mode.SELF: (
        "This is a re-review of your own code. Below are the findings from the previous self-review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of `findings`\n"
        "- Add any new findings from changes since the last review"
    ),
}

# The independent-review preamble a full-review prior opens with. Same-HEAD,
# focus-changed, and unreachable-prior runs are all full reviews: the earlier
# review's checks are not evidence. One sentence, varied only by whose review it
# was and what the agent is asked to cover.
def _independent_ctx(review_of: str, earlier: str, scope: str) -> str:
    return (
        f"This is an independent full review{review_of}. The findings below are from "
        f"an earlier {earlier} and are listed for continuity only, so their IDs, sids "
        "and declines carry over. Do not cite that review's checks or conclusions as "
        "evidence: re-verify each finding against the code, carry forward the ones "
        "still present, leave fixed ones out of `findings`, and review "
        f"{scope} as if no earlier review existed."
    )


_INDEPENDENT_CTX: dict[Mode, str] = {
    Mode.PR: _independent_ctx("", "review", "every changed file"),
    Mode.SELF: _independent_ctx(" of your own code", "self-review", "every changed file"),
}

_INDEPENDENT_GROUP_CTX = _independent_ctx("", "review", "every file in this group")

_SAME_HEAD_CLAUSE = (
    "The earlier review read this same commit; its conclusions are not a "
    "substitute for reading it again."
)

# Which numbered step adds cross-cutting findings, per synthesis template. The
# execution-claim guard names it, and the two templates number their task
# lists differently — a mismatch here sends the agent to the wrong step.
# tests/review_contracts_template_prose_test.py holds these against the templates themselves.
_SYNTHESIS_CROSS_CUTTING_STEP: dict[Mode, int] = {
    Mode.PR: 8,
    Mode.SELF: 9,
}


def _identify_review(b: PromptBuilder, job: ReviewJob, **pr_only) -> None:
    """Register what names the review — the only thing the two modes split on.

    A self-review has no PR to point at, so it is identified by its branch and
    has no reviews on it to read. `pr_only` carries whatever else belongs to the
    PR side of one template: the title for synthesis. A caller states that
    difference rather than restating the split it hangs off.
    """
    if job.mode is Mode.SELF:
        b.set("branch_name", job.pr.head)
        return
    b.shared("reviews_section")
    b.set("pr_number", job.pr_number)
    for key, value in pr_only.items():
        b.set(key, value)


def _artifact_format(job: ReviewJob, extra: dict, *, rereview: bool = False,
                     derive_focus: bool = False, has_areas: bool = True,
                     has_behaviour_source: bool = True) -> str:
    """The Output section's JSON example and field guide for this phase's artifact.

    `rereview` is whether this prompt carries prior findings to account for.
    Only then does the example show the fields that carry one forward (D20).
    """
    return output_format(extra["artifact"], job.mode, rereview=rereview,
                         derive_focus=derive_focus, has_areas=has_areas,
                         has_claim_source=has_claim_source(job),
                         has_behaviour_source=has_behaviour_source)


def _prompt_single(job, common, extra, output):
    """The one-agent review, of an open PR or of the working branch.

    The modes differ in two places, both consequences of there being no PR: a
    self-review is identified by its branch and has no reviews on it to read.
    """
    prior_section = _build_prior_section(
        job.prior,
        _incremental_prior_ctx(
            job, _REREVIEW_CTX[job.mode], _INDEPENDENT_CTX[job.mode],
        ),
        reply_threads=job.reply_threads,
    )
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context",
        "reply_threads", "env_section", "issue_section", "focus_section",
        "omitted_guidance", "max_turns",
    )
    b.set("test_hint_section", _build_test_hint_section(unpaired_subjects(job.pr.files)))
    _identify_review(b, job)
    b.set("repo", job.repo)
    b.set("prior_section", prior_section)
    b.execution_claim_guard()
    b.output(output, stdout_warning=True, output_format=_artifact_format(
        job, extra, rereview=bool(prior_section),
        derive_focus=needs_derived(job.focus), has_areas=bool(job.focus),
        has_behaviour_source=review.behaviours.has_behaviour_source(job),
    ))
    b.fit(
        job,
        reads=OmittedReads.SELF if job.mode is Mode.SELF else OmittedReads.REVIEW,
    )
    return BuiltPrompt(b, "")


def _prompt_synthesis(job, common, extra, output):
    """The group findings written up as the review document.

    Same split as `_prompt_single`.

    The prior section here is not the prior review — the group agents were each
    shown their slice of it and their conclusions are already in
    `merged_content`. It is the remainder: the findings none of them accounted
    for, which only this agent is still in a position to decide.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "reply_threads", "issue_section",
        "focus_section", "max_turns",
    )
    b.set("test_hint_section", _build_test_hint_section(unpaired_subjects(job.pr.files)))
    _identify_review(b, job, pr_title=job.pr.title)
    b.set("repo", job.repo)
    b.set("wt_path", job.wt_path)
    b.set("prior_section", _build_unaccounted_section(extra.get("unaccounted_prior") or []))
    b.set("group_count", extra["group_count"])
    b.set("holistic_content", extra.get("holistic_content") or "## Phase 1 context\n_No holistic assessment available._")
    b.set("merged_content", extra["merged_content"])
    # The step that authors new cross-cutting findings is numbered by the
    # template, and the two modes number it differently.
    b.execution_claim_guard(
        cross_cutting_step=_SYNTHESIS_CROSS_CUTTING_STEP[job.mode],
    )
    b.output(output, output_format=_artifact_format(
        job, extra, rereview=job.prior is not None,
        derive_focus=needs_derived(job.focus), has_areas=bool(job.focus),
        has_behaviour_source=review.behaviours.has_behaviour_source(job),
    ))
    # Synthesis has all findings in merged_content — diff is supplementary,
    # so allow it to shrink to 0 rather than blowing the budget.
    b.fit(job, skip_file_contents=True, min_diff=0)
    return BuiltPrompt(b, "")


_SURVEY_FOCUS_NOTE = (
    "These areas were requested and are already covered — the `focus` you derive "
    "should add what they do not."
)


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
    b.set("focus_section", _build_focus_section(user_areas(job.focus), note=_SURVEY_FOCUS_NOTE))
    b.output(output, output_format=_artifact_format(job, extra))
    b.fit(job, reads=OmittedReads.SURVEY)
    return BuiltPrompt(b, "")


def _prompt_group(job, common, extra, output):
    group_files = extra.get("group_file_paths", [])
    file_filter = group_files or None
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the prior findings for YOUR files. "
        "Carry forward the ones still present; leave fixed ones out of `findings`."
    ), _INDEPENDENT_GROUP_CTX)
    prior_section = _build_prior_section(
        job.prior, prior_ctx,
        file_filter=file_filter,
        reply_threads=job.reply_threads,
    )
    holistic = extra.get("holistic_content", "")
    b = PromptBuilder(common)
    b.shared("issue_section", "env_section", "omitted_guidance", "max_turns")
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("pr_header", _build_pr_header(
        job.pr, job.ctx, job.effort, file_filter=file_filter,
    ))
    b.set("reply_threads", _build_reply_threads_section(job.reply_threads, file_filter=file_filter))
    b.set("project_context", build_project_context(job.preflight, file_filter=file_filter) if job.preflight else "")
    b.set("holistic_block", f"\n{holistic}" if holistic else "")
    b.set("prior_section", prior_section)
    b.set("group_idx", extra["group_idx"])
    b.set("group_count", extra["group_count"])
    b.set("group_name", extra["group_name"])
    b.set("group_files_formatted", extra["group_files_formatted"])
    b.set(
        "focus_section",
        _build_focus_section(
            areas_for_group(job.focus, group_files) if group_files else job.focus,
        ),
    )
    scope = set(group_files)
    paths = [p for p in unpaired_subjects(job.pr.files) if p in scope] if group_files else unpaired_subjects(job.pr.files)
    b.set("test_hint_section", _build_test_hint_section(paths))
    b.execution_claim_guard()
    files = [f for f in job.pr.files if f["path"] in scope] if group_files else job.pr.files
    b.output(output, output_format=_artifact_format(
        job, extra, rereview=bool(prior_section),
        has_behaviour_source=bool(hint_subjects(files)),
    ))
    b.fit(job, file_filter=file_filter, skip_project_context=True)
    return BuiltPrompt(b, str(extra["group_idx"]))


def _prompt_disprove(job, common, extra, output):
    b = PromptBuilder(common)
    b.shared("max_turns")
    b.set("review_content", extra.get("review_content", ""))
    b.output(output, output_format=_artifact_format(job, extra))
    return BuiltPrompt(b, "")


def build_common_sections(
    job: ReviewJob, *, max_turns: int, budget_bytes: int,
) -> CommonSections:
    return CommonSections(
        pr_header=_build_pr_header(
            job.pr, job.ctx, job.effort, viewer_role=job.viewer_role,
        ),
        state_context=_build_state_context_section(job),
        reviews_section=_build_reviews_section(job.ctx),
        reply_threads=_build_reply_threads_section(job.reply_threads),
        env_section=_build_env_section(job.wt_path, preflight=job.preflight),
        issue_section=_build_issue_section(job.issue_link, job.issue_context),
        focus_section=_build_focus_section(job.focus, derive=needs_derived(job.focus)),
        omitted_guidance=_build_omitted_guidance(
            job.preflight,
            skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
        ),
        max_turns=max_turns,
        budget_bytes=budget_bytes,
    )


class PromptTooLarge(RuntimeError):
    """A rendered prompt that exceeds the budget with every lever already pulled.

    Raised by `review.registry.build_prompt` after the prompt and its stats are
    written, so the oversized prompt is on disk to look at. The alternative —
    logging "EXCEEDS budget" and sending it anyway — spends a phase's cost on a
    request the model truncates or rejects, and reports whatever comes back as
    the phase's finding.
    """

    def __init__(
        self, template: str, prompt_bytes: int,
        budget_bytes: int = 0, model: str = "", token_overshoot: int = 0,
    ):
        self.template = template
        self.prompt_bytes = prompt_bytes
        self.budget_bytes = budget_bytes
        self.model = model
        self.token_overshoot = token_overshoot
        against = f"{budget_bytes // 1024}KB budget"
        if model:
            against += f" for {model}"
        if token_overshoot and prompt_bytes <= budget_bytes:
            # Bytes fit; the measured token count is what does not. Saying
            # "N KB against a larger M KB budget" would read as a contradiction.
            super().__init__(
                f"{template} prompt is {prompt_bytes // 1024}KB, within the "
                f"{against}, but {token_overshoot} tokens over the model "
                f"window, with every lever already pulled"
            )
            return
        super().__init__(
            f"{template} prompt is {prompt_bytes // 1024}KB against a "
            f"{against}, with every lever already pulled"
        )


