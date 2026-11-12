"""--settle records a thread that is on the PR but missing from the snapshot."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _fix, _make_state, _no_published_summary  # noqa: E402, F401
from conftest import make_ctx
import pr.state
import pr.settlement
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.thread_models import ReportThread


class TestSettleAdoptsOffSnapshot:
    """An id GitHub still has, that no fix pass recorded, is not a typo."""

    def _ctx(self, tmp_path):
        return make_ctx(
            branch="feature", worktree_root=tmp_path / "wt",
            head_sha="abc1234", target_dir=tmp_path / "target",
        )

    @staticmethod
    def _needs_human(tid="t1"):
        return ItemOutcome(
            id=tid, outcome=FixOutcome.NEEDS_HUMAN,
            summary="contested", file="a.py", line=1,
        )

    def _save(self, ctx, *items):
        pr.state.save_state(ctx.target_dir, _make_state(_fix(list(items))))

    def _reload(self, ctx):
        return pr.state.load_state(ctx.target_dir).fix

    def _thread(self, tid="PRRT_t1", reviewer="alice"):
        return ReportThread(
            id=tid, reviewer=reviewer, file="a.py", line=1,
            comments=[{"body": "please rename the guard"}],
        )

    def test_id_on_the_pr_but_not_in_the_snapshot_is_recorded(self, tmp_path, capsys):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t-other"))
        fetch = MagicMock(return_value={"PRRT_t1": self._thread()})
        assert pr.settlement.run_settle(
            ctx, ["PRRT_t1"], "already_addressed", "", "",
            fetch_threads=fetch,
        ) == 0
        fetch.assert_called_once()
        fix = self._reload(ctx)
        item = next(o for o in fix.fix.items if o.id == "PRRT_t1")
        assert item.outcome is FixOutcome.ALREADY_ADDRESSED
        assert item.settled_by is SettledBy.OPERATOR
        assert fix.replies_pending
        assert fix.summary_deferred
        assert fix.reviewers["PRRT_t1"] == "alice"
        assert "not in the fix snapshot — taken from the PR" in capsys.readouterr().err

    def test_no_snapshot_and_a_known_thread_creates_state(self, tmp_path):
        ctx = self._ctx(tmp_path)
        thread = self._thread("t1")
        assert pr.settlement.run_settle(
            ctx, ["t1"], "already_addressed", "", "",
            fetch_threads=lambda: {"t1": thread},
        ) == 0
        state = pr.state.load_state(ctx.target_dir)
        assert state is not None
        assert state.fix.fix.items[0].id == "t1"
        assert state.fix.fix.items[0].outcome is FixOutcome.ALREADY_ADDRESSED
        assert state.fix.replies_pending
        assert state.fix.summary_deferred

    def test_unknown_both_sides_is_never_recorded(self, tmp_path, capsys):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t-other"))
        assert pr.settlement.run_settle(
            ctx, ["typo"], "already_addressed", "", "",
            fetch_threads=lambda: {"t1": self._thread("t1")},
        ) == 1
        assert "never recorded" in capsys.readouterr().err
        assert [o.id for o in self._reload(ctx).fix.items] == ["t-other"]

    def test_fetch_threads_is_not_called_when_every_id_is_in_the_snapshot(
        self, tmp_path,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        fetch = MagicMock()
        assert pr.settlement.run_settle(
            ctx, ["t1"], "already_addressed", "", "",
            fetch_threads=fetch,
        ) == 0
        fetch.assert_not_called()
        assert self._reload(ctx).fix.items[0].outcome is FixOutcome.ALREADY_ADDRESSED
