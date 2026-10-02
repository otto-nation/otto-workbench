"""What `rebase.survival.audit` reports a resolution as having thrown away.

Pure: four texts in, a verdict out. The fixtures are seven-line files so each
side's change can sit apart from the other's — git merges a change on line 2
and one on line 6 without help, which is what makes losing either of them a
blocking loss rather than a judgement call.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.survival  # noqa: E402
from rebase.survival import LossKind, Side  # noqa: E402

BASE = "a\nb\nc\nd\ne\nf\ng\n"
# The branch's commit: two changes, on lines 2 and 6.
REPLAYED = "a\nB\nc\nd\ne\nF\ng\n"
# The target collides with the line-2 change and adds one of its own on line 4.
TARGET = "a\nBM\nc\nD\ne\nf\ng\n"


def _audit(resolved, *, base=BASE, target=TARGET, replayed=REPLAYED):
    return rebase.survival.audit(
        "f.txt", base=base, target=target, replayed=replayed, resolved=resolved,
    )


def _kinds(audit):
    return [(loss.kind, loss.side, loss.blocking) for loss in audit.losses]


class TestCleanResolutions:
    def test_a_resolution_keeping_every_change_loses_nothing(self):
        assert _audit("a\nBM+B\nc\nD\ne\nF\ng\n").losses == ()

    def test_changes_that_never_collided_survive_gits_own_merge(self):
        replayed = "a\nB\nc\nd\ne\nf\ng\n"
        target = "a\nb\nc\nd\ne\nF\ng\n"

        audit = _audit("a\nB\nc\nd\ne\nF\ng\n", target=target, replayed=replayed)

        assert audit.losses == ()

    def test_a_line_the_replayed_commit_repeated_is_not_read_as_lost(self):
        """Alignment can place a duplicated line one copy over; that is no revert."""
        audit = _audit(
            "foo\nfoo\nbar\n", base="foo\nbar\n", target="foo\nbar\n",
            replayed="foo\nfoo\nbar\n",
        )

        assert audit.losses == ()


class TestWholeFileCheckout:
    def test_checkout_ours_discards_the_replayed_commits_clean_change(self):
        """The incident: `--ours` takes the target's file, and line 6 goes with it."""
        audit = _audit(TARGET)

        assert _kinds(audit) == [(LossKind.FILE_TAKEN_WHOLE, Side.REPLAYED, True)]
        assert audit.blocking == audit.losses

    def test_checkout_theirs_discards_the_targets_clean_change(self):
        audit = _audit(REPLAYED)

        assert _kinds(audit) == [(LossKind.FILE_TAKEN_WHOLE, Side.TARGET, True)]

    def test_a_target_rewrite_winning_every_collision_is_only_advisory(self):
        """No clean change was lost, so taking the target whole is a judgement call."""
        replayed = "a\nB\nc\nd\ne\nf\ng\n"

        audit = _audit(TARGET, replayed=replayed)

        assert _kinds(audit) == [(LossKind.REGION_ONE_SIDED, Side.REPLAYED, False)]
        assert audit.blocking == ()


class TestRevertedHunks:
    def test_a_script_rerun_restoring_one_change_still_reports_the_other(self):
        """`--ours` then a codemod: line 2 comes back, line 6 does not."""
        audit = _audit("a\nB\nc\nD\ne\nf\ng\n")

        hunks = [loss for loss in audit.losses if loss.kind is LossKind.HUNK_REVERTED]
        assert [(loss.side, loss.line, loss.excerpt) for loss in hunks] == [
            (Side.REPLAYED, 6, ("+ F",)),
        ]
        assert all(loss.blocking for loss in hunks)

    def test_a_removed_line_is_quoted_as_the_line_that_was_removed(self):
        # The replayed commit edits line 3 and deletes line 6; the resolution
        # keeps the edit, so it is not either side verbatim.
        replayed = "a\nb\nC\nd\ne\ng\n"
        target = "a\nB\nc\nd\ne\nf\ng\n"

        audit = _audit("a\nB\nC\nd\ne\nf\ng\n", target=target, replayed=replayed)

        assert [(loss.kind, loss.excerpt) for loss in audit.losses] == [
            (LossKind.HUNK_REVERTED, ("- f",)),
        ]

    def test_the_description_names_the_side_and_the_line(self):
        loss = _audit("a\nB\nc\nD\ne\nf\ng\n").blocking[0]

        assert loss.describe() == (
            "the replayed commit's change at line 6 reads as the base again, "
            "though git had merged it cleanly\n      + F"
        )


class TestCollidingRegions:
    def test_a_collision_resolved_to_one_side_is_advisory(self):
        audit = _audit("a\nBM\nc\nD\ne\nF\ng\n")

        assert _kinds(audit) == [(LossKind.REGION_ONE_SIDED, Side.REPLAYED, False)]
        assert audit.advisory[0].excerpt == ("+ B",)

    def test_a_collision_merged_from_both_sides_is_not_reported(self):
        assert _audit("a\nBM and B\nc\nD\ne\nF\ng\n").losses == ()


class TestFilePresence:
    def test_undoing_a_clean_deletion_blocks(self):
        audit = _audit(BASE, target=BASE, replayed=None)

        assert _kinds(audit) == [(LossKind.FILE_PRESENCE, Side.REPLAYED, True)]
        assert audit.losses[0].describe() == (
            "the replayed commit deleted the file, and the resolution undid it"
        )

    def test_dropping_a_file_the_commit_added_blocks(self):
        audit = _audit(None, base=None, target=None, replayed="new\n")

        assert _kinds(audit) == [(LossKind.FILE_PRESENCE, Side.REPLAYED, True)]

    def test_a_deletion_against_an_edit_is_a_judgement_call(self):
        audit = _audit(TARGET, replayed=None)

        assert _kinds(audit) == [(LossKind.FILE_PRESENCE, Side.REPLAYED, False)]

    def test_a_file_the_commit_added_and_the_resolution_kept_is_fine(self):
        assert _audit("new\n", base=None, target=None, replayed="new\n").losses == ()


def test_a_file_past_the_line_cap_is_skipped_rather_than_stalled_on(monkeypatch):
    monkeypatch.setattr(rebase.survival, "MAX_AUDITED_LINES", 10)

    audit = _audit(TARGET)

    assert audit.losses == ()
    assert audit.skipped == "over 10 lines across its versions"
