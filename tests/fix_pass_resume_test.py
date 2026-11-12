"""fix.pass_resume helpers: overlay, deleted sentinel, suite re-apply."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.pass_resume  # noqa: E402
import fix.suite  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.fix_checkpoint import EngineProgress  # noqa: E402


def test_edited_since_detects_a_restored_deletion(tmp_path):
    recorded = {"gone.py": fix.pass_resume.DELETED}
    live = {}  # restored to match HEAD: not in the agent-files map
    with patch.object(fix.pass_resume, "agent_files", return_value=live):
        assert fix.pass_resume.edited_since(tmp_path, recorded) == ["gone.py"]


def test_edited_since_still_deleted_is_unchanged(tmp_path):
    recorded = {"gone.py": fix.pass_resume.DELETED}
    with patch.object(fix.pass_resume, "agent_files", return_value=recorded):
        assert fix.pass_resume.edited_since(tmp_path, recorded) == []


def test_prior_answers_overlay_only_when_asked(tmp_path):
    progress = EngineProgress(answers=[
        ItemOutcome(id="i0", outcome=FixOutcome.FIXED, verified=False),
    ])
    tracking = tmp_path / "fix-tracking.md"
    tracking.write_text("")
    with patch("fix.tracking.parse", return_value=[
        ItemOutcome(id="i0", outcome=FixOutcome.FIXED, reason="from file"),
    ]):
        overlaid = fix.pass_resume.prior_answers(progress, tracking, overlay=True)
        assert overlaid["i0"].reason == "from file"
        owned = fix.pass_resume.prior_answers(progress, tracking, overlay=False)
        assert owned["i0"].verified is False
        assert owned["i0"].reason == ""


def test_apply_suite_if_missing_does_not_double_detail():
    outcomes = [
        ItemOutcome(
            id="i0", outcome=FixOutcome.FIXED, verified=False,
            verify_detail=fix.suite.RED_DETAIL,
        ),
    ]
    result = fix.suite.SuiteResult(status=fix.suite.SuiteStatus.RED, command="true")
    fix.pass_resume.apply_suite_if_missing(outcomes, result)
    assert outcomes[0].verify_detail == fix.suite.RED_DETAIL
