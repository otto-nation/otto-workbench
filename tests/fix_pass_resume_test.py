"""fix.pass_resume helpers: overlay, deleted sentinel, suite re-apply."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import commit_all, git_out  # noqa: E402
from review_convergence_support import committed_repo, head  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.pass_resume  # noqa: E402
import fix.suite  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.fix_checkpoint import EngineProgress  # noqa: E402


def test_recorded_agent_files_skips_when_the_pass_does_not_record(tmp_path):
    with patch.object(fix.pass_resume, "agent_files") as files:
        assert fix.pass_resume.recorded_agent_files(
            tmp_path, set(), "HEAD", False) is None
    files.assert_not_called()


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


def test_agent_files_compares_against_base_not_live_head(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    first = head(repo)
    (repo / "a.py").write_text("agent\n")
    blob = git_out(repo, "hash-object", str(repo / "a.py")).strip()
    commit_all(repo, "landed")
    vs_head = fix.pass_resume.agent_files(repo, set())
    vs_before = fix.pass_resume.agent_files(repo, set(), base=first)
    assert vs_head == {}
    assert vs_before == {"a.py": blob}
    assert fix.pass_resume.edited_since(
        repo, {"a.py": blob}, base=first,
    ) == []
    assert fix.pass_resume.edited_since(
        repo, {"a.py": blob},
    ) == ["a.py"]
