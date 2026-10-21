"""Tests for how the review fix pass reports what it did — the summary it
prints and the `review.md` it re-renders from the agent's outcomes.
"""

import re
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
import review.document
import review.fix
import review.grammar
from pr.fix import FixOutcome, ItemOutcome
from review.fix import apply_outcomes
from review.record import Disposition, ProseSection
from review.render import render_body

from review_fix_pass_support import _outcome, _finding
from review_record_support import finding, record


def _apply(rec, outcomes):
    return render_body(apply_outcomes(rec, outcomes))


def _applied(rec, outcomes):
    return apply_outcomes(rec, outcomes)


def _finding_line(text, fid):
    return next(ln for ln in text.split("\n") if f"**[{fid}]**" in ln)


_FINDING_DECL_RE = re.compile(r"^- (\[[ x]\] )?\*\*\[([A-Z]\d+)\]\*\*")


def _declared_ids(text):
    return [
        m.group(2)
        for ln in text.split("\n")
        if (m := _FINDING_DECL_RE.match(ln))
    ]


class TestTheSummary:
    """Three answers worth telling apart, in the terms each is worth reading."""

    FINDINGS = {"M1": "the guard is missing"}

    def test_a_fix_is_described_by_the_finding_it_answered(self):
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED)], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"

    def test_a_fix_for_a_finding_the_review_no_longer_holds_names_its_file(self):
        """The tracking file records a location, and nothing else about the item."""
        outcome = ItemOutcome(id="M9", outcome=FixOutcome.FIXED, file="gone.py")
        assert "[M9] gone.py" in review.fix._summary([outcome], self.FINDINGS)

    def test_a_multi_line_body_is_reported_by_its_first_line(self):
        described = {"M1": review.fix._describe_finding(
            _finding("M1", body="headline\n\nthe rest of it"),
        )}
        summary = review.fix._summary([_outcome("M1", FixOutcome.FIXED)], described)
        assert summary == "Fixed:\n  - [M1] headline"

    def test_a_long_finding_wraps_instead_of_truncating_mid_sentence(self):
        """The commit body is where a self-review records why; clipping loses it."""
        sentence = (
            "Scoping the toolchain-pin guard to only `strategy.job-index == 0` "
            "makes the entire matrix skip the toolchain pin rather than applying "
            "it on every job."
        )
        described = {"S1": review.fix._describe_finding(
            _finding("S1", body=sentence),
        )}
        summary = review.fix._summary(
            [_outcome("S1", FixOutcome.FIXED)], described,
        )
        assert sentence in " ".join(summary.split())
        assert "\u2026" not in summary
        lines = summary.splitlines()
        assert all(len(line) <= 100 for line in lines)
        prefix = "  - [S1] "
        assert lines[1].startswith(prefix)
        continuations = lines[2:]
        assert continuations
        hang = " " * len(prefix)
        assert all(line.startswith(hang) for line in continuations)

    def test_a_skip_is_reported_by_the_reason_the_agent_gave(self):
        summary = review.fix._summary(
            [_outcome("S1", FixOutcome.NEEDS_HUMAN, "needs a product decision")], {},
        )
        assert "Skipped:\n  - [S1] needs a product decision" in summary

    def test_a_deferral_is_reported_as_a_skip_with_no_reason(self):
        """The agent never reached it, so there is no reason it could have given."""
        summary = review.fix._summary([_outcome("N1", FixOutcome.DEFERRED)], {})
        assert "Skipped:\n  - [N1] no auto-fix" in summary

    def test_a_truncated_pass_names_the_turn_limit(self):
        summary = review.fix._summary(
            [
                _outcome("M1", FixOutcome.FIXED),
                _outcome("N1", FixOutcome.DEFERRED),
            ],
            self.FINDINGS,
            stop=Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=30),
        )
        assert "max turns" in summary
        assert "30" in summary

    def test_a_truncated_deferral_is_not_reported_as_no_auto_fix(self):
        summary = review.fix._summary(
            [_outcome("N1", FixOutcome.DEFERRED)],
            {},
            stop=Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=30),
        )
        assert "not reached (turn limit)" in summary
        assert "no auto-fix" not in summary

    def test_a_decline_has_its_own_heading(self):
        """A skip is retried next pass; a decline is work nobody is going to do."""
        summary = review.fix._summary(
            [_outcome("M2", FixOutcome.DECLINED, "documented `ceiling:` tradeoff")], {},
        )
        assert "Declined:\n  - [M2] documented `ceiling:` tradeoff" in summary
        assert "Skipped:" not in summary

    def test_a_decline_without_a_reason_says_what_it_still_means(self):
        summary = review.fix._summary([_outcome("N1", FixOutcome.DECLINED)], {})
        assert "adjudicated, not a defect" in summary

    def test_a_pass_that_settled_nothing_summarises_nothing(self):
        assert review.fix._summary([], {}) == ""

    def test_a_pass_claiming_no_fixes_names_the_files_it_is_committing(self):
        """The blocks describe outcomes; the commit carries files.

        Attribution between the two is by path, so an agent that fixed a
        finding by editing its caller or its test is invisible to every
        mechanical check — and the summary then reads as though the pass did
        nothing, over a commit that changed the code.
        """
        summary = review.fix._summary(
            [_outcome("N1", FixOutcome.DEFERRED)], {}, {"caller.py", "a_test.py"},
        )

        assert "no auto-fix" in summary
        assert "This pass reports no fixes but is committing changes to:" in summary
        # Sorted, so the same pass renders the same message twice running.
        assert summary.index("  a_test.py") < summary.index("  caller.py")
        assert "Read the diff" in summary

    def test_a_pass_with_a_fix_in_it_does_not_get_the_footer(self):
        """A fix already explains why the tree moved.

        Printing the files under every summary would train the reader to skip
        the block, which costs exactly the case the block exists for.
        """
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED), _outcome("N1", FixOutcome.DEFERRED)],
            {}, {"a.py"},
        )

        assert "is committing changes to" not in summary

    def test_a_pass_that_committed_nothing_does_not_get_the_footer(self):
        """Nothing was staged, so there is no discrepancy to report."""
        summary = review.fix._summary(
            [_outcome("N1", FixOutcome.DEFERRED)], {}, set(),
        )

        assert "is committing changes to" not in summary

    def test_an_unreadable_worktree_does_not_get_the_footer(self):
        """None is "could not look", which is not evidence of unclaimed work."""
        summary = review.fix._summary(
            [_outcome("N1", FixOutcome.DEFERRED)], {}, None,
        )

        assert "is committing changes to" not in summary

    def test_a_fix_the_gate_could_not_stand_behind_says_so(self):
        """Only a falsified fix is demoted, so an unverifiable one stays under Fixed."""
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False,
                      verify_detail="no runnable check")],
            self.FINDINGS,
        )
        assert summary == (
            "Fixed:\n  - [M1] the guard is missing "
            "(not verified automatically — no runnable check)"
        )

    def test_an_unverified_fix_with_no_detail_still_carries_the_caveat(self):
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False)], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing (not verified automatically)"

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_fix_the_gate_confirmed_carries_no_caveat(self):
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=True,
                      verify_detail="pytest tests/a_test.py passed")],
            self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_pass_that_never_ran_the_gate_reads_as_it_always_did(self):
        """`verified is None` is nobody asking, which is not a caveat to print."""
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verify_detail="ignored")], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"


class TestApplyOutcomes:
    """The re-render — the review document is written from the outcomes."""

    OPEN = record(
        finding("M1", "Missing nil check", checkbox=True),
        finding("M2", "Retry budget is unbounded", path="b.py", line=2, checkbox=True),
    )

    # What a PR-mode review writes: `review-templates/single-agent.md` asks for a
    # finding with no checkbox, and `FINDING_ID_RE` makes the box optional so the
    # line parses either way. A pass that only ever saw `OPEN` cannot see what
    # this shape does to a tick.
    NO_CHECKBOX = record(finding("M1", "Missing nil check"))

    def test_a_fix_ticks_the_box(self):
        out = _apply(self.OPEN, [_outcome("M1", FixOutcome.FIXED)])
        assert "- [x] **[M1]**" in out
        assert "- [ ] **[M2]**" in out

    def test_an_unverified_fix_ticks_the_box_and_says_so(self):
        """The tick is honest — an edit landed — but nothing exercised it."""
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert _finding_line(out, "M1").endswith("*(unverified — no runnable check)*")

    def test_a_skip_appended_to_prose_quoting_a_decline_stays_a_skip(self):
        """The verdict path has the same hazard and cannot answer it by refusing.

        A caveat withheld costs a caveat. A skip withheld costs the outcome: the
        finding parses as declined and `run_fix_pass` drops a declined finding
        from the work set, so no later round picks it up either.
        """
        rec = record(finding(
            "M1",
            "the `*(declined — x)*` annotation is read anywhere",
            checkbox=True,
        ))
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        answered = _applied(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        ).finding("M1")
        assert answered.disposition is Disposition.SKIPPED
        assert _finding_line(out, "M1").endswith("*(skipped — no auto-fix)*")
        assert "- [x]" not in out

    def test_a_reason_carrying_a_newline_does_not_fabricate_a_finding(self):
        """A split line's remainder is parsed as whatever it happens to look like.

        `fix.tracking` collapses whitespace on the engine's path, but an
        `ItemOutcome` built anywhere else does not pass through it.
        """
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "one\n- [ ] **[M9]** `b.py:2` — injected"),
        ])
        assert "**[M9]**" in out
        assert _declared_ids(out) == ["M1", "M2"]

    # passes-at-base: base writes no caveat, so no detail reaches the document
    def test_a_verify_detail_carrying_a_newline_does_not_fabricate_a_finding(self):
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="one\n- [ ] **[S9]** `b.py:2` — injected"),
        ])
        assert "**[S9]**" in out
        assert _declared_ids(out) == ["M1", "M2"]

    # passes-at-base: base rewrites nothing on the line, so the id is safe there for free
    def test_an_append_leaves_the_stable_id_alone(self):
        """`FindingIdentity` hashes the body's first eighty characters.

        An annotation lands past them, so appending one has never changed the
        id — and `reconcile` matches a prior round's finding by exactly that id.
        Anything this function rewrites *before* position eighty breaks the
        carry-forward silently, on the next run rather than this one.
        """
        body = (
            "the guard  is missing here and this body runs well past eighty "
            "characters so the append lands after it"
        )
        rec = record(finding("M1", body, checkbox=True))
        before_line = _finding_line(render_body(rec), "M1")
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        before = review.grammar.FindingIdentity.of(before_line)
        after = review.grammar.FindingIdentity.of(_finding_line(out, "M1"))
        assert after.stable_id == before.stable_id

    # passes-at-base: base rewrites nothing on the line, so the spacing is safe for free
    def test_an_append_leaves_the_author_s_own_spacing_alone(self):
        """Inline code, table alignment and indentation are the author's."""
        rec = record(finding(
            "M1", "compare `x  ==  y` and a | a  | b  | table", checkbox=True,
        ))
        before = _finding_line(render_body(rec), "M1")
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "nope")],
        )
        assert _finding_line(out, "M1").startswith(before)

    def test_a_verdict_does_not_overwrite_a_carried_caveat(self):
        """The finding stays open, so the next round retries it rather than losing it."""
        rec = record(finding(
            "M1", "x *(unverified — no runnable check)*", checkbox=True,
        ))
        before = _finding_line(render_body(rec), "M1")
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        assert _finding_line(out, "M1") == before

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_an_annotation_is_not_turned_into_one(self):
        """The decline pattern runs from any `*(` to the last `)*` on the line.

        A quotation mid-line is safe until something is appended after it — the
        append supplies the close and the pattern spans the whole distance. The
        docs and tests of this module quote the annotation verbatim, so this is
        the shape a review of this very file takes.
        """
        rec = record(finding(
            "M1", "The `*(declined — reason)*` annotation is matched anywhere",
            checkbox=True,
        ))
        out = _apply(rec, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        answered = _applied(rec, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ]).finding("M1")
        assert answered.disposition is Disposition.FIXED
        assert "- [x] **[M1]**" in out

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_a_skip_is_not_turned_into_one(self):
        """Asserted against the skip pattern, not the disposition alone.

        A FIXED finding is not SKIPPED even when its body quotes a skip
        annotation, so the quotation surviving into the document is what
        this has to hold.
        """
        rec = record(finding(
            "M1", "prose about `*(skipped — x)*` here", checkbox=True,
        ))
        out = _apply(rec, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        answered = _applied(rec, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ]).finding("M1")
        assert review.grammar.SKIP_TAIL_RE.search(answered.body) is None
        assert answered.disposition is Disposition.FIXED
        assert "- [x] **[M1]**" in out

    def test_a_box_quoted_in_prose_is_not_the_one_that_gets_ticked(self):
        """A finding about a template quotes the empty box in its own body.

        Ticking that occurrence corrupts the prose and annotates a line whose
        own declaration stays unchecked, so the finding never closes.
        """
        rec = record(finding(
            "M1", "the template writes `- [ ] **[M1]**` with no box",
        ))
        out = _apply(rec, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert _finding_line(out, "M1") == (
            "- **[M1]** `a.py:1` — the template writes `- [ ] **[M1]**` "
            "with no box"
        )

    def test_a_skip_reason_quoting_a_decline_stays_a_skip(self):
        """`reason` is the gate's own prose, by way of the engine's verdict."""
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "as the docs say *(declined — adjudicated)*"),
        ])
        answered = _applied(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "as the docs say *(declined — adjudicated)*"),
        ]).finding("M1")
        assert answered.disposition is Disposition.SKIPPED
        assert "skipped" in _finding_line(out, "M1")

    def test_a_clip_with_no_room_yields_nothing(self):
        """`text[:n]` with a non-positive n counts from the end.

        The slice would hand back most of the string where the budget was
        tightest — longest output exactly where the caller had least room.
        """
        assert review.fix._clip("abcdefgh", 0) == ""
        assert review.fix._clip("abcdefgh", -5) == ""

    def test_a_clip_never_exceeds_the_limit_it_was_given(self):
        for limit in range(-2, 12):
            assert len(review.fix._clip("abcdefgh", limit)) <= max(limit, 0)

    # passes-at-base: base writes no caveat, so its lines are short for free
    def test_a_hedged_summary_line_fits_the_commit_body_limit(self):
        """These lines land in a commit body, and no hook on this path checks them."""
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="y" * 60)],
            {"M1": "x" * 80},
        )
        assert all(len(line) <= 100 for line in summary.splitlines())

    def test_a_long_detail_alone_cannot_overrun_the_line(self):
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="y" * 90)],
            {"M1": "short"},
        )
        assert all(len(line) <= 100 for line in summary.splitlines())
        assert "not verified automatically" in summary

    def test_the_caveat_survives_a_description_long_enough_to_crowd_it(self):
        """Wrapping keeps both halves: the description is no longer cut to make room."""
        description = "x" * 80
        summary = review.fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="no runnable check")],
            {"M1": description},
        )
        joined = " ".join(summary.split())
        assert description in joined
        assert "(not verified automatically — no runnable check)" in joined
        assert all(len(line) <= 100 for line in summary.splitlines())

    # passes-at-base: base writes no annotation, so the wording assertions hold vacuously there
    def test_an_unverified_tick_renders_as_a_fix(self):
        """The caveat is for the reader; it must not read back as a skip or decline."""
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        line = _finding_line(out, "M1")
        assert line.startswith("- [x] **[M1]**")
        assert review.grammar.SKIP_TAIL_RE.search(line) is None
        assert review.grammar.DECLINED_TAIL_RE.search(line) is None

    # passes-at-base: base annotates nothing, so a checkbox-free line is untouched there anyway
    def test_a_finding_with_no_checkbox_is_not_annotated(self):
        """A PR-mode line has no box to tick, so there is no landed fix to hedge.

        The tick is a no-op on that shape and `checked` stays false, so the
        guard that makes this idempotent never engages — annotating anyway
        appends a caveat per round to a finding that never closes.
        """
        out = _apply(self.NO_CHECKBOX, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert "- [x]" not in out
        assert "unverified" not in out

    # passes-at-base: base annotates nothing, so nothing can compound there
    def test_a_checkbox_free_finding_is_stable_across_rounds(self):
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        rec = self.NO_CHECKBOX
        for _ in range(3):
            rec = _applied(rec, [outcome])
        text = render_body(rec)
        assert "- [x]" not in text
        assert "unverified" not in text

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_an_annotation_does_not_become_one(self):
        """`verify_detail` is agent prose, and the gate reasons about this repo.

        The decline pattern is unanchored at its head, so a quotation inside the
        caveat is found there and the whole finding reads as adjudicated — which
        drops it from the next round's work set.
        """
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="the repro *(declined — see above)*"),
        ])
        line = _finding_line(out, "M1")
        assert line.startswith("- [x] **[M1]**")
        assert review.grammar.DECLINED_TAIL_RE.search(line) is None

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_a_skip_does_not_become_one(self):
        """Asserted against the skip pattern itself, not the disposition.

        A FIXED finding is not SKIPPED even when verify_detail quotes a skip,
        so only the pattern can see whether the quotation survived into the
        document.
        """
        out = _apply(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="see *(skipped — needs design)*"),
        ])
        line = _finding_line(out, "M1")
        assert review.grammar.SKIP_TAIL_RE.search(line) is None

    # passes-at-base: base leaves a carried-forward annotation alone by writing none of its own
    def test_an_already_hedged_line_gains_no_second_caveat(self):
        """A synthesis pass carries a trailing annotation forward intact.

        Asserted on the whole line, not on a count of the opening: the append
        path defuses a `*(` it finds in the line, so a second caveat arrives
        beside a first one that has been broken to `* (` — which a count of
        `*(unverified` reports as one, the same answer as leaving it alone.
        """
        hedged = record(finding(
            "M1", "x *(unverified — no runnable check)*", checkbox=True,
        ))
        out = _apply(hedged, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert _finding_line(out, "M1") == (
            "- [x] **[M1]** `a.py:1` — x *(unverified — no runnable check)*"
        )

    def test_an_unverified_fix_with_no_detail_is_annotated_bare(self):
        out = _apply(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=False)],
        )
        assert _finding_line(out, "M1").endswith("*(unverified)*")

    # passes-at-base: base ticks and annotates nothing, idempotent for a reason this must keep
    def test_a_second_round_does_not_annotate_an_unverified_tick_twice(self):
        """A record accumulates across rounds, so the same outcome is re-applied."""
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        once = _applied(self.OPEN, [outcome])
        assert _applied(once, [outcome]) == once

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_verified_fix_ticks_the_box_and_says_nothing_more(self):
        out = _apply(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=True)],
        )
        assert _finding_line(out, "M1") == "- [x] **[M1]** `a.py:1` — Missing nil check"

    def test_a_needs_a_person_is_annotated_as_a_skip(self):
        """`*(skipped — reason)*` is the vocabulary the review's parser reads."""
        out = _apply(
            self.OPEN, [_outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert _finding_line(out, "M2").endswith("*(skipped — needs design)*")
        assert _applied(
            self.OPEN, [_outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design")],
        ).finding("M2").disposition is Disposition.SKIPPED

    def test_an_agent_s_decline_is_annotated_as_one(self):
        answered = _applied(
            self.OPEN, [_outcome("M1", FixOutcome.DECLINED, "documented tradeoff")],
        ).finding("M1")
        assert answered.disposition is Disposition.DECLINED
        assert answered.reason == "documented tradeoff"

    def test_an_annotation_with_no_reason_still_registers(self):
        assert _applied(
            self.OPEN, [_outcome("M1", FixOutcome.DECLINED)],
        ).finding("M1").disposition is Disposition.DECLINED

    def test_a_finding_the_agent_never_reached_is_left_for_the_next_round(self):
        out = _apply(self.OPEN, [_outcome("M1", FixOutcome.DEFERRED)])
        assert "- [ ] **[M1]**" in out and "- [x]" not in out
        assert "*(skipped" not in out and "*(declined" not in out

    def test_a_finding_no_outcome_names_is_left_alone(self):
        out = _apply(self.OPEN, [])
        assert "- [ ] **[M1]**" in out and "- [ ] **[M2]**" in out
        assert "- [x]" not in out

    def test_a_box_the_review_already_ticked_is_not_re_annotated(self):
        rec = record(finding(
            "M1", "Already fixed", checkbox=True, disposition=Disposition.FIXED,
        ))
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert _finding_line(out, "M1").startswith("- [x] **[M1]**")
        assert "*(skipped" not in out

    def test_a_finding_the_review_declined_keeps_that_verdict(self):
        """The decline outranks the pass: it was reached before the agent ran."""
        rec = record(finding(
            "M1", "Lock", checkbox=True,
            disposition=Disposition.DECLINED, reason="documented tradeoff",
        ))
        out = _apply(rec, [_outcome("M1", FixOutcome.FIXED)])
        assert "- [x]" not in out
        assert "*(declined — documented tradeoff)*" in _finding_line(out, "M1")

    def test_a_finding_already_carrying_a_skip_gains_no_second_annotation(self):
        """Two annotations on one line leave the document saying two things."""
        rec = record(finding(
            "M1", "Lock", checkbox=True,
            disposition=Disposition.SKIPPED, reason="needs design",
        ))
        out = _apply(
            rec, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "still needs design")],
        )
        line = _finding_line(out, "M1")
        assert line.count("*(skipped") == 1
        assert "still needs design" not in line

    def test_prose_outside_a_finding_line_is_untouched(self):
        rec = record(
            finding("M1", "Missing nil check", checkbox=True),
            finding("M2", "Retry budget is unbounded", path="b.py", line=2, checkbox=True),
            extra=(ProseSection("Notes", "A paragraph about `- [ ] **[M1]**` syntax."),),
        )
        out = _apply(rec, [_outcome("M1", FixOutcome.FIXED)])
        assert out.endswith("A paragraph about `- [ ] **[M1]**` syntax.\n")
        assert "- [x] **[M1]**" in out
