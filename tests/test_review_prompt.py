"""Tests for review_prompt: scoped prompt section builders."""

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.budget import (
    FileFit, MAX_DELTA_LIST_ENTRIES, MAX_PROMPT_BYTES, MIN_DIFF_BYTES,
    fit_files, fixed_preflight_bytes,
)
from review.grouping import ReviewProfile, ReviewRule, format_profiles_section
from review.collect import build_project_context, format_preflight_data
from gh.types import PRContext, PRMetadata
from review.types import (
    FindingRef, PreflightData, PriorDisposition, PriorFinding, ReviewJob,
)
from core.phases import Effort, Mode, Phase
from dataclasses import asdict

from unittest.mock import patch

from review.grammar import parse_ledger_line
from review.prompt import BudgetLever, Cut, _build_common_sections, _fit_budget
from review.prompt_prior import _LEDGER_INSTRUCTION, _build_unaccounted_section
from review.prompt_sections import (
    _build_ci_failure_items, _build_delta_section, _build_env_section,
    _build_omitted_guidance, _build_pr_header,
)
from review import registry as review_registry
from pr.ci_failures import FailureGroup, FailureItem, FailureKind, RunState
from pr.domains import CIDomain


# ── _build_delta_section with file_filter ──────────────────────────────────


def _make_preflight(**overrides):
    defaults = dict(
        diff="", commit_log="", file_contents={"a.py": "x", "b.py": "y"},
        file_permissions={}, claude_md="", architecture_md="",
        omitted_files=[],
        prior_head_sha="abc1234def",
        delta_files=["a.py", "b.py"],
        delta_commit_log="feat: stuff",
        delta_diff=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n"
        ),
    )
    defaults.update(overrides)
    return PreflightData(**defaults)


class TestBuildDeltaSectionScoped:
    def test_no_filter_includes_all(self):
        pf = _make_preflight()
        section = _build_delta_section(pf)
        assert "`a.py`" in section
        assert "`b.py`" in section
        assert "a/a.py" in section
        assert "a/b.py" in section

    def test_filter_scopes_delta_files(self):
        pf = _make_preflight()
        section = _build_delta_section(pf, file_filter=["a.py"])
        assert "`a.py`" in section
        assert "a/a.py" in section
        assert "a/b.py" not in section

    def test_filter_scopes_unchanged_to_filter_set(self):
        pf = _make_preflight(delta_files=["a.py"])
        section = _build_delta_section(pf, file_filter=["a.py", "b.py"])
        assert "### Files modified" in section
        assert "`a.py`" in section
        assert "### Files unchanged" in section
        assert "`b.py`" in section

    def test_filter_excludes_commit_log(self):
        pf = _make_preflight()
        unfiltered = _build_delta_section(pf)
        filtered = _build_delta_section(pf, file_filter=["a.py"])
        assert "feat: stuff" in unfiltered
        assert "feat: stuff" not in filtered

    def test_no_preflight_returns_empty(self):
        assert _build_delta_section(None) == ""
        assert _build_delta_section(None, file_filter=["a.py"]) == ""

    def test_no_prior_sha_returns_empty(self):
        pf = _make_preflight(prior_head_sha="")
        assert _build_delta_section(pf, file_filter=["a.py"]) == ""


class TestBuildDeltaSectionBounded:
    """The section cannot spend the prompt on lists or on the delta diff."""

    def test_file_lists_are_capped_and_say_so(self):
        """A rebase-inflated delta is a summary, not 5,000 lines of paths.

        The one that prompted this budgeted 4,974 delta files against a
        107-file PR: 260KB of `- \\`path\\`` lines, which pushed synthesis 75%
        past its budget on their own.
        """
        many = [f"pkg/f{i:05d}.go" for i in range(4_974)]
        pf = _make_preflight(delta_files=many, delta_diff="", file_contents={})
        section = _build_delta_section(pf)
        assert section.count("\n- `") == MAX_DELTA_LIST_ENTRIES
        assert f"+{4_974 - MAX_DELTA_LIST_ENTRIES} more not listed" in section
        assert len(section.encode()) < 20_000

    def test_max_bytes_shrinks_the_delta_diff(self):
        big = "".join(
            f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n-old\n+{'x' * 500}\n"
            for i in range(200)
        )
        pf = _make_preflight(delta_diff=big, delta_files=[f"f{i}.py" for i in range(200)])
        unbounded = _build_delta_section(pf)
        bounded = _build_delta_section(pf, max_bytes=40_000)
        assert len(bounded.encode()) < len(unbounded.encode())
        assert len(bounded.encode()) <= 40_000

    def test_no_room_drops_the_diff_rather_than_a_fragment(self):
        pf = _make_preflight()
        section = _build_delta_section(pf, max_bytes=100)
        assert "### Delta diff" not in section
        assert "Incremental review context" in section


# ── _build_pr_header with file_filter ──────────────────────────────────────


def _make_pr(**overrides):
    defaults = dict(
        title="Test PR", body="Description", head="feat", base="main",
        head_sha="abc123", additions=100, deletions=50, changed_files=3,
        files=[
            {"path": "a.py", "additions": 40, "deletions": 20},
            {"path": "b.py", "additions": 30, "deletions": 15},
            {"path": "c.py", "additions": 30, "deletions": 15},
        ],
    )
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _make_ctx(**overrides):
    defaults = dict(commits="abc feat: stuff")
    defaults.update(overrides)
    return PRContext(**defaults)


class TestBuildPrHeaderScoped:
    def test_no_filter_shows_full_size(self):
        header = _build_pr_header(_make_pr(), _make_ctx(), Effort.MEDIUM)
        assert "+100 -50 across 3 files" in header

    def test_filter_scopes_size_line(self):
        header = _build_pr_header(
            _make_pr(), _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "+40 -20 across 1 files" in header
        assert "of 3 total" in header

    def test_filter_scopes_file_breakdown(self):
        pr = _make_pr(additions=600, deletions=100)
        header = _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM, file_filter=["a.py", "b.py"],
        )
        assert "a.py" in header
        assert "b.py" in header
        assert "c.py" not in header

    def test_filter_always_includes_file_breakdown(self):
        pr = _make_pr(additions=10, deletions=5)
        header_unfiltered = _build_pr_header(pr, _make_ctx(), Effort.MEDIUM)
        assert "File breakdown" not in header_unfiltered

        header_filtered = _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "File breakdown" in header_filtered

    def test_filter_preserves_description_and_commits(self):
        header = _build_pr_header(
            _make_pr(), _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "Description" in header
        assert "feat: stuff" in header

    def test_low_effort_suppresses_a_breakdown_medium_would_show(self):
        """The same PR, classified by the preset."""
        pr = _make_pr(additions=600, deletions=150)
        assert "File breakdown" in _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM,
        )
        assert "File breakdown" not in _build_pr_header(
            pr, _make_ctx(), Effort.LOW,
        )


# ── _build_ci_failure_items ─────────────────────────────────────────────────


class TestBuildCiFailureItems:
    def _make_run(self, run_id=999):
        item = FailureItem(
            id="sc2086-bin-foo-42",
            annotation="SC2086: Double quote to prevent globbing",
            file="bin/foo.sh", line=42,
            diagnosis=None, fix_sha=None, outcome=None,
        )
        group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
        return RunState(
            run_id=run_id, run_number=1, head_sha="abc", status="completed",
            conclusion="failure", fetched_at="2026-08-12T00:00:00+00:00",
            failures={"shellcheck": group},
        )

    def test_returns_failure_items_for_the_latest_run(self):
        ci = CIDomain(latest_run_id=999, runs={999: self._make_run()})
        pr = _make_pr(files=[{"path": "bin/foo.sh", "additions": 1, "deletions": 0}])
        items = _build_ci_failure_items(ci, pr)
        assert items
        assert any("bin/foo.sh:42" in item for item in items)
        assert any("SC2086" in item for item in items)


# ── _fit_budget ─────────────────────────────────────────────────────────────


def _make_job(preflight=None, mode=Mode.PR):
    pr = PRMetadata(
        title="T", body="B", head="h", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 5}],
    )
    ctx = PRContext(commits="abc feat")
    return ReviewJob(
        repo="r", pr_number="1", pr=pr, ctx=ctx,
        wt_path="/tmp/w", review_file="/tmp/r.md",
        session_log="/tmp/l.jsonl",
        preflight=preflight, mode=mode,
    )


class TestFitBudget:
    def test_returns_remaining_when_within_budget(self):
        pf = _make_preflight(claude_md="", architecture_md="")
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"})
        assert plan.diff_bytes > MIN_DIFF_BYTES
        assert plan.cuts == ()
        assert plan.files.included == pf.file_contents

    def test_clamps_to_min_diff_by_default(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(claude_md=huge))
        plan = _fit_budget(job, {"header": "small"})
        assert plan.diff_bytes == MIN_DIFF_BYTES
        floored = [c for c in plan.cuts if c.lever is BudgetLever.DIFF_FLOOR]
        assert [c.floor_bytes for c in floored] == [MIN_DIFF_BYTES]
        assert floored[0].shortfall_bytes > 0

    def test_min_diff_zero_allows_zero_budget(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(claude_md=huge))
        plan = _fit_budget(job, {"header": "small"}, min_diff=0)
        assert plan.diff_bytes == 0
        # No floor to hold the diff at, so the cut is the whole of it.
        assert plan.cuts[-1].floor_bytes == 0
        assert "the full diff entirely" in plan.cuts[-1].describe()

    def test_skip_file_contents_frees_budget(self):
        pf = _make_preflight(file_contents={"gen.pb.go": "y" * 200_000})
        job = _make_job(pf)
        with_fc = _fit_budget(job, {"header": "small"}, file_filter=["gen.pb.go"])
        without_fc = _fit_budget(
            job, {"header": "small"}, file_filter=["gen.pb.go"], skip_file_contents=True,
        )
        assert without_fc.diff_bytes - with_fc.diff_bytes >= 200_000

    def test_file_contents_are_the_first_lever_and_are_named(self):
        """The lever the log used to report was the one already at zero.

        The group phase hand-rolled this drop and every other phase went
        without it, so a scout prompt with 452KB of pre-collected contents had
        no lever left and logged "diff capped to 20KB" — naming a section that
        was not the problem.
        """
        pf = _make_preflight(file_contents={"gen.pb.go": "y" * 400_000})
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"}, file_filter=["gen.pb.go"])
        assert not plan.files.any_included
        assert plan.cuts[0].lever is BudgetLever.FILE_CONTENTS
        assert plan.cuts[0].freed_bytes == 400_000
        assert plan.cuts[0].describe() == "390KB of pre-collected file contents (1 file)"

    def test_the_delta_is_cut_before_the_diff_is_floored(self):
        pf = _make_preflight(
            file_contents={},
            delta_files=[f"pkg/f{i:05d}.go" for i in range(4_974)],
            delta_diff="".join(
                f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n+{'x' * 900}\n"
                for i in range(400)
            ),
        )
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"})
        assert [c.lever for c in plan.cuts] == [BudgetLever.DELTA]
        assert plan.diff_bytes >= MIN_DIFF_BYTES
        # Everything the plan admits still fits, which is what the ladder is for.
        assert (
            len(plan.delta_section.encode()) + plan.diff_bytes
            <= MAX_PROMPT_BYTES
        )


class TestProfilesAreCountedByTheBudget:
    """A profile renders into the prompt, so a budget that ignores it overspends.

    `review_profiles` reaches the prompt through `format_preflight_data` ->
    `build_project_context` -> `format_profiles_section`, and was counted by
    neither `fixed_preflight_bytes` nor collect's `base_size`. Zero-cost while
    no repo declares a profile, which is why it went unnoticed.
    """

    def _profile(self, rule_bytes: int) -> ReviewProfile:
        return ReviewProfile(
            name="payments",
            description="money handling",
            paths=["**/*.py"],
            rules=[ReviewRule(severity="must-fix", rule="r" * rule_bytes)],
        )

    def test_a_profile_costs_the_diff_the_bytes_it_renders(self):
        job = _make_job(_make_preflight())
        without = _fit_budget(job, {"header": "small"})
        with_profile = _fit_budget(
            _make_job(_make_preflight(review_profiles=[self._profile(50_000)])),
            {"header": "small"},
        )
        assert without.diff_bytes - with_profile.diff_bytes >= 50_000

    def test_the_charge_is_the_rendered_section_not_the_rule_text(self):
        # The rendered section carries a heading and a preamble no rule holds,
        # so measuring the source text alone would still under-count.
        profile = self._profile(1_000)
        rendered = len(format_profiles_section([profile]).encode())
        job = _make_job(_make_preflight())
        without = _fit_budget(job, {"header": "small"})
        with_profile = _fit_budget(
            _make_job(_make_preflight(review_profiles=[profile])),
            {"header": "small"},
        )
        assert without.diff_bytes - with_profile.diff_bytes == rendered

    def test_no_profiles_costs_nothing(self):
        assert fixed_preflight_bytes("", "", "", {}, []) == 0
        assert fixed_preflight_bytes("", "", "", {}, None) == 0
        assert fixed_preflight_bytes("", "", "", {}) == 0

    def test_a_caller_that_rendered_the_context_is_not_charged_twice(self):
        """`_prompt_group` renders project context itself and registers it.

        Those bytes are already in `known_bytes`, so reserving the same
        sections again takes the difference out of the diff — the group phase
        would be poorer by exactly the rendered context for no reason.
        """
        pf = _make_preflight(
            claude_md="c" * 10_000, review_profiles=[self._profile(40_000)],
        )
        job = _make_job(pf)
        ctx = build_project_context(pf)
        as_group = _fit_budget(
            job, {"project_context": ctx},
            skip_file_contents=True, skip_project_context=True,
        )
        unrendered = _fit_budget(job, {}, skip_file_contents=True)
        # Not exactly equal: the reserve counts the sections, while the group
        # registers the rendered context, which adds `build_project_context`'s
        # own heading and separators on top. That wrapper is real prompt bytes
        # the reserve never counted, so the group pays a little more — tens of
        # bytes against the ~50KB it was previously charged twice for.
        wrapper = len(ctx.encode()) - fixed_preflight_bytes(
            "", pf.claude_md, pf.architecture_md, pf.review_checklists,
            pf.review_profiles,
        )
        assert 0 < wrapper < 1024
        assert unrendered.diff_bytes - as_group.diff_bytes == wrapper

    def test_the_double_count_was_the_whole_reserve(self):
        # Pins the size of the bug being fixed, so a regression is legible as
        # "the group phase lost the context back" rather than a stray number.
        pf = _make_preflight(
            claude_md="c" * 10_000, review_profiles=[self._profile(40_000)],
        )
        job = _make_job(pf)
        ctx = build_project_context(pf)
        charged_twice = _fit_budget(
            job, {"project_context": ctx}, skip_file_contents=True,
        )
        once = _fit_budget(
            job, {"project_context": ctx},
            skip_file_contents=True, skip_project_context=True,
        )
        reserve = fixed_preflight_bytes(
            pf.commit_log, pf.claude_md, pf.architecture_md,
            pf.review_checklists, pf.review_profiles,
        )
        assert reserve > 50_000
        assert once.diff_bytes - charged_twice.diff_bytes == reserve


class TestThePlanIsCheckedAgainstTheRender:
    """The residual is how an unmeasured section becomes findable.

    A budget that does not measure everything it sends bounds nothing, and the
    way that failure presents is an over-budget render nobody can account for.
    """

    def test_a_plan_reports_what_it_expects_the_render_to_cost(self):
        job = _make_job(_make_preflight())
        plan = _fit_budget(job, {"header": "small"})
        assert plan.planned_bytes > 0

    def test_the_plan_counts_every_section_it_measured(self):
        pf = _make_preflight(claude_md="c" * 5_000, commit_log="l" * 3_000)
        plan = _fit_budget(_make_job(pf), {"header": "h" * 2_000})
        # Known sections, unshrinkable preflight, and the room handed to the
        # two capped sections — the flat reserve is not part of the estimate,
        # since nothing renders it.
        expected = (
            2_000 + 5_000 + 3_000
            + len("xy".encode())
            + len(plan.delta_section.encode())
            + plan.diff_bytes
        )
        assert plan.planned_bytes == expected

    def test_a_phase_that_never_fits_plans_nothing(self):
        # Disprove builds no budgeted section, so there is no plan to compare a
        # render against and no residual worth recording.
        from review.prompt import PromptBuilder
        job = _make_job(_make_preflight())
        b = PromptBuilder(_build_common_sections(job, max_turns=10))
        assert b.planned_bytes == 0


class TestBudgetKeepsTheFilesItCanAfford:
    """An over-ceiling prompt drops the lowest-ranked files, not all of them.

    The collector already ranked the files by tier and size and kept what fit.
    When the prompt went over anyway, the ladder threw the whole collection
    away — including the files there was still room for — so a large PR was
    reviewed with no file contents at all rather than with most of them.
    """

    def test_a_partial_drop_keeps_what_still_fits(self):
        # One file that cannot fit beside the others, and two small ones that can.
        pf = _make_preflight(file_contents={
            "huge.py": "x" * (MAX_PROMPT_BYTES - 50_000),
            "small_a.py": "y" * 1_000,
            "small_b.py": "z" * 1_000,
        })
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        assert plan.files.included, "the ladder dropped every file"
        assert "huge.py" in plan.files.omitted
        assert set(plan.files.included) == {"small_a.py", "small_b.py"}

    def test_the_cut_counts_the_files_it_dropped(self):
        pf = _make_preflight(file_contents={
            "huge.py": "x" * (MAX_PROMPT_BYTES - 50_000),
            "small_a.py": "y" * 1_000,
        })
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        cut = next(c for c in plan.cuts if c.lever is BudgetLever.FILE_CONTENTS)
        assert cut.dropped_files == 1
        assert "1 file" in cut.describe()

    def test_a_dropped_file_is_named_to_the_agent(self):
        """A file the budget drops joins the list the prompt tells the agent to read.

        Dropping contents without naming them leaves the agent told its files
        are in the prompt and shown neither them nor their names.
        """
        pf = _make_preflight(file_contents={
            "huge.py": "x" * (MAX_PROMPT_BYTES - 50_000),
            "small_a.py": "y" * 1_000,
        })
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        text = format_preflight_data(pf, files=plan.files)
        assert "- huge.py" in text
        assert "Files not pre-collected" in text

    def test_nothing_fitting_is_still_a_clean_drop(self):
        pf = _make_preflight(file_contents={"huge.py": "x" * (MAX_PROMPT_BYTES * 2)})
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        assert plan.files.included == {}
        assert not plan.files.any_included


class TestCutSurvivesTheJournal:
    """A cut is data first and a sentence second.

    `prompt-stats.json` is the artifact an over-budget run is diagnosed from, so
    a reader asking which lever fired on which phase reads a field rather than
    parsing the log line.
    """

    def test_every_lever_describes_itself(self):
        described = {
            lever: Cut(lever, freed_bytes=4096, shortfall_bytes=2048, floor_bytes=1024).describe()
            for lever in BudgetLever
        }
        assert len(set(described.values())) == len(BudgetLever)
        assert all(d for d in described.values())

    def test_the_journalled_form_is_addressable_by_field(self):
        cut = Cut(BudgetLever.FILE_CONTENTS, freed_bytes=400_000)
        assert json.loads(json.dumps(asdict(cut))) == {
            "lever": "file_contents",
            "freed_bytes": 400_000,
            "shortfall_bytes": 0,
            "floor_bytes": 0,
            "dropped_files": 0,
        }


# ── Dropped file contents are declared ──────────────────────────────────────


class TestDroppedContentsAreDeclared:
    """What the budget drops, the prompt has to admit to dropping.

    Dropping the contents used to drop the list of them along with it, while
    the environment section went on saying "File contents and diffs are in the
    Pre-collected data section" — so the agent was told its files were in the
    prompt and shown neither them nor their names.
    """

    def test_skipping_contents_still_names_the_files(self):
        pf = _make_preflight(file_contents={"a.py": "x", "b.py": "y"})
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        text = format_preflight_data(pf, files=dropped_all)
        assert "### Changed file contents" not in text
        assert "### Files not pre-collected (read directly)" in text
        assert "- a.py" in text
        assert "- b.py" in text

    def test_skipping_contents_does_not_double_list_omitted_files(self):
        pf = _make_preflight(file_contents={"a.py": "x"}, omitted_files=["big.go"])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        text = format_preflight_data(pf, files=dropped_all)
        assert text.count("- big.go") == 1
        assert "- a.py" in text

    def test_env_section_sends_the_agent_to_the_worktree(self):
        pf = _make_preflight()
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        kept = _build_env_section("/tmp/w", preflight=pf)
        dropped = _build_env_section("/tmp/w", preflight=pf, files=dropped_all)
        assert "File contents and diffs are in the Pre-collected data section" in kept
        assert "file contents are not" in dropped
        assert "Files not pre-collected" in dropped

    def test_nothing_to_fit_is_not_a_drop(self):
        """An empty fit with nothing omitted is not the same as a fit that lost everything.

        `_fit_budget` starts every plan at `FileFit(scoped, ..., [])`, so a
        `file_filter` scoping a section to zero collected files produces this
        exact shape with no budget cut involved. Reading it as "dropped
        everything" tells the agent to batch-read a "Files not pre-collected"
        list that was never populated.
        """
        pf = _make_preflight(file_contents={}, omitted_files=[])
        nothing_to_fit = FileFit({}, {}, [])
        text = _build_env_section("/tmp/w", preflight=pf, files=nothing_to_fit)
        assert "File contents and diffs are in the Pre-collected data section" in text
        assert "file contents are not" not in text

    def test_omitted_guidance_asks_for_the_batch_read(self):
        pf = _make_preflight(omitted_files=[])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        assert _build_omitted_guidance(pf) == ""
        assert "Files not pre-collected" in _build_omitted_guidance(
            pf, files=dropped_all,
        )

    def test_skip_omitted_does_not_silence_dropped_contents(self):
        """The effort preset declines the large files, not every file."""
        pf = _make_preflight(omitted_files=["big.go"])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        assert "not reviewed at this effort level" in _build_omitted_guidance(
            pf, skip_omitted=True,
        )
        assert "Files not pre-collected" in _build_omitted_guidance(
            pf, skip_omitted=True, files=dropped_all,
        )


# ── Shared prompt bodies ────────────────────────────────────────────────────


class TestSharedPromptBodies:
    """The paired prompts must stay interchangeable apart from their variant."""

    def _vars(self, phase, output, mode=Mode.PR, **extra):
        job = _make_job(_make_preflight(), mode=mode)
        common = _build_common_sections(job, max_turns=10)
        built = review_registry.for_phase(phase).build(job, common, extra, output)
        return built.builder.vars

    def test_scout_and_holistic_are_the_same_prompt(self):
        """One builder, two artifacts: the phase spec is the whole difference.

        The two are alternative first passes over identical inputs, so sharing
        the builder is what keeps them that way — a section added for one is
        added for both, and the file each writes is the spec's answer.
        """
        assert (
            review_registry.for_phase(Phase.HOLISTIC).build
            is review_registry.for_phase(Phase.SCOUT).build
        )
        holistic = self._vars(Phase.HOLISTIC, "/tmp/h.md")
        scout = self._vars(Phase.SCOUT, "/tmp/s.md")
        assert holistic.keys() == scout.keys()
        differing = [k for k in holistic if holistic[k] != scout[k]]
        assert differing == ["output_block"]

    def test_single_variants_differ_only_in_identity_and_the_verdict(self):
        pr = self._vars(Phase.SINGLE, "/tmp/r.md")
        self_ = self._vars(Phase.SINGLE, "/tmp/r.md", mode=Mode.SELF)
        assert set(pr) - set(self_) == {
            "pr_number", "reviews_section", "verdict_options",
        }
        assert set(self_) - set(pr) == {"branch_name"}
        # The re-review preamble is worded per mode, so it is expected to differ.
        common_keys = (set(pr) & set(self_)) - {"prior_section"}
        assert all(pr[k] == self_[k] for k in common_keys)

    def test_synthesis_variants_differ_only_in_identity_and_prior_reviews(self):
        shared = dict(group_count=2, merged_content="m", holistic_content="h")
        pr = self._vars(Phase.SYNTHESIS, "/tmp/r.md", **shared)
        self_ = self._vars(Phase.SYNTHESIS, "/tmp/r.md", mode=Mode.SELF, **shared)
        assert set(pr) - set(self_) == {"pr_number", "pr_title", "reviews_section"}
        assert set(self_) - set(pr) == {"branch_name"}
        common_keys = set(pr) & set(self_)
        assert all(pr[k] == self_[k] for k in common_keys)


# ── An over-budget prompt is refused, not logged past ───────────────────────


class TestBuildPromptRefusesAnOversizedPrompt:
    # CLAUDE.md is fixed overhead — no lever reaches it — so one over the whole
    # budget puts the prompt past it whatever the ladder cuts.
    UNBUDGETABLE = "x" * (MAX_PROMPT_BYTES + 1000)

    def _job(self, tmp_path, **preflight):
        job = _make_job(_make_preflight(**preflight))
        job.review_file = str(tmp_path / "review.md")
        return job

    def test_a_prompt_over_the_budget_raises(self, tmp_path):
        from review.prompt import PromptTooLarge
        from review.registry import build_prompt

        job = self._job(tmp_path, claude_md=self.UNBUDGETABLE)
        with pytest.raises(PromptTooLarge) as exc:
            build_prompt(Phase.SCOUT, job, max_turns=10)
        assert exc.value.prompt_bytes > MAX_PROMPT_BYTES

    def test_the_oversized_prompt_is_on_disk_to_look_at(self, tmp_path):
        """The stats are written before the raise, so the run is diagnosable."""
        from review.prompt import PromptTooLarge
        from review.registry import build_prompt

        job = self._job(tmp_path, claude_md=self.UNBUDGETABLE)
        with pytest.raises(PromptTooLarge):
            build_prompt(Phase.SCOUT, job, max_turns=10)
        stats = json.loads((tmp_path / "prompt-stats.json").read_text())
        assert stats[-1]["prompt_bytes"] > MAX_PROMPT_BYTES
        assert (tmp_path / "prompt-scout.md").exists()

    def test_an_ordinary_prompt_still_renders(self, tmp_path):
        from review.registry import build_prompt

        prompt = build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        assert len(prompt.encode()) <= MAX_PROMPT_BYTES
        assert "Incremental review context" in prompt


# ── Token telemetry in the prompt stats ─────────────────────────────────────


class TestPromptTokenTelemetry:
    """What `prompt-stats.json` records about a prompt's real token cost.

    The counter is a network round trip, so it is patched here; what these
    hold is the contract around it — that it is off unless asked for, that an
    unavailable count leaves no trace rather than a wrong one, and that a count
    is never recorded without the tokenizer it was measured against.
    """

    def _job(self, tmp_path):
        job = _make_job(_make_preflight())
        job.review_file = str(tmp_path / "review.md")
        return job

    def _stats(self, tmp_path):
        return json.loads((tmp_path / "prompt-stats.json").read_text())[-1]

    def test_measured_by_default(self, tmp_path, monkeypatch):
        """A measurement nobody takes calibrates nothing.

        This shipped opt-in and was never switched on: 1,758 recorded renders
        carry no token count at all. The default is now on, so the density the
        budget reasons about comes from real reviews.
        """
        monkeypatch.delenv("WORKBENCH_AI_MEASURE_TOKENS", raising=False)
        with patch("review.prompt.count_tokens", return_value=1000) as counter:
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        counter.assert_called_once()
        assert self._stats(tmp_path)["prompt_tokens"] == 1000

    def test_zero_opts_out(self, tmp_path, monkeypatch):
        """The round trip is small but not free, and a run may decline it."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "0")
        with patch("review.prompt.count_tokens") as counter:
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        counter.assert_not_called()
        assert "prompt_tokens" not in self._stats(tmp_path)

    def test_records_count_model_and_density_when_measured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=1000):
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert stats["prompt_tokens"] == 1000
        # A density without its tokenizer is not interpretable: sonnet-5 counts
        # the same text ~27% denser than sonnet-4-5.
        assert stats["token_model"]
        assert stats["bytes_per_token"] == round(stats["prompt_bytes"] / 1000, 3)

    def test_an_unavailable_count_records_nothing(self, tmp_path, monkeypatch):
        """None is not zero — a missing measurement must leave no density behind."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=None):
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert "prompt_tokens" not in stats
        assert "bytes_per_token" not in stats

    def test_a_zero_count_records_no_density(self, tmp_path, monkeypatch):
        """Zero is a count, but it is not a density — and must not divide."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=0):
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert stats["prompt_tokens"] == 0
        assert "bytes_per_token" not in stats

    def test_counts_against_the_model_the_phase_will_use(self, tmp_path, monkeypatch):
        """A count against another tokenizer is worse than no count at all."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=10) as counter:
            review_registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        model = counter.call_args.args[1]
        assert model == self._stats(tmp_path)["token_model"]


# ── The prior findings handed to synthesis to settle ────────────────────────


def _passed_over(finding_id, path, text):
    return PriorFinding(FindingRef(finding_id, path), "sid", text)


class TestUnaccountedPriorSection:
    """Synthesis's `prior_section` is the remainder, not the prior review.

    The group agents were each shown their slice of the prior review and their
    conclusions are already in the merged content. What reaches synthesis is
    what none of them accounted for — the last point in the run where an agent
    can still decide it.
    """

    M1 = _passed_over(
        "M1", "handler.go",
        "- **[M1]** **`handler.go:42`** — `rows, _ := db.Query(sql)` drops the error",
    )

    def test_nothing_passed_over_renders_no_section(self):
        assert _build_unaccounted_section([]) == ""

    def test_a_finding_arrives_with_the_text_that_reported_it(self):
        section = _build_unaccounted_section([self.M1])
        assert "**[M1]**" in section
        assert "`rows, _ := db.Query(sql)` drops the error" in section

    def test_the_section_asks_for_the_ledger_the_parser_reads(self):
        """One owner for the ledger's shape — this section states none of its own."""
        assert _LEDGER_INSTRUCTION in _build_unaccounted_section([self.M1])

    def test_the_findings_come_before_the_instruction_that_says_above(self):
        section = _build_unaccounted_section([self.M1])
        assert section.index("</unaccounted_findings>") < section.index(_LEDGER_INSTRUCTION)

    def test_the_synthesis_prompt_carries_them(self):
        job = _make_job(_make_preflight())
        common = _build_common_sections(job, max_turns=10)
        extra = dict(
            group_count=1, merged_content="m", holistic_content="h",
            unaccounted_prior=[self.M1],
        )
        built = review_registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert "drops the error" in built.builder.vars["prior_section"]

    def test_a_synthesis_prompt_with_nothing_left_over_says_nothing(self):
        job = _make_job(_make_preflight())
        common = _build_common_sections(job, max_turns=10)
        extra = dict(group_count=1, merged_content="m", holistic_content="h")
        built = review_registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert built.builder.vars["prior_section"] == ""


# ── The ledger instruction and the ledger parser ────────────────────────────


class TestLedgerInstructionParses:
    """The form asked for and the form accepted cannot drift apart.

    The instruction is the only description of the ledger an agent ever reads,
    and reconciliation is the only thing that reads what it writes back. An
    example the parser rejects is therefore invisible until a whole re-review's
    bookkeeping is lost, which is what happened when the verdicts the reviews
    wrote ended in a full stop and the examples all used an em dash.
    """

    def _examples(self):
        """The ledger lines the instruction shows, as an agent would copy them.

        Each is a backticked span whose own backticks are escaped, so the one
        that closes it is the first that no backslash precedes.
        """
        spans = (re.match(r"^- `(.+?)(?<!\\)`", line)
                 for line in _LEDGER_INSTRUCTION.split("\n"))
        return [m.group(1).replace("\\`", "`") for m in spans if m]

    def test_the_instruction_shows_every_verdict(self):
        shown = [e for e in self._examples() for d in PriorDisposition if d.value in e]
        assert len(shown) == len(list(PriorDisposition))

    def test_every_example_parses_to_the_verdict_it_names(self):
        examples = self._examples()
        assert examples, "the instruction shows no ledger lines"
        for example in examples:
            entry = parse_ledger_line(example)
            assert entry, f"the instruction's example does not parse: {example}"
            assert entry.disposition and entry.disposition.value in example
