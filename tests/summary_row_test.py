"""pr.summary_row: status text, settled rows and cell escaping."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary, _published_summary  # noqa: E402
import core.markdown
import pr.attribution
import pr.summary_model
import pr.summary_scope
import pr.summary_row
from pr.summary_model import ActionCell
from pr.fix import RECONCILED_REASON, SettledBy
from pr.thread_models import CommentItem


# ── _fixed_status_text ──────────────────────────────────────────────────────


class TestFixedStatusText:
    """Test status text rendering for each CommitPushResult state."""

    def test_pushed(self):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "Fixed in" in text
        assert "abc1234" in text
        assert "push failed" not in text

    def test_push_failed_says_the_commit_exists(self):
        """"Fix pending" would deny a commit that is sitting in the worktree."""
        cp = pr.attribution.CommitPushResult("abc1234", "push_failed", "rejected")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "push failed" in text
        assert "abc1234" not in text

    def test_push_held_says_why_it_is_waiting(self):
        cp = pr.attribution.CommitPushResult("abc1234", "push_held", "")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "push held" in text
        assert "abc1234" not in text

    def test_push_lost_says_the_remote_does_not_have_it(self):
        """The operator saw a clean push, so "push failed" would read as wrong."""
        cp = pr.attribution.CommitPushResult("abc1234", "push_lost", "")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "remote does not have it" in text
        assert "abc1234" not in text

    def test_push_unverified_does_not_claim_the_remote_answered(self):
        """An unreachable remote said neither yes nor no — say only that."""
        cp = pr.attribution.CommitPushResult("abc1234", "push_unverified", "")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "could not reach the remote" in text
        assert "does not have it" not in text
        assert "abc1234" not in text

    def test_no_changes_claims_nothing_about_why(self):
        """"Fixed" and "nothing committed" cannot both be true."""
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert text == ActionCell.UNATTRIBUTED
        assert "no commit needed" not in text

    def test_commit_failed(self):
        cp = pr.attribution.CommitPushResult(None, "commit_failed", "hook error")
        text = pr.summary_row.fixed_status_text(cp, "owner/repo")
        assert "commit failed" in text
        assert "pre-commit" in text


class TestSettledRowsAreNotCreditedToThePass:
    """The fix pass did not land this work, so its commit must not be named for it."""

    def test_an_uncitable_settled_row_says_the_work_was_handled(self):
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            settled_by=SettledBy.OPERATOR)
        cp = pr.attribution.CommitPushResult("aaa1111", "pushed", "")
        cell = pr.summary_row.fixed_status_for(entry, cp, "owner/repo")
        assert cell == ActionCell.RECONCILED
        assert cell != ActionCell.UNATTRIBUTED

    def test_a_settled_row_that_resolved_a_commit_cites_that_one(self):
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            settled_by=SettledBy.OPERATOR, commit_sha="bbb2222")
        cp = pr.attribution.CommitPushResult("aaa1111", "pushed", "")
        cell = pr.summary_row.fixed_status_for(entry, cp, "owner/repo")
        assert "bbb2222" in cell
        assert "aaa1111" not in cell

    def test_the_reason_wording_no_longer_decides_the_cell(self):
        """Provenance decides, not prose.

        The reason text used to be the whole signal, so an entry the pass itself
        settled would claim the work went somewhere else on the strength of a
        sentence written for a reviewer to read.
        """
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            reason=RECONCILED_REASON)
        cp = pr.attribution.CommitPushResult("aaa1111", "pushed", "")
        assert pr.summary_row.fixed_status_for(entry, cp, "owner/repo") == (
            ActionCell.UNATTRIBUTED
        )


class TestPipesStayInTheirCell:
    """Summary prose is unconstrained; one pipe would shift every later cell."""

    def _row(self, summary, status="Fixed"):
        entry = CommentItem(id="t1", summary=summary, reviewer="kgn", file="f.go", line=2)
        return pr.summary_row.render_row(pr.summary_row.row_cells_for(entry, status, {}, "owner/repo", 1))

    def test_a_summary_pipe_does_not_add_a_cell(self):
        row = self._row("use a || b, not a | b")
        assert len(core.markdown.row_cells(row)) == len(pr.summary_model.TABLE_COLUMNS)

    def test_a_status_pipe_does_not_add_a_cell(self):
        row = self._row("plain", status="Deferred — a | b")
        assert len(core.markdown.row_cells(row)) == len(pr.summary_model.TABLE_COLUMNS)

    def test_the_fallback_key_survives_a_summary_pipe(self):
        deferred = self._row("use a | b", status="Deferred")
        fixed = self._row("use a | b", status="Fixed in `abc`")
        assert pr.summary_scope.row_key(deferred) == pr.summary_scope.row_key(fixed)
        assert pr.summary_scope.carried_over_rows(
            _published_summary(deferred), _published_summary(fixed)) == []
