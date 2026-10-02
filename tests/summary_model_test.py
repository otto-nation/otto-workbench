"""pr.summary_model: round content and the Action-cell vocabulary."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    HAND_EDITED_ROW, ROUND_ONE_ROW, _GENERATED_ACTION_CELL, _HAND_WRITTEN_ACTION_CELL,
    _no_published_summary, _published_summary, _unmarked, content,
)
from pr.comments_state import ThreadState
import core.markdown
from git.land import CommitStatus
import pr.thread_replies
import pr.attribution
import pr.triage_round
import pr.published_record
import pr.summary_model
import pr.summary_render
import pr.summary_scope
import pr.summary_row
from pr.summary_model import ActionCell, RetiredActionCell
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.thread_models import CommentItem


# Every shape `ActionCell.deferred` is asked for: a filed issue with a link, one
# without, and a deferral that has neither. Shared by the two tests that sweep
# the dynamic cells, so a shape added to one is added to both — a formatter
# branch no test reaches is a wording that can drift from its opening.
_DEFERRED_ARGS = (
    ("ENG-1", "https://linear.app/i/ENG-1"), ("ENG-1", ""), ("", ""),
)


class TestRoundContentHasContent:
    """The one owner of "does this round have anything for a table to say".

    Tested directly rather than only through its callers: the whole point of the
    property is that all of them read the same answer, and a contract pinned
    only through one of them is one the others can still be changed out from
    under.
    """

    def _has(self, content, **kw):
        return content(**kw).has_content

    def test_an_empty_round_has_nothing_to_say(self, content):
        assert self._has(content) is False

    @pytest.mark.parametrize(
        "bucket",
        ["fixed", "needs_human", "declined", "deferred", "dismissed",
         "already_addressed"],
    )
    def test_any_settled_thread_is_a_row(self, content, bucket):
        assert self._has(content, **{bucket: ["t1"]}) is True

    @pytest.mark.parametrize("kind", ["issue_comments", "review_body_comments"])
    def test_an_unseen_comment_is_a_row_with_no_thread_behind_it(self, content, kind):
        assert self._has(content, **{kind: [{"seen": False}]}) is True

    @pytest.mark.parametrize("kind", ["issue_comments", "review_body_comments"])
    def test_a_comment_the_round_saw_is_not(self, content, kind):
        assert self._has(content, **{kind: [{"seen": True}]}) is False

    def test_a_comment_with_no_seen_key_reads_as_unseen(self, content):
        """External data, so the read is a `.get` and its default is the answer."""
        assert self._has(content, issue_comments=[{}]) is True

    def test_a_bucket_present_but_empty_is_not_a_row(self, content):
        """An outcome the round produced nothing under says nothing.

        The mapping normally omits such a bucket, but a caller that keeps one
        it computed as empty must not thereby claim the round has a table.
        """
        assert self._has(content, fixed=[]) is False


class TestRoundContentNeedsAPerson:
    """The one place `DECLINED` and `NEEDS_HUMAN` are folded together.

    They are separate outcomes in the state file because the reason each
    carries is worth telling apart, and every reviewer-facing surface wants
    them as one list — so the fold belongs to the content, not to each caller.
    """

    def test_both_outcomes_arrive_in_one_list(self, content):
        assert content(
            needs_human=["t1"], declined=["t2"],
        ).needs_a_person == ["t1", "t2"]

    def test_every_member_of_the_constant_is_folded(self, content):
        """A member added to `_NEEDS_A_PERSON` reaches the fold on its own."""
        for outcome in pr.summary_model.NEEDS_A_PERSON:
            assert content(**{outcome.value: ["t1"]}).needs_a_person == ["t1"]

    def test_an_outcome_no_entry_reached_contributes_nothing(self, content):
        assert content(fixed=["t1"]).needs_a_person == []


class TestGeneratedActionCell:
    """A cell no generated opening claims was written by a person."""

    def test_every_fix_status_the_renderer_writes_is_recognised(self):
        """Assert on what the builders emit, not on a transcribed copy — a
        wording change there must not silently freeze the rows it renders."""
        for status in CommitStatus:
            cp = pr.attribution.CommitPushResult("9f2e1a0", status, "")
            assert pr.summary_model.is_generated_action(pr.summary_row.fixed_status_text(cp, "owner/repo")) is True
            bare = pr.attribution.CommitPushResult(None, status, "")
            assert pr.summary_model.is_generated_action(pr.summary_row.fixed_status_text(bare, "owner/repo")) is True

    def test_an_unverified_fix_cell_is_recognised_and_still_reads_as_fixed(self):
        """The hedge is a suffix, so the prefix table still places the row.

        `action_outcome` matches on the opening, and both sides of
        `RoundScope.covers` read it: a cell that stopped reading as FIXED would
        make an unverified row differ from its own published copy every round
        and restate it for the life of the PR.
        """
        cell = ActionCell.fixed_in("9f2e1a0", "owner/repo", verified=False)
        assert pr.summary_model.is_generated_action(cell) is True
        assert pr.summary_model.action_outcome(cell) is FixOutcome.FIXED
        assert "unverified" in cell.lower()

    def test_a_verified_fix_cell_does_not_hedge(self):
        cell = ActionCell.fixed_in("9f2e1a0", "owner/repo", verified=True)
        assert pr.summary_model.action_outcome(cell) is FixOutcome.FIXED
        assert "unverified" not in cell.lower()

    def test_every_human_reason_prose_is_recognised(self):
        for reason in pr.summary_model.HumanReason:
            assert pr.summary_model.is_generated_action(reason.prose) is True

    @pytest.mark.parametrize("cell", [
        "Already addressed",
        "Dismissed (invalid)",
        "Deferred",
        "Deferred → ENG-1",
        "Deferred → [ENG-1](https://linear.app/i/ENG-1)",
        "Addressed outside the fix pass",
    ])
    def test_the_literal_cells_are_recognised(self, cell):
        assert pr.summary_model.is_generated_action(cell) is True

    @pytest.mark.parametrize("cell", [
        "",
        _HAND_WRITTEN_ACTION_CELL,
        "Withdrawn by the reviewer",
    ])
    def test_anything_else_reads_as_hand_written(self, cell):
        assert pr.summary_model.is_generated_action(cell) is False

    def test_only_a_row_the_render_covers_is_held(self):
        """A hand-written row the render does not cover is the carry-forward
        case, and must not be reported twice."""
        published = _published_summary(HAND_EDITED_ROW)
        fresh = _published_summary(
            "| [new work](https://github.com/owner/repo/pull/1#discussion_r222) "
            "| @kgn | `new.go:1` | Fixed in `bbbbbbb` |")
        assert pr.summary_scope.hand_written_rows([published], fresh) == []
        assert _unmarked(pr.summary_scope.carried_over_rows(published, fresh)) == [HAND_EDITED_ROW]


class TestTheTwoOursVocabulariesAgree:
    """The reply prefixes and the Action-cell openings, where they overlap.

    Two surfaces answer "did a human overwrite what we wrote?" — a thread reply
    and a summary row — and they answer it against different tables, because a
    reply opens "Applied: dropped the guard" where the cell opens "Fix applied".
    That is correct and #909's spec §2.4 was withdrawn on the strength of it
    (the two predicates disagree on 17 of 19 generated values, by design).

    Two openings do appear in both, and on those the two must not drift: a
    reply the fix pass wrote and a row reporting the same verdict have to be
    recognised as ours by both readers, or one surface protects a hand edit the
    other overwrites. Nothing enforced that until this test.
    """

    # What each reply prefix must mean to the Action table, or None where the
    # reply vocabulary deliberately has no counterpart.
    _EXPECTED = {
        pr.thread_replies.APPLIED_REPLY_PREFIX: None,
        pr.thread_replies.ADDRESSED_REPLY_PREFIX: FixOutcome.ALREADY_ADDRESSED,
        pr.thread_replies.DISMISSED_REPLY_PREFIX: None,
        pr.thread_replies.DEFERRED_REPLY_PREFIX: FixOutcome.DEFERRED,
    }

    def test_every_reply_prefix_is_accounted_for(self):
        """Read through `_action_outcome`, not by key equality.

        `DEFERRED_REPLY_PREFIX` is "Deferred:" and the Action table's key is
        "Deferred" — a proper prefix, so a membership test would report a
        disagreement that does not exist.
        """
        for prefix, expected in self._EXPECTED.items():
            assert pr.summary_model.action_outcome(prefix) is expected, prefix

    def test_the_overlap_is_exactly_two_openings(self):
        """Pins the shape, so gaining or losing an overlap is a failing test.

        Adding "Applied" to the Action table, or dropping "Deferred" from it,
        both change what one surface treats as ours and neither would otherwise
        be noticed.
        """
        overlapping = [p for p, o in self._EXPECTED.items() if o is not None]
        assert len(overlapping) == 2


class TestActionCellOutcome:
    """Which outcome a published Action cell states, under the wording drift.

    One outcome is written several ways across rounds — a fix reported with a
    commit one round and without one the next — so the cell's text cannot stand
    in for the outcome it reports. Only the outcome tells a round that changed a
    row from one that merely re-rendered it.
    """

    def test_every_fix_status_reads_as_fixed(self):
        """Every wording but one, which says the fix landed out of the pass's reach."""
        for status in CommitStatus:
            expected = (
                FixOutcome.SETTLED_ELSEWHERE if status == CommitStatus.RECONCILED
                else FixOutcome.FIXED
            )
            cp = pr.attribution.CommitPushResult("9f2e1a0", status, "")
            assert pr.summary_model.action_outcome(
                pr.summary_row.fixed_status_text(cp, "owner/repo")) is expected
            bare = pr.attribution.CommitPushResult(None, status, "")
            assert pr.summary_model.action_outcome(
                pr.summary_row.fixed_status_text(bare, "owner/repo")) is expected

    def test_every_cell_a_status_builder_can_emit_is_a_live_wording(self):
        """A wording with no member reads as hand-written and freezes its row.

        Swept over the builders rather than listed, because the list is what
        goes stale. The assertion is `ActionCell` and not merely "recognised":
        a builder emitting a `RetiredActionCell` opening parses fine and so
        would pass the weaker test, while meaning the wording was retired out
        from under a live caller.
        """
        entry = CommentItem(id="t1", summary="s", file="a.py", line=1)
        settled = CommentItem(id="t2", summary="s", file="a.py", line=1,
                              settled_by=SettledBy.RECONCILIATION)
        cells = [
            pr.summary_row.fixed_status_for(e, pr.attribution.CommitPushResult(sha, status, ""), "owner/repo")
            for status in CommitStatus
            for sha in ("9f2e1a0", None)
            for e in (entry, settled)
        ]
        cells += [
            pr.summary_row.addressed_status_for(
                pr.attribution.AddressedFraming(in_response=r, sha=sha), "owner/repo",
            )
            for r in (True, False)
            for sha in ("9f2e1a0", "")
        ]
        cells += [ActionCell.deferred(i, u) for i, u in _DEFERRED_ARGS]
        cells += [pr.summary_model.HumanReason.prose_for(r) for r in (
            *(m.value for m in pr.summary_model.HumanReason), "wat_is_this", "")]
        cells += [ActionCell.DISMISSED, ActionCell.RECONCILED]
        stale = [c for c in cells
                 if not isinstance(pr.summary_model._ActionVocabulary.matching(c), ActionCell)]
        assert stale == []

    def test_a_satisfied_row_reported_as_a_fix_carries_the_hedge(self):
        """The table and the reply beside it must not disagree.

        A satisfied row whose commit postdates the comment reports as a fix and
        counts as one, so an unverified one owes the same `(unverified)` suffix
        the fixed rows carry. Without the kwarg the cell claims the fix plainly
        while the reply hedges it.
        """
        cell = pr.summary_row.addressed_status_for(
            pr.attribution.AddressedFraming(in_response=True, sha="9f2e1a0"),
            "owner/repo", verified=False,
        )
        assert "(unverified)" in cell

    def test_a_row_whose_code_predates_the_comment_never_hedges(self):
        """Not a fix and not a claim the gate could check.

        `ALREADY_ADDRESSED` is the reviewer's own observation confirmed; there
        is nothing for a hedge to weaken, and adding one would caveat rows that
        never ran a gate.
        """
        cell = pr.summary_row.addressed_status_for(
            pr.attribution.AddressedFraming(in_response=False, sha="9f2e1a0"),
            "owner/repo", verified=False,
        )
        assert "(unverified)" not in cell

    def test_no_live_wording_is_unreachable_from_the_builders(self):
        """A member nobody emits is a retired wording still filed as live.

        The inverse of the sweep above, and the half that has no other guard:
        a wording drops out of use silently, because nothing fails when a
        builder stops producing one. "Added to the PR description (no commit)"
        sat in the live table for as long as it did for exactly that reason.
        """
        entry = CommentItem(id="t1", summary="s", file="a.py", line=1)
        settled = CommentItem(id="t2", summary="s", file="a.py", line=1,
                              settled_by=SettledBy.RECONCILIATION)
        emitted = {
            pr.summary_model._ActionVocabulary.matching(
                pr.summary_row.fixed_status_for(
                    e, pr.attribution.CommitPushResult(sha, status, ""), "owner/repo"))
            for status in CommitStatus
            for sha in ("9f2e1a0", None)
            for e in (entry, settled)
        }
        emitted |= {
            pr.summary_model._ActionVocabulary.matching(
                pr.summary_row.addressed_status_for(
                    pr.attribution.AddressedFraming(in_response=r, sha=sha), "owner/repo"))
            for r in (True, False)
            for sha in ("9f2e1a0", "")
        }
        # The three the renderer writes directly, and the five HumanReason names.
        emitted |= {ActionCell.DISMISSED, ActionCell.DEFERRED, ActionCell.RECONCILED}
        emitted |= {m.cell for m in pr.summary_model.HumanReason}
        assert set(ActionCell) - emitted == set()

    def test_a_fix_reported_two_ways_reads_the_same(self):
        """The false positive a cell comparison produces: same outcome, two
        wordings, because one round resolved a commit and the next did not."""
        cited = ActionCell.fixed_in("9f2e1a0", "owner/repo")
        assert pr.summary_model.action_outcome(cited) is pr.summary_model.action_outcome(
            ActionCell.UNATTRIBUTED)

    def test_every_human_reason_prose_reads_as_open(self):
        for reason in pr.summary_model.HumanReason:
            assert pr.summary_model.action_outcome(reason.prose) is FixOutcome.NEEDS_HUMAN

    @pytest.mark.parametrize("cell,outcome", [
        ("Already addressed", FixOutcome.ALREADY_ADDRESSED),
        ("Dismissed (invalid)", FixOutcome.DISMISSED),
        ("Deferred", FixOutcome.DEFERRED),
        ("Deferred → ENG-1", FixOutcome.DEFERRED),
        ("Addressed outside the fix pass", FixOutcome.SETTLED_ELSEWHERE),
        ("Added to the PR description (no commit)", FixOutcome.FIXED),
    ])
    def test_the_literal_cells_read_as_their_outcome(self, cell, outcome):
        assert pr.summary_model.action_outcome(cell) is outcome

    @pytest.mark.parametrize("cell", ["", _HAND_WRITTEN_ACTION_CELL])
    def test_a_cell_we_did_not_write_states_no_outcome(self, cell):
        """None is what keeps a hand-written cell from reading as a round's own
        re-classification — the row is the hand-held path's business, not this."""
        assert pr.summary_model.action_outcome(cell) is None

    def test_no_two_openings_are_equal(self):
        """Two members declared with the same opening are silently one member.

        `StrEnum` aliases the second to the first and raises nothing, so the
        aliased member's outcome is simply gone and every cell it was meant to
        grade reports the survivor's. `@enum.unique` would catch it within one
        enum; nothing catches it across the live and retired halves, which is
        the pair most at risk — retiring a wording without deleting the live
        member is exactly how a duplicate arises.
        """
        openings = [m.value for m in pr.summary_model._ActionVocabulary.members()]
        assert sorted(openings) == sorted(set(openings))

    def test_a_longer_opening_wins_over_the_one_it_extends(self):
        """The scan order, which replaced the rule forbidding overlap.

        An opening extending another is not a collision to legislate away: the
        longer one is the more specific claim about what the cell says, and
        resolving to it is right. Scanning longest-first makes that the answer
        whatever order the members are declared in — the predecessor walked a
        dict in insertion order, so the answer moved when a line did.
        """
        assert pr.summary_model._ActionVocabulary.matching(
            "Fix applied (commit not recorded)") is ActionCell.UNATTRIBUTED
        assert pr.summary_model._ActionVocabulary.matching(
            "Fix committed locally (push failed)") is ActionCell.PUSH_FAILED
        assert pr.summary_model._ActionVocabulary.matching(
            "Fix applied") is RetiredActionCell.APPLIED

    def test_a_dynamic_cell_opens_with_its_own_member(self):
        """The two cells with a variable tail, pinned to the opening they carry.

        This is what makes the vocabulary closed by construction rather than by
        a sweep: a formatter interpolating a literal instead of its member
        could drift from the parse side, and nothing else would notice.
        """
        for verified in (True, False, None):
            cell = ActionCell.fixed_in("9f2e1a0", "owner/repo", verified=verified)
            assert cell.startswith(ActionCell.FIXED_IN)
            assert pr.summary_model.action_outcome(cell) is FixOutcome.FIXED
        for args in _DEFERRED_ARGS:
            cell = ActionCell.deferred(*args)
            assert cell.startswith(ActionCell.DEFERRED)
            assert pr.summary_model.action_outcome(cell) is FixOutcome.DEFERRED

    def test_every_live_opening_is_spelled_exactly(self):
        """The literal spellings, pinned as literals. The only test that can
        catch a reworded cell.

        Every other assertion here reads a wording off the member that wrote
        it, so it holds for whatever the member says — render-then-reparse is
        self-consistent and passes a swapped or reworded cell without comment.
        What a silent reword costs is not a duplicate row: the Action cell is
        excluded from `row_key_from_cells`, so the row keeps its identity. It
        is that every *already published* row carrying the old wording stops
        being recognised as ours, is held by `hand_written_rows`, and freezes
        for the life of the PR — while `RoundScope` loses the outcome it needed
        to see the row change.

        So a reword is a deliberate two-step: pin the new spelling here, and
        move the old one to `RetiredActionCell` so the published rows keep
        parsing.
        """
        assert {m.value: m.outcome for m in ActionCell} == {
            "Fixed in ": FixOutcome.FIXED,
            "Fix applied (commit failed — pre-commit hook?)": FixOutcome.FIXED,
            "Fix applied (commit not recorded)": FixOutcome.FIXED,
            "Fix committed locally (push held pending discussion)": FixOutcome.FIXED,
            "Fix committed locally (push failed)": FixOutcome.FIXED,
            "Fix committed locally (push reported success, remote does not have it)":
                FixOutcome.FIXED,
            "Fix committed and pushed (could not reach the remote to confirm)":
                FixOutcome.FIXED,
            "Fix pending": FixOutcome.FIXED,
            "Addressed outside the fix pass": FixOutcome.SETTLED_ELSEWHERE,
            "Deferred": FixOutcome.DEFERRED,
            "Already addressed": FixOutcome.ALREADY_ADDRESSED,
            "Dismissed (invalid)": FixOutcome.DISMISSED,
            "Contested — needs discussion": FixOutcome.NEEDS_HUMAN,
            "Conflicting reviewer feedback": FixOutcome.NEEDS_HUMAN,
            "Question for the author": FixOutcome.NEEDS_HUMAN,
            "Too complex to auto-fix": FixOutcome.NEEDS_HUMAN,
            "Needs discussion": FixOutcome.NEEDS_HUMAN,
        }

    def test_every_retired_opening_is_spelled_exactly(self):
        """The retired half, where the literal is the entire contract.

        A live wording can at least be derived from the builder that writes it.
        A retired one has no builder, so nothing but this test says what it was
        — and the rows depending on it are on PRs already published.
        """
        assert {m.value: m.outcome for m in RetiredActionCell} == {
            "Fix applied": FixOutcome.FIXED,
            "Fix committed locally": FixOutcome.FIXED,
            "Fix committed and pushed": FixOutcome.FIXED,
            "Added to the PR description (no commit)": FixOutcome.FIXED,
        }

    def test_a_retired_wording_still_parses_and_is_not_emittable(self):
        """A published summary outlives the builder that wrote its cells, so an
        opening no builder produces any more still opens rows on live PRs — and
        must not be reachable from a builder."""
        cell = "Added to the PR description (no commit)"
        assert pr.summary_model.is_generated_action(cell) is True
        assert pr.summary_model.action_outcome(cell) is FixOutcome.FIXED
        assert pr.summary_model._ActionVocabulary.matching(cell) is (
            RetiredActionCell.IN_DESCRIPTION)
        assert "IN_DESCRIPTION" not in ActionCell.__members__

    def test_the_reconciled_cell_reports_settled_elsewhere_from_a_fixed_row(self):
        """What the cell reports is the member's to say, not the bucket's.

        A FIXED-bucket builder renders this deliberately: the cell says the
        work landed somewhere this run cannot name, which is what a
        settled-elsewhere row says. Tying the outcome to the emitting bucket
        would make the row read as a fix and contradict its own wording.
        """
        entry = CommentItem(id="t1", summary="s", file="a.py", line=1)
        cp = pr.attribution.CommitPushResult(None, CommitStatus.RECONCILED, "")
        cell = pr.summary_row.fixed_status_for(entry, cp, "owner/repo")
        assert cell == ActionCell.RECONCILED
        assert pr.summary_model.action_outcome(cell) is FixOutcome.SETTLED_ELSEWHERE

    def test_a_row_with_no_action_cell_is_re_rendered(self):
        """A shape this renderer no longer produces is repaired, not frozen."""
        stub = "| [drop the guard](https://github.com/owner/repo/pull/1#discussion_r111) |"
        fresh = _published_summary(ROUND_ONE_ROW)
        assert pr.summary_scope.hand_written_rows([_published_summary(stub)], fresh) == []

    def test_the_held_row_names_both_halves(self):
        fresh = _published_summary(
            ROUND_ONE_ROW.replace(_GENERATED_ACTION_CELL, "Conflicting reviewer feedback"))
        held = pr.summary_scope.hand_written_rows([_published_summary(HAND_EDITED_ROW)], fresh)
        assert [h.key for h in held] == [pr.published_record.fingerprint("#discussion_r111")]
        assert pr.summary_scope.row_action_cell(held[0].published) == _HAND_WRITTEN_ACTION_CELL
        assert pr.summary_scope.row_action_cell(held[0].replaced_by) == "Conflicting reviewer feedback"

    def test_an_edit_on_an_older_comment_is_still_found(self):
        """Once a round posts its own comment, the edited cell is on one no
        later round targets — reading only the newest hands the row back."""
        fresh = _published_summary(ROUND_ONE_ROW)
        held = pr.summary_scope.hand_written_rows(
            [_published_summary(HAND_EDITED_ROW),
             _published_summary("| [other](https://x/pull/1#discussion_r9) "
                                    "| @kgn | `b.go:1` | Fixed |")],
            fresh)
        assert [pr.summary_scope.row_action_cell(h.published) for h in held] == [
            _HAND_WRITTEN_ACTION_CELL]

    def test_the_newest_comment_wins_the_row(self):
        """Restoring a generated cell on the newest comment hands the row back."""
        fresh = _published_summary(ROUND_ONE_ROW)
        assert pr.summary_scope.hand_written_rows(
            [_published_summary(HAND_EDITED_ROW),
             _published_summary(ROUND_ONE_ROW)], fresh) == []

    def test_a_later_hand_edit_supersedes_the_generated_cell(self):
        """The mirror case — proves the newest-wins rule is not just first-wins."""
        fresh = _published_summary(ROUND_ONE_ROW)
        held = pr.summary_scope.hand_written_rows(
            [_published_summary(ROUND_ONE_ROW),
             _published_summary(HAND_EDITED_ROW)], fresh)
        assert _unmarked(h.published for h in held) == [HAND_EDITED_ROW]


# ── HumanReason ─────────────────────────────────────────────────────────────


class TestHumanReason:
    """The Action cell of a needs-human row reads as prose, never as a token."""

    def _action_cell(self, content, reason):
        """The rendered Action cell for a needs-human entry with this reason."""
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(needs_human=[
                CommentItem(summary="s", file="a.py", line=1, reason=reason),
            ]),
            cp, "owner/repo", 1, {},
        )
        rows = pr.summary_scope.table_rows(body)
        assert len(rows) == 1
        return core.markdown.row_cells(rows[0])[-1]

    @pytest.mark.parametrize("reason", [
        "contested", "conflicting", "question", "complex", "needs_discussion",
    ])
    def test_every_known_reason_renders_as_prose(self, content, reason):
        assert self._action_cell(content, reason) == pr.summary_model.HumanReason(reason).prose

    def test_no_rendered_cell_holds_a_snake_case_token(self, content):
        for member in pr.summary_model.HumanReason:
            cell = self._action_cell(content, member.value)
            assert "_" not in cell
            assert cell[0].isupper()

    def test_an_unknown_reason_falls_back_to_readable_text(self, content):
        assert self._action_cell(content, "wat_is_this") == "Needs discussion"

    def test_an_empty_reason_falls_back_to_readable_text(self, content):
        assert self._action_cell(content, "") == "Needs discussion"

    def test_the_persisted_tokens_stay_stable(self):
        """State files written before the enum existed must still read back."""
        assert [m.value for m in pr.summary_model.HumanReason] == [
            "contested", "conflicting", "question", "complex", "needs_discussion",
        ]

    def test_every_reason_names_an_action_cell(self):
        """The prose is the member's, not a second copy of it.

        These five openings are Action cells like any other and are declared
        with the rest. A reason carrying its own string would put the
        needs-human wordings back outside the vocabulary the parse side reads,
        which is the whole of the defect.
        """
        for reason in pr.summary_model.HumanReason:
            assert isinstance(reason.cell, pr.summary_model.ActionCell)
            assert reason.prose == reason.cell.value
            assert pr.summary_model.action_outcome(reason.prose) is FixOutcome.NEEDS_HUMAN

    def test_triage_stamps_the_token_not_the_prose(self):
        """`reason` stays machine-readable — the state file and JSON report carry it."""
        entries = [
            CommentItem(id="t1", state=ThreadState.CONTESTED),
            CommentItem(id="t2", classification="conflicting"),
            CommentItem(id="t3", classification="question"),
            CommentItem(id="t4", classification="actionable_suggestion",
                        verification="valid", complexity="high"),
            CommentItem(id="t5", classification="actionable_suggestion",
                        verification="needs_discussion"),
        ]
        result = pr.triage_round.classify_entries(entries)
        assert [e.reason for e in result.needs_human] == [
            "contested", "conflicting", "question", "complex", "needs_discussion",
        ]

    def test_a_token_read_back_from_state_renders_as_prose(self, content):
        """The round trip the token stability exists for: state file → Action cell.

        `--finish` rebuilds the needs-human bucket out of persisted
        `ItemOutcome`s rather than the triage entries, so the prose mapping has
        to hold across the rehydration `from_outcome` performs.
        """
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        entry = CommentItem.from_outcome(ItemOutcome(
            id="t1", summary="premise disputed", file="a.py", line=1,
            outcome=FixOutcome.NEEDS_HUMAN, reason=pr.summary_model.HumanReason.CONTESTED.value,
        ))
        body = pr.summary_render.build_summary_body(
            content(needs_human=[entry]), cp, "owner/repo", 1, {})
        rows = pr.summary_scope.table_rows(body)
        assert core.markdown.row_cells(rows[0])[-1] == pr.summary_model.HumanReason.CONTESTED.prose
