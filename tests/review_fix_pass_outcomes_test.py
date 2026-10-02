"""Tests for how the review fix pass reports what it did — the summary it
prints and the `review.md` it re-renders from the agent's outcomes.
"""

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

from review_fix_pass_support import _outcome, _finding


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

    OPEN = (
        "## Must fix\n"
        "- [ ] **[M1]** `a.py:1` — Missing nil check\n"
        "- [ ] **[M2]** `b.py:2` — Retry budget is unbounded\n"
    )

    # What a PR-mode review writes: `review-templates/single-agent.md` asks for a
    # finding with no checkbox, and `FINDING_ID_RE` makes the box optional so the
    # line parses either way. A pass that only ever saw `OPEN` cannot see what
    # this shape does to a tick.
    NO_CHECKBOX = (
        "## Must fix\n"
        "- **[M1]** **`a.py:1`** — Missing nil check\n"
    )

    def test_a_fix_ticks_the_box(self):
        out = review.fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.FIXED)])
        assert "- [x] **[M1]**" in out
        assert "- [ ] **[M2]**" in out

    def test_an_unverified_fix_ticks_the_box_and_says_so(self):
        """The tick is honest — an edit landed — but nothing exercised it."""
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out.splitlines()[1].endswith("*(unverified — no runnable check)*")

    def test_a_skip_appended_to_prose_quoting_a_decline_stays_a_skip(self):
        """The verdict path has the same hazard and cannot answer it by refusing.

        A caveat withheld costs a caveat. A skip withheld costs the outcome: the
        finding parses as declined and `run_fix_pass` drops a declined finding
        from the work set, so no later round picks it up either.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — the `*(declined — x)*` annotation is read "
            "anywhere\n"
        )
        out = review.fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        doc = review.document.ReviewDocument.parse(out)
        assert doc.findings[0].declined is False
        assert review.document.is_skipped(doc.findings[0]) is True
        assert [f.id for f in doc.open_findings if not f.declined] == ["M1"]

    def test_a_reason_carrying_a_newline_does_not_fabricate_a_finding(self):
        """A split line's remainder is parsed as whatever it happens to look like.

        `fix.tracking` collapses whitespace on the engine's path, but an
        `ItemOutcome` built anywhere else does not pass through it.
        """
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "one\n- [ ] **[M9]** `b.py:2` — injected"),
        ])
        assert [f.id for f in review.document.ReviewDocument.parse(out).findings] == [
            "M1", "M2",
        ]

    # passes-at-base: base writes no caveat, so no detail reaches the document
    def test_a_verify_detail_carrying_a_newline_does_not_fabricate_a_finding(self):
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="one\n- [ ] **[S9]** `b.py:2` — injected"),
        ])
        assert [f.id for f in review.document.ReviewDocument.parse(out).findings] == [
            "M1", "M2",
        ]

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
        line = f"- [ ] **[M1]** `a.py:1` — {body}"
        out = review.fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        before = review.grammar.FindingIdentity.of(line)
        after = review.grammar.FindingIdentity.of(out.splitlines()[1])
        assert after.stable_id == before.stable_id

    # passes-at-base: base rewrites nothing on the line, so the spacing is safe for free
    def test_an_append_leaves_the_author_s_own_spacing_alone(self):
        """Inline code, table alignment and indentation are the author's."""
        line = "- [ ] **[M1]** `a.py:1` — compare `x  ==  y` and a | a  | b  | table"
        out = review.fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "nope")],
        )
        assert out.splitlines()[1].startswith(line)

    def test_a_verdict_does_not_overwrite_a_carried_caveat(self):
        """The finding stays open, so the next round retries it rather than losing it."""
        line = "- [ ] **[M1]** `a.py:1` — x *(unverified — no runnable check)*"
        out = review.fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        doc = review.document.ReviewDocument.parse(out)
        assert out.splitlines()[1] == line
        assert [f.id for f in doc.open_findings if not f.declined] == ["M1"]

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_an_annotation_is_not_turned_into_one(self):
        """The decline pattern runs from any `*(` to the last `)*` on the line.

        A quotation mid-line is safe until something is appended after it — the
        append supplies the close and the pattern spans the whole distance. The
        docs and tests of this module quote the annotation verbatim, so this is
        the shape a review of this very file takes.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — The `*(declined — reason)*` annotation "
            "is matched anywhere\n"
        )
        out = review.fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert finding.checked is True

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_a_skip_is_not_turned_into_one(self):
        """Asserted against the skip pattern, which `is_skipped` cannot answer.

        `is_skipped` short-circuits on a checked finding, so it reports False
        for any tick however the body reads — including one this append just
        turned into a skip annotation.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — prose about `*(skipped — x)*` here\n"
        )
        out = review.fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert review.document._SKIP_TAIL_RE.search(finding.body) is None
        assert finding.checked is True

    def test_a_box_quoted_in_prose_is_not_the_one_that_gets_ticked(self):
        """A finding about a template quotes the empty box in its own body.

        Ticking that occurrence corrupts the prose and annotates a line whose
        own declaration stays unchecked, so the finding never closes.
        """
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.py:1`** — the template writes `- [ ] **[M1]**` "
            "with no box\n"
        )
        out = review.fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out == text

    def test_a_skip_reason_quoting_a_decline_stays_a_skip(self):
        """`reason` is the gate's own prose, by way of the engine's verdict."""
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "as the docs say *(declined — adjudicated)*"),
        ])
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert review.document.is_skipped(finding) is True

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
    def test_an_unverified_tick_is_still_a_fix_to_the_parser(self):
        """The caveat is for the reader; it must not read back as a skip or decline.

        Asserted on the wording rather than only on `is_skipped`, which
        short-circuits on a checked finding and so answers False for any tick
        however the annotation reads.
        """
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert "*(skipped" not in out
        assert "*(declined" not in out
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert finding.checked is True
        assert finding.declined is False
        assert review.document.is_skipped(finding) is False

    # passes-at-base: base annotates nothing, so a checkbox-free line is untouched there anyway
    def test_a_finding_with_no_checkbox_is_not_annotated(self):
        """A PR-mode line has no box to tick, so there is no landed fix to hedge.

        The tick is a no-op on that shape and `checked` stays false, so the
        guard that makes this idempotent never engages — annotating anyway
        appends a caveat per round to a finding that never closes.
        """
        out = review.fix._apply_outcomes(self.NO_CHECKBOX, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out == self.NO_CHECKBOX

    # passes-at-base: base annotates nothing, so nothing can compound there
    def test_a_checkbox_free_finding_is_stable_across_rounds(self):
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        text = self.NO_CHECKBOX
        for _ in range(3):
            text = review.fix._apply_outcomes(text, [outcome])
        assert text == self.NO_CHECKBOX

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_an_annotation_does_not_become_one(self):
        """`verify_detail` is agent prose, and the gate reasons about this repo.

        The decline pattern is unanchored at its head, so a quotation inside the
        caveat is found there and the whole finding reads as adjudicated — which
        drops it from the next round's work set.
        """
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="the repro *(declined — see above)*"),
        ])
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert finding.checked is True

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_a_skip_does_not_become_one(self):
        """Asserted against the skip pattern itself, not `is_skipped`.

        `is_skipped` short-circuits on a checked finding, so it answers False
        for any tick however the annotation reads — it cannot see whether the
        quotation survived into the document.
        """
        out = review.fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="see *(skipped — needs design)*"),
        ])
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert review.document._SKIP_TAIL_RE.search(finding.body) is None
        assert review.document.is_skipped(finding) is False
        assert finding.checked is True

    # passes-at-base: base leaves a carried-forward annotation alone by writing none of its own
    def test_an_already_hedged_line_gains_no_second_caveat(self):
        """A synthesis pass carries a trailing annotation forward intact.

        Asserted on the whole line, not on a count of the opening: the append
        path defuses a `*(` it finds in the line, so a second caveat arrives
        beside a first one that has been broken to `* (` — which a count of
        `*(unverified` reports as one, the same answer as leaving it alone.
        """
        hedged = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — x *(unverified — no runnable check)*\n"
        )
        out = review.fix._apply_outcomes(hedged, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out.splitlines()[1] == (
            "- [x] **[M1]** `a.py:1` — x *(unverified — no runnable check)*"
        )

    def test_an_unverified_fix_with_no_detail_is_annotated_bare(self):
        out = review.fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=False)],
        )
        assert out.splitlines()[1].endswith("*(unverified)*")

    # passes-at-base: base ticks and annotates nothing, idempotent for a reason this must keep
    def test_a_second_round_does_not_annotate_an_unverified_tick_twice(self):
        """A record accumulates across rounds, so the same outcome is re-applied."""
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        once = review.fix._apply_outcomes(self.OPEN, [outcome])
        assert review.fix._apply_outcomes(once, [outcome]) == once

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_verified_fix_ticks_the_box_and_says_nothing_more(self):
        out = review.fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=True)],
        )
        assert out.splitlines()[1] == "- [x] **[M1]** `a.py:1` — Missing nil check"

    def test_a_needs_a_person_is_annotated_as_a_skip(self):
        """`*(skipped — reason)*` is the vocabulary the review's parser reads."""
        out = review.fix._apply_outcomes(
            self.OPEN, [_outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert out.splitlines()[2].endswith("*(skipped — needs design)*")
        assert review.document.ReviewDocument.parse(out).findings[1].checked is False

    def test_an_agent_s_decline_is_annotated_as_one(self):
        out = review.fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.DECLINED, "documented tradeoff")],
        )
        finding = review.document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is True
        assert finding.decline_reason == "documented tradeoff"

    def test_an_annotation_with_no_reason_still_registers(self):
        out = review.fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.DECLINED)])
        assert review.document.ReviewDocument.parse(out).findings[0].declined is True

    def test_a_finding_the_agent_never_reached_is_left_for_the_next_round(self):
        out = review.fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.DEFERRED)])
        assert out == self.OPEN

    def test_a_finding_no_outcome_names_is_left_alone(self):
        assert review.fix._apply_outcomes(self.OPEN, []) == self.OPEN

    def test_a_box_the_review_already_ticked_is_not_re_annotated(self):
        text = "## Must fix\n- [x] **[M1]** `a.py:1` — Already fixed\n"
        out = review.fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert out == text

    def test_a_finding_the_review_declined_keeps_that_verdict(self):
        """The decline outranks the pass: it was reached before the agent ran."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — *(declined — documented tradeoff)* — Lock\n"
        )
        out = review.fix._apply_outcomes(text, [_outcome("M1", FixOutcome.FIXED)])
        assert out == text

    def test_a_finding_already_carrying_a_skip_gains_no_second_annotation(self):
        """Two annotations on one line leave the document saying two things."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — Lock *(skipped — needs design)*\n"
        )
        out = review.fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "still needs design")],
        )
        assert out == text

    def test_prose_outside_a_finding_line_is_untouched(self):
        text = self.OPEN + "\n## Notes\n\nA paragraph about `- [ ] **[M1]**` syntax.\n"
        out = review.fix._apply_outcomes(text, [_outcome("M1", FixOutcome.FIXED)])
        assert out.endswith("A paragraph about `- [ ] **[M1]**` syntax.\n")
        assert "- [x] **[M1]**" in out
