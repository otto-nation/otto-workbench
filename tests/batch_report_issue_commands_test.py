"""On-request filing commands in the session report.

The report prints argv; it never files. Revert targets are named per test.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.plan  # noqa: E402
import batch.session_report  # noqa: E402
import gh.client  # noqa: E402
import review.issue  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402
from pr.comments_fix import CloseoutDebt, FixSummary  # noqa: E402
from pr.fix import FixOutcome, FixRecord, ItemOutcome  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402
from review.issue import IssueProviderInfo  # noqa: E402


def _state(*ids, issue_id="", pending=False, worktree="/wt"):
    return PRState(
        identity=PRIdentity(repo="o/r", branch="b1", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(
            fix=FixRecord(items=[
                ItemOutcome(id=i, summary=f"s{i}", file="a.py", line=1,
                            outcome=FixOutcome.DEFERRED, reason="agent could not auto-fix")
                for i in ids
            ]),
            deferred_issue_id=issue_id,
            deferred_issue_pending=pending,
        ),
    )


def _boom(*args, **kwargs):
    raise AssertionError("writer called")


def test_filing_tracks_each_id_and_is_never_first(tmp_path, monkeypatch):
    # Revert: emit --track-all, or prepend the filing command.
    monkeypatch.setattr(batch.plan, "pr_state", lambda d, b: _state("T1", "T2", worktree=tmp_path))
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda *a, **k: IssueProviderInfo(name="github"))
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path))
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: CloseoutDebt(summary=True), tree=None)
    commands = entries[0].commands
    assert commands, "expected closeout plus filing"
    assert not commands[0].files_issue
    filing = [c for c in commands if c.files_issue]
    assert len(filing) == 1
    argv = filing[0].argv
    tracks = [argv[i + 1] for i, a in enumerate(argv) if a == "--track"]
    assert tracks == ["T1", "T2"]
    assert "--track-all" not in argv
    assert Path(argv[argv.index("--repo-dir") + 1]).is_absolute()


def test_report_calls_no_writer(tmp_path, monkeypatch):
    # Revert: drop adopt=False on load_issue_provider.
    monkeypatch.setattr(batch.plan, "pr_state", lambda d, b: _state("T1", worktree=tmp_path))
    monkeypatch.setattr(review.issue, "create_issue", _boom)
    monkeypatch.setattr(review.issue, "adopt_project_review_yml", _boom)
    monkeypatch.setattr(gh.client, "api", _boom)
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path))
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: CloseoutDebt(summary=True), tree=None)
    assert any(c.files_issue for e in entries for c in e.commands)


def test_provider_note_only_when_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.plan, "pr_state", lambda d, b: _state("T1", worktree=tmp_path))
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path))
    run = make_run([item])
    debt = CloseoutDebt(summary=True)

    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda *a, **k: IssueProviderInfo(name=""))
    unresolved = batch.session_report.sessions(run, closeout=lambda d, b: debt, tree=None)
    assert batch.session_report.PROVIDER_NOTE in unresolved[0].notes

    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda *a, **k: IssueProviderInfo(name="github"))
    resolved = batch.session_report.sessions(run, closeout=lambda d, b: debt, tree=None)
    assert batch.session_report.PROVIDER_NOTE not in resolved[0].notes
