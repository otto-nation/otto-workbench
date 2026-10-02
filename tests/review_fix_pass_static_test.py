"""Tests for the review fix pass's second work stream: static analysis
violations handed to the agent, declines carried into the next round, and
the `## Static Analysis` section rewritten to what the pass did.
"""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.fix
import review.paths
from pr.fix import FixOutcome
from review.static_analysis import (
    CheckerResult, StaticViolation, format_static_analysis,
)

from review_fix_pass_support import git_wt, _make_job, _run, _outcome


def _violation(
    vid: str, path: str = "a.py", line: int = 1,
    message: str = "depth 5 exceeds limit 4", **kwargs,
) -> StaticViolation:
    return StaticViolation(file=path, line=line, message=message, id=vid, **kwargs)


def _static_results(
    *violations: StaticViolation, scoped: bool = True,
) -> list[CheckerResult]:
    """Violations in the shape `run_static_analysis` hands to the job.

    `scoped` defaults True — the shape the orchestrator produces when it could
    read the branch's diff, which is the ordinary case and the only one that
    yields work.
    """
    return [CheckerResult(
        name="Nesting depth", violations=list(violations),
        files_checked=len({v.file for v in violations}),
        scoped_to_added=scoped,
    )]


# ── the second work stream: static analysis violations ──────────────────────


class TestStaticViolationsAsWork:
    """The checkers' violations are work the pass takes, not just a note.

    They arrive on the job from the run that wrote the `## Static Analysis`
    section, so the pass works the list a reader sees rather than re-measuring
    a tree that section has already described.
    """

    def test_a_violation_becomes_an_item_the_agent_is_handed(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        job.static_results = _static_results(_violation("SA1", "src.py", 12))
        inv = _run(job, {"SA1": "fixed"})

        assert "<!-- fix:SA1 -->" in inv.call_args.args[1]

    @pytest.mark.parametrize(
        "vid", ["SA1", ""], ids=["duplicate-id", "both-empty-id"],
    )
    def test_a_repeated_violation_id_is_warned_about_not_silently_dropped(
        self, git_wt, tmp_path, capsys, vid,
    ):
        """A hand-built list going straight through the constructor, not the
        documented `run_static_analysis` path, can carry a repeated id — and an
        empty id collapses the same way, since two unaddressable violations are
        also two violations sharing one key. The dict `items()` reads from
        keeps only the last of the two either way; this asserts that loss is
        reported rather than silent, for both id shapes.
        """
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        adapter = review.fix.ReviewFixAdapter(
            job, [],
            violations=[
                _violation(vid, "src.py", 1, message="first"),
                _violation(vid, "src.py", 2, message="second"),
            ],
        )

        assert len(adapter.items()) == 1
        assert "Duplicate static violation ids" in capsys.readouterr().err

    def test_a_violation_off_the_branch_is_still_in_scope(self, git_wt, tmp_path):
        """The defensive union in `_anchor_files`, exercised rather than assumed.

        Violations are drawn from `job.pr.files` today, so their paths are
        already branch files and the union adds nothing. The fallback is there
        for a caller whose violations come from somewhere else, and until this
        nothing established it works — an untested safety net is a claim, not a
        guarantee. Without it such a violation would be handed to the agent as
        work and then refused at the commit as out of scope.
        """
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        adapter = review.fix.ReviewFixAdapter(
            job, [], violations=[_violation("SA1", "elsewhere.py", 3)],
        )

        assert "elsewhere.py" in adapter._anchor_files()
        assert "elsewhere.py" in adapter._allowed_paths()

    def test_violations_run_the_pass_when_there_are_no_findings(self, git_wt, tmp_path):
        """A clean review over a file the checker flags is still work.

        Before this the pass returned early on an empty findings list, so the
        violations were reported and never acted on.
        """
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        job.static_results = _static_results(_violation("SA1", "src.py", 12))
        inv = _run(job, {"SA1": "fixed"})

        inv.assert_called_once()

    # passes-at-base: asserts the early return this change was careful to keep
    def test_a_clean_review_with_no_violations_still_runs_nothing(self, git_wt, tmp_path):
        """A second work stream must not make an empty one a reason to run."""
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        inv = _run(job, {})
        inv.assert_not_called()

    def test_findings_and_violations_are_handed_over_together(self, git_wt, tmp_path):
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `src.py:1` — Missing guard\n",
            files=["src.py"],
        )
        job.static_results = _static_results(_violation("SA1", "src.py", 12))
        inv = _run(job, {"M1": "fixed", "SA1": "fixed"})

        prompt = inv.call_args.args[1]
        assert "<!-- fix:M1 -->" in prompt
        assert "<!-- fix:SA1 -->" in prompt
        # Findings first: the template orders work by severity, and a violation
        # has none to sort into.
        assert prompt.index("<!-- fix:M1 -->") < prompt.index("<!-- fix:SA1 -->")

    def test_the_agent_is_told_the_measurement_is_not_a_claim_to_disprove(
        self, git_wt, tmp_path,
    ):
        """The template's disprove-first section is wrong for a counted depth.

        An agent invited to argue with the checker produces a decline where the
        repo wanted an early return.
        """
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        job.static_results = _static_results(_violation("SA1", "src.py", 12))
        inv = _run(job, {"SA1": "fixed"})

        prompt = inv.call_args.args[1]
        assert "not claimed by a reviewer" in prompt
        assert "Machine-checked items" in prompt

    def test_the_pass_takes_at_most_the_cap(self, git_wt, tmp_path):
        """Five turns an item against an eighty-turn cap: an uncapped section
        would spend the whole budget on mechanical edits."""
        over = review.fix._MAX_STATIC_ITEMS + 5
        job = _make_job(git_wt, tmp_path, "## Must fix\n", files=["src.py"])
        # One site each: `_static_items` collapses a site to a single item, so
        # violations sharing a function would test that collapse instead.
        job.static_results = _static_results(*[
            _violation(f"SA{n}", "src.py", n, context=f"in f{n}()")
            for n in range(1, over + 1)
        ])
        taken = review.fix._static_items(job.static_results)

        assert len(taken) == review.fix._MAX_STATIC_ITEMS
        # The ones a reader sees first, not an arbitrary slice.
        assert [v.id for v in taken] == [
            f"SA{n}" for n in range(1, review.fix._MAX_STATIC_ITEMS + 1)
        ]

    def test_a_violation_with_no_id_is_never_taken(self, git_wt, tmp_path):
        """It never went through `run_static_analysis`, so it renders no box.

        An outcome against it would have no line to be written back to.
        """
        results = _static_results(StaticViolation(file="src.py", line=1, message="x"))
        assert review.fix._static_items(results) == []

    def test_an_unscoped_result_yields_no_work(self, git_wt, tmp_path):
        """Whole-file measurements are a report, not a work list.

        A result the diff could not narrow includes depth the branch inherited.
        Handing that to the agent is asking it to flatten code the branch never
        touched — and `fix.scope` cannot refuse the edit, because the file it
        lands in is a branch file. So the refusal is here.
        """
        results = _static_results(
            _violation("SA1", "src.py", 4), scoped=False,
        )
        assert review.fix._static_items(results) == []

    def test_an_unscoped_result_still_reports_its_violations(self, git_wt, tmp_path):
        """Not taking the work does not mean hiding the finding."""
        results = _static_results(
            _violation("SA1", "src.py", 4), scoped=False,
        )
        rendered = format_static_analysis(results)
        assert "SA1" in rendered
        assert "src.py:4" in rendered

    def test_one_over_deep_function_is_one_item_not_several(self, git_wt, tmp_path):
        """A nesting checker reports every line past the limit; one edit fixes all.

        Handed over whole they spend four items of the cap, ask the agent to
        tick four boxes for one edit, and guarantee a false contradiction at
        the gate — `fix.reconcile` attributes by path, so several items in one
        file make every deferral among them look like a deferral with an edit
        behind it.
        """
        results = _static_results(*[
            _violation(f"SA{n}", "src.py", n, context="in deep()")
            for n in range(4, 8)
        ])
        taken = review.fix._static_items(results)

        assert [v.id for v in taken] == ["SA4"]
        # The shallowest line, which is where the flattening starts.
        assert taken[0].line == 4

    def test_two_functions_in_one_file_are_two_items(self, git_wt, tmp_path):
        """The collapse is per site, not per file — these need separate edits."""
        results = _static_results(
            _violation("SA1", "src.py", 4, context="in one()"),
            _violation("SA2", "src.py", 9, context="in two()"),
        )
        assert [v.id for v in review.fix._static_items(results)] == ["SA1", "SA2"]

    def test_the_same_function_name_in_two_files_is_two_items(self, git_wt, tmp_path):
        results = _static_results(
            _violation("SA1", "a.py", 4, context="in run()"),
            _violation("SA2", "b.py", 4, context="in run()"),
        )
        assert [v.id for v in review.fix._static_items(results)] == ["SA1", "SA2"]

    def test_a_fixed_violation_is_described_by_its_location_and_message(self):
        """A violation has no prose body, so the location has to be in the line."""
        violation = _violation("SA1", "src.py", 12, context="in run()")
        summary = review.fix._summary(
            [_outcome("SA1", FixOutcome.FIXED)], {"SA1": violation.describe()},
        )
        assert summary == (
            "Fixed:\n  - [SA1] src.py:12 — depth 5 exceeds limit 4 (in run())"
        )


class TestADeclineSurvivesTheNextRound:
    """The section is a fresh render of a fresh scan, every review.

    So an annotation written onto it is erased by the next run. For a decline
    that means the adjudication is lost and the violation goes back to the
    agent — every round, for the life of the branch, because nothing about the
    code changed to stop it being reported. A finding keeps its verdict on the
    document and `run_fix_pass` reads it back; a violation has no such line, so
    the verdict has to live in the sidecar.
    """

    def _declined_round(self, git_wt, tmp_path):
        """Run a pass that declines SA1, and hand back the job."""
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n\n## Static Analysis\n\n"
            "- [ ] **[SA1]** **`src.py:4`** — depth 5 exceeds limit 4 (in f())\n",
            files=["src.py"],
        )
        job.static_results = _static_results(
            _violation("SA1", "src.py", 4, context="in f()"),
        )
        _run(job, {"SA1": "declined — ceiling: documented tradeoff"})
        return job

    def test_the_decline_is_recorded_against_the_site(self, git_wt, tmp_path):
        job = self._declined_round(git_wt, tmp_path)
        site = _violation("SA1", "src.py", 4, context="in f()").site

        recorded = review.paths.read_review_meta(
            Path(job.artifact_dir),
        ).static_declined
        assert site in recorded
        assert "documented tradeoff" in recorded[site]

    def test_the_next_round_does_not_hand_it_back_to_the_agent(
        self, git_wt, tmp_path,
    ):
        """The whole point: a `ceiling:` decline is not re-litigated forever."""
        job = self._declined_round(git_wt, tmp_path)
        declined = review.paths.read_review_meta(
            Path(job.artifact_dir),
        ).static_declined

        # The next review renumbers from a fresh scan; same site, new id.
        next_round = _static_results(
            _violation("SA1", "src.py", 6, context="in f()"),
        )
        assert review.fix._static_items(next_round, declined) == []

    def test_a_line_that_moved_is_still_the_same_site(self, git_wt, tmp_path):
        """Keyed on file and function, so an edit above it does not revive it."""
        first = _violation("SA1", "src.py", 4, context="in f()")
        moved = _violation("SA7", "src.py", 88, context="in f()")
        assert first.site == moved.site

    def test_a_different_function_in_the_same_file_is_not_covered(self):
        """A decline adjudicates one piece of work, not the whole file."""
        declined = _violation("SA1", "src.py", 4, context="in f()")
        other = _violation("SA2", "src.py", 9, context="in g()")
        assert declined.site != other.site

        taken = review.fix._static_items(
            _static_results(other), {declined.site: "ceiling"},
        )
        assert [v.id for v in taken] == ["SA2"]

    def test_the_section_shows_the_reader_it_was_adjudicated(self, git_wt, tmp_path):
        """The scan keeps reporting it — that is what a decline means — so the
        section has to say someone already decided, or it reads as new work."""
        violation = _violation("SA1", "src.py", 4, context="in f()")
        rendered = format_static_analysis(
            _static_results(violation),
            {violation.site: "ceiling: documented tradeoff"},
        )
        line = next(ln for ln in rendered.split("\n") if "SA1" in ln)
        assert line.endswith("*(declined — ceiling: documented tradeoff)*")

    def test_a_skip_is_not_persisted(self, git_wt, tmp_path):
        """Work still owed belongs back in the next round's list."""
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n\n## Static Analysis\n\n"
            "- [ ] **[SA1]** **`src.py:4`** — depth 5 exceeds limit 4 (in f())\n",
            files=["src.py"],
        )
        job.static_results = _static_results(
            _violation("SA1", "src.py", 4, context="in f()"),
        )
        _run(job, {"SA1": "needs a person — a design call"})

        assert review.paths.read_review_meta(
            Path(job.artifact_dir),
        ).static_declined == {}


class TestApplyStaticOutcomes:
    """The `## Static Analysis` section is rewritten to what the pass did.

    Without this the section still describes the tree the review measured, and
    a reader opening the PR after a fix pass sees a violation reported live at
    a line where it no longer exists.
    """

    SECTION = (
        "## Static Analysis\n"
        "\n"
        "### Nesting depth\n"
        "\n"
        "- [ ] **[SA1]** **`a.py:1`** — depth 5 exceeds limit 4 (in one())\n"
        "- [ ] **[SA2]** **`b.py:2`** — depth 6 exceeds limit 4 (in two())\n"
    )

    def _line(self, text: str, vid: str) -> str:
        return next(ln for ln in text.split("\n") if f"**[{vid}]**" in ln)

    def test_a_fixed_violation_is_ticked(self):
        out = review.fix._apply_static_outcomes(
            self.SECTION, [_outcome("SA1", FixOutcome.FIXED)],
        )
        assert self._line(out, "SA1").startswith("- [x] **[SA1]**")

    def test_a_skipped_violation_carries_the_reason(self):
        out = review.fix._apply_static_outcomes(self.SECTION, [
            _outcome("SA1", FixOutcome.NEEDS_HUMAN, "needs an extracted helper"),
        ])
        line = self._line(out, "SA1")
        assert line.startswith("- [ ] **[SA1]**")
        assert line.endswith("*(skipped — needs an extracted helper)*")

    def test_a_declined_violation_carries_the_reason(self):
        out = review.fix._apply_static_outcomes(self.SECTION, [
            _outcome("SA1", FixOutcome.DECLINED, "ceiling: documented tradeoff"),
        ])
        assert self._line(out, "SA1").endswith("*(declined — ceiling: documented tradeoff)*")

    def test_a_violation_nothing_answered_is_left_exactly_as_rendered(self):
        """What the document should say about work nothing reached.

        A violation past the cap has no outcome, and annotating it would report
        a verdict the pass never reached.
        """
        out = review.fix._apply_static_outcomes(
            self.SECTION, [_outcome("SA1", FixOutcome.FIXED)],
        )
        assert self._line(out, "SA2") == self._line(self.SECTION, "SA2")

    def test_a_deferral_leaves_the_line_alone(self):
        """The agent never reached it, so the line still describes open work."""
        out = review.fix._apply_static_outcomes(
            self.SECTION, [_outcome("SA1", FixOutcome.DEFERRED)],
        )
        assert self._line(out, "SA1") == self._line(self.SECTION, "SA1")

    def test_an_unverified_fix_ticks_and_says_so(self):
        out = review.fix._apply_static_outcomes(self.SECTION, [
            _outcome("SA1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        line = self._line(out, "SA1")
        assert line.startswith("- [x] **[SA1]**")
        assert line.endswith("*(unverified — no runnable check)*")

    def test_an_already_ticked_line_is_not_annotated_twice(self):
        ticked = self.SECTION.replace("- [ ] **[SA1]**", "- [x] **[SA1]**")
        out = review.fix._apply_static_outcomes(ticked, [
            _outcome("SA1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert self._line(out, "SA1") == self._line(ticked, "SA1")

    def test_a_box_quoted_in_the_message_is_not_the_one_that_gets_ticked(self):
        """The declaration's own box, not the first `- [ ]` anywhere on the line.

        The findings side guards this because a review of a template quotes an
        empty box in its prose. A violation message can carry one the same way —
        a checker reporting on a markdown template, or any message quoting the
        syntax — and ticking the quotation would corrupt the text while leaving
        the violation open.
        """
        section = (
            "## Static Analysis\n"
            "- [ ] **[SA1]** **`t.md:1`** — the line `- [ ] fixed` is malformed\n"
        )
        out = review.fix._apply_static_outcomes(
            section, [_outcome("SA1", FixOutcome.FIXED)],
        )
        line = self._line(out, "SA1")
        assert line.startswith("- [x] **[SA1]**")
        # The quotation is prose and stays exactly as the checker wrote it.
        assert "`- [ ] fixed`" in line

    def test_a_declaration_quoted_outside_the_section_is_not_the_one_ticked(self):
        """The rewrite is scoped to the section, not to the line's shape.

        A finding quoting an `SA` line is a line of the same shape outside the
        section — and a review of this repo quotes one, because the tests and
        docstrings here contain them verbatim. Unscoped, the first match in the
        document won: the quotation inside the finding's body was ticked and
        the violation it quoted stayed open, so one pass corrupted a finding
        and reported its own fix as undone.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `t.py:1` — the fixture quotes a declaration:\n"
            "  - [ ] **[SA1]** **`src.py:9`** — depth 5 exceeds limit 4\n"
            "\n"
            + self.SECTION
        )
        out = review.fix._apply_static_outcomes(
            text, [_outcome("SA1", FixOutcome.FIXED)],
        )
        quoted, declared = [
            ln for ln in out.split("\n") if "**[SA1]**" in ln
        ]
        assert quoted.strip().startswith("- [ ] **[SA1]**"), "the quotation was ticked"
        assert declared.startswith("- [x] **[SA1]**"), "the declaration was not"

    def test_a_document_with_no_section_is_returned_unchanged(self):
        """Nothing to scope to is nothing to rewrite."""
        text = "## Must fix\n- [ ] **[M1]** `a.py:1` — Missing guard\n"
        assert review.fix._apply_static_outcomes(
            text, [_outcome("SA1", FixOutcome.FIXED)],
        ) == text

    def test_the_rest_of_the_document_survives_the_rewrite(self):
        """The scoping splices the section back between what surrounded it."""
        text = self.SECTION + "\n## Verdict\n\nRequest changes\n"
        out = review.fix._apply_static_outcomes(
            text, [_outcome("SA1", FixOutcome.FIXED)],
        )
        assert "## Verdict" in out
        assert "Request changes" in out
        assert self._line(out, "SA2") == self._line(self.SECTION, "SA2")

    def test_a_finding_line_is_left_to_the_findings_rewriter(self):
        """The two streams must not rewrite each other's lines."""
        both = "## Must fix\n- [ ] **[M1]** `a.py:1` — Missing guard\n\n" + self.SECTION
        out = review.fix._apply_static_outcomes(
            both, [_outcome("M1", FixOutcome.FIXED)],
        )
        assert "- [ ] **[M1]**" in out

    def test_a_real_pass_writes_the_rewritten_section_to_the_review_file(
        self, git_wt, tmp_path,
    ):
        """Asserted through `run_fix_pass`, not by calling the rewriter.

        Every case above calls `_apply_static_outcomes` directly, so all of them
        keep passing with the call site disconnected — which is the whole defect
        this rewriter exists to fix, reappearing as a unit test that cannot see
        it. This one reads the file the pass actually wrote.
        """
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n\n" + self.SECTION,
            files=["src.py"],
        )
        job.static_results = _static_results(
            _violation("SA1", "a.py", 1), _violation("SA2", "b.py", 2),
        )

        def edit():
            (git_wt / "src.py").write_text("flattened\n")

        _run(job, {"SA1": "fixed", "SA2": "needs a person — an extracted helper"},
             work=edit)

        written = Path(job.review_file).read_text()
        sa1 = next(ln for ln in written.split("\n") if "**[SA1]**" in ln)
        sa2 = next(ln for ln in written.split("\n") if "**[SA2]**" in ln)
        assert sa1.startswith("- [x] **[SA1]**")
        assert sa2.startswith("- [ ] **[SA2]**")
        assert sa2.endswith("*(skipped — an extracted helper)*")
