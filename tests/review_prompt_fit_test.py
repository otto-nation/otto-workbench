"""Tests for review.prompt._fit_budget: the ladder of cuts that fits a prompt to its budget."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.budget import MIN_DIFF_BYTES, fixed_preflight_bytes
from review.grouping import ReviewProfile, ReviewRule, format_profiles_section
from review.collect import (
    _COMMIT_LOG_TRIMMED, build_project_context, format_preflight_data,
    trim_commit_log,
)
from dataclasses import asdict

from review.prompt import BudgetLever, Cut, build_common_sections
from review.prompt import _fit_budget as _fit_budget_impl

from review_prompt_support import MAX_PROMPT_BYTES, _make_preflight, _make_job


# ── _fit_budget ─────────────────────────────────────────────────────────────


def _fit_budget(job, known_sections, **kw):
    """`review.prompt._fit_budget` with this file's default budget applied.

    Shadows the real name deliberately: the budget is a required argument now,
    and two dozen call sites here care about the ladder rather than about which
    ceiling it ran against. A test that cares passes `budget_bytes` itself.
    """
    kw.setdefault("budget_bytes", MAX_PROMPT_BYTES)
    return _fit_budget_impl(job, known_sections, **kw)


class TestFitBudget:
    def test_returns_remaining_when_within_budget(self):
        pf = _make_preflight(instructions_md="", architecture_md="")
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"})
        assert plan.diff_allowance_bytes > MIN_DIFF_BYTES
        assert plan.cuts == ()
        assert plan.files.included == pf.file_contents

    def test_clamps_to_min_diff_by_default(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(instructions_md=huge))
        plan = _fit_budget(job, {"header": "small"})
        assert plan.diff_allowance_bytes == MIN_DIFF_BYTES
        floored = [c for c in plan.cuts if c.lever is BudgetLever.DIFF_FLOOR]
        assert [c.floor_bytes for c in floored] == [MIN_DIFF_BYTES]
        assert floored[0].shortfall_bytes > 0

    def test_min_diff_zero_allows_zero_budget(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(instructions_md=huge))
        plan = _fit_budget(job, {"header": "small"}, min_diff=0)
        assert plan.diff_allowance_bytes == 0
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
        assert without_fc.diff_allowance_bytes - with_fc.diff_allowance_bytes >= 200_000

    def test_file_contents_are_the_first_lever_and_are_named(self):
        """The lever the log used to report was the one already at zero.

        The group phase hand-rolled this drop and every other phase went
        without it, so a scout prompt with 452KB of pre-collected contents had
        no lever left and logged "diff capped to 20KB" — naming a section that
        was not the problem.
        """
        contents = MAX_PROMPT_BYTES - 10_000
        pf = _make_preflight(file_contents={"gen.pb.go": "y" * contents})
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"}, file_filter=["gen.pb.go"])
        assert not plan.files.any_included
        assert plan.cuts[0].lever is BudgetLever.FILE_CONTENTS
        assert plan.cuts[0].freed_bytes == contents
        assert plan.cuts[0].describe() == (
            f"{contents // 1024}KB of pre-collected file contents (1 file)"
        )

    def test_the_delta_is_cut_before_the_diff_is_floored(self):
        # Enough delta hunks to exceed the budget on their own, so the lever
        # has to fire. The count is derived rather than literal: a fixed one
        # silently stops binding the next time the ceiling moves.
        hunk = f"diff --git a/f0.py b/f0.py\n@@ -1 +1 @@\n+{'x' * 900}\n"
        hunks = MAX_PROMPT_BYTES // len(hunk.encode()) + 100
        pf = _make_preflight(
            file_contents={},
            delta_files=[f"pkg/f{i:05d}.go" for i in range(4_974)],
            delta_diff="".join(
                f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n+{'x' * 900}\n"
                for i in range(hunks)
            ),
        )
        job = _make_job(pf)
        plan = _fit_budget(job, {"header": "small"})
        assert [c.lever for c in plan.cuts] == [BudgetLever.DELTA]
        assert plan.diff_allowance_bytes >= MIN_DIFF_BYTES
        # Everything the plan admits still fits, which is what the ladder is for.
        assert (
            len(plan.delta_section.encode()) + plan.diff_allowance_bytes
            <= MAX_PROMPT_BYTES
        )

    def test_commit_log_trims_newest_first_before_the_diff_floor(self):
        oldest = "commit aaa\n" + ("o" * 4_000) + "\n\n"
        newest = "commit zzz\n" + ("n" * 200) + "\n\n"
        log = oldest + newest
        pf = _make_preflight(
            commit_log=log, file_contents={}, instructions_md="",
            delta_diff="", delta_files=[], prior_head_sha="",
        )
        # Room for the newest commit and nothing else, so the lever has to fire.
        plan = _fit_budget(
            _make_job(pf), {"header": "x"},
            budget_bytes=len(newest.encode()) + len(_COMMIT_LOG_TRIMMED.encode()) + 50,
            min_diff=0,
        )
        assert any(c.lever is BudgetLever.COMMIT_LOG for c in plan.cuts)
        assert "zzz" in plan.commit_log
        assert "aaa" not in plan.commit_log
        assert plan.commit_log.startswith(_COMMIT_LOG_TRIMMED)
        assert plan.commit_log.endswith(newest)


class TestTrimCommitLog:
    def _log(self):
        return "".join(
            f"commit c{i}\n{'x' * 100}\n\n" for i in range(5)
        )

    def test_a_trimmed_log_keeps_the_same_oldest_first_order_as_an_untrimmed_one(self):
        log = self._log()
        assert trim_commit_log(log, len(log.encode())) == log
        trimmed = trim_commit_log(log, 330 + len(_COMMIT_LOG_TRIMMED.encode()))
        assert trimmed.index("commit c3") < trimmed.index("commit c4")
        assert "commit c0" not in trimmed

    def test_a_trimmed_log_says_commits_were_dropped_and_fits_the_cap(self):
        log = self._log()
        cap = 330 + len(_COMMIT_LOG_TRIMMED.encode())
        trimmed = trim_commit_log(log, cap)
        assert trimmed.startswith(_COMMIT_LOG_TRIMMED)
        assert len(trimmed.encode()) <= cap

    def test_an_oversized_newest_commit_keeps_its_head(self):
        log = "commit old\nold\n\ncommit new\n" + "HEAD" + "y" * 5_000
        cap = 200
        trimmed = trim_commit_log(log, cap)
        assert "commit new\nHEAD" in trimmed
        assert "commit old" not in trimmed
        assert len(trimmed.encode()) <= cap


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
        assert without.diff_allowance_bytes - with_profile.diff_allowance_bytes >= 50_000

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
        assert without.diff_allowance_bytes - with_profile.diff_allowance_bytes == rendered

    def test_no_profiles_costs_nothing(self):
        assert fixed_preflight_bytes("", "", {}, []) == 0
        assert fixed_preflight_bytes("", "", {}, None) == 0
        assert fixed_preflight_bytes("", "", {}) == 0

    def test_a_caller_that_rendered_the_context_is_not_charged_twice(self):
        """`_prompt_group` renders project context itself and registers it.

        Those bytes are already in `known_bytes`, so reserving the same
        sections again takes the difference out of the diff — the group phase
        would be poorer by exactly the rendered context for no reason.
        """
        pf = _make_preflight(
            instructions_md="c" * 10_000, review_profiles=[self._profile(40_000)],
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
            pf.instructions_md, pf.architecture_md, pf.review_checklists,
            pf.review_profiles,
        )
        assert 0 < wrapper < 1024
        assert unrendered.diff_allowance_bytes - as_group.diff_allowance_bytes == wrapper

    def test_the_double_count_was_the_whole_reserve(self):
        # Pins the size of the bug being fixed, so a regression is legible as
        # "the group phase lost the context back" rather than a stray number.
        pf = _make_preflight(
            instructions_md="c" * 10_000, review_profiles=[self._profile(40_000)],
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
            pf.instructions_md, pf.architecture_md,
            pf.review_checklists, pf.review_profiles,
        )
        assert reserve > 50_000
        assert once.diff_allowance_bytes - charged_twice.diff_allowance_bytes == reserve


class TestThePlanIsCheckedAgainstTheRender:
    """The unaccounted bytes are how an unmeasured section becomes findable.

    A budget that does not measure everything it sends bounds nothing, and the
    way that failure presents is an over-budget render nobody can account for.
    Charging the diff at its allowance rather than its spend is what made the
    figure useless: every healthy render read a quarter of a megabyte under,
    so a kilobyte of genuine excess could never surface.
    """

    def test_a_plan_reports_what_it_expects_the_render_to_cost(self):
        job = _make_job(_make_preflight())
        plan = _fit_budget(job, {"header": "small"})
        assert plan.measured_bytes > 0

    def test_the_plan_counts_every_section_it_measured(self):
        pf = _make_preflight(instructions_md="c" * 5_000, commit_log="l" * 3_000)
        plan = _fit_budget(_make_job(pf), {"header": "h" * 2_000})
        # Known sections, unshrinkable preflight, the file contents and the
        # delta — every section that renders at the size it was measured at.
        # The diff is not among them: it renders at whatever it costs, up to
        # its cap. The flat reserve is absent too, since nothing renders it.
        expected = (
            2_000 + 5_000 + 3_000
            + len("xy".encode())
            + len(plan.delta_section.encode())
        )
        assert plan.measured_bytes == expected

    def test_the_allowance_is_every_measured_section_plus_the_diffs_cap(self):
        plan = _fit_budget(_make_job(_make_preflight()), {"header": "small"})
        assert plan.allowance_bytes == plan.measured_bytes + plan.diff_allowance_bytes

    def test_the_diff_is_charged_what_it_rendered_not_what_it_was_allowed(self):
        """An allowance the diff never spends is not a cost the render incurred.

        This is the regression: pairing the plan against the cap made every
        real record hundreds of kilobytes negative, so the metric could report
        slack it did not need and never the excess it was added to detect.
        """
        plan = _fit_budget(_make_job(_make_preflight()), {"header": "small"})
        assert plan.diff_allowance_bytes > 1_000

        acc = plan.reconcile(1_000)
        assert acc.accounted_bytes == plan.measured_bytes + 1_000
        assert acc.accounted_bytes < acc.allowance_bytes

    def test_a_diff_that_overspends_its_allowance_shows_a_positive_excess(self):
        """The small positive excess is the whole point of the figure.

        A context too large to budget floors the diff's allowance at zero, and
        the diff still renders its truncation notice — so the render costs a
        few dozen bytes the ladder did not authorise. Harmless in itself, and
        the cheapest available proof that an excess is visible at all.
        """
        pf = _make_preflight(
            diff="diff --git a/a.py b/a.py\n-old\n+new\n",
            instructions_md="c" * MAX_PROMPT_BYTES,
        )
        plan = _fit_budget(_make_job(pf), {"header": "small"}, min_diff=0)
        assert plan.diff_allowance_bytes == 0

        block = format_preflight_data(pf, max_diff_bytes=plan.diff_allowance_bytes)
        assert block.rendered_diff_bytes > 0
        assert plan.reconcile(block.rendered_diff_bytes).accounted_bytes > plan.allowance_bytes

    def test_a_phase_that_never_fits_has_no_accounting(self):
        # Disprove builds no budgeted section, so there is no plan to compare a
        # render against and nothing worth recording.
        from review.prompt import PromptBuilder
        job = _make_job(_make_preflight())
        b = PromptBuilder(build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES))
        assert b.accounting is None


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
            "huge.py": "x" * (MAX_PROMPT_BYTES - 2_000),
            "small_a.py": "y" * 1_000,
            "small_b.py": "z" * 1_000,
        })
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        assert plan.files.included, "the ladder dropped every file"
        assert "huge.py" in plan.files.omitted
        assert set(plan.files.included) == {"small_a.py", "small_b.py"}

    def test_the_cut_counts_the_files_it_dropped(self):
        pf = _make_preflight(file_contents={
            "huge.py": "x" * (MAX_PROMPT_BYTES - 2_000),
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
            "huge.py": "x" * (MAX_PROMPT_BYTES - 2_000),
            "small_a.py": "y" * 1_000,
        })
        plan = _fit_budget(_make_job(pf), {"header": "small"})

        text = format_preflight_data(pf, files=plan.files).text
        assert "- huge.py" in text
        assert "Files not pre-collected" in text

    def test_the_header_sends_the_agent_to_the_omitted_files(self):
        """The do-not-re-read header has to admit the exception below it.

        Scoping Read/Bash to files outside the PR contradicts the omitted
        section and the turn-budget guidance, both of which tell the agent to
        read files that are in it.
        """
        header = format_preflight_data(
            _make_preflight(omitted_files=["big.go"]),
        ).text.split("### Full diff")[0]
        assert "files named under \"Files not pre-collected\"" in header

        nothing_omitted = format_preflight_data(
            _make_preflight(omitted_files=[]),
        ).text.split("### Full diff")[0]
        assert "Files not pre-collected" not in nothing_omitted

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


class TestTheLadderDoesNotReserveTwice:
    """A reserve for sections the ladder already measures is budget nobody spends.

    The flat 120KB reserve covered the template, the PR header, prior reviews
    and reply threads — every one of which `known_bytes` measures exactly — so
    on a typical prompt it held back ~116KB the review had room for and never
    used.
    """

    def test_measured_sections_are_charged_once(self):
        pf = _make_preflight(instructions_md="", architecture_md="")
        grew_by = 50_000
        small = _fit_budget(_make_job(pf), {"header": ""})
        large = _fit_budget(_make_job(pf), {"header": "h" * grew_by})

        # The header comes out of the diff's share exactly once: charged twice
        # the difference would be 100_000, and not at all it would be 0.
        lost = small.diff_allowance_bytes - large.diff_allowance_bytes
        assert lost == grew_by
