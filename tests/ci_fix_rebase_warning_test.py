"""ci-check --fix rebase warning names aborted vs paused from the tree."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.ci_fix  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.target  # noqa: E402
import rebase.types  # noqa: E402


def _ctx():
    ctx = MagicMock()
    ctx.require_worktree.return_value = Path("/tmp/wt")
    return ctx


def _report():
    report = MagicMock()
    report.behind_main = 3
    return report


def test_clean_abort_outcome_logs_aborted_not_paused():
    trail = MagicMock()
    with patch.object(rebase.target, "resolve_target_ref", return_value="origin/main"), \
         patch.object(rebase.commands, "cmd_start",
                      return_value=rebase.types.CONFLICTS_EXIT), \
         patch.object(rebase.inspect, "rebase_in_progress", return_value=False):
        assert rebase.ci_fix.rebase_if_behind(trail, _report(), _ctx()) is False
    trail.warn.assert_called()
    assert trail.warn.call_args[0][0] == "rebase_aborted"
    assert "paused" not in trail.warn.call_args[0][1]
