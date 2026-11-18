"""A fresh tree/closeout read drops stale recorded publish findings.

Revert: `_apply_plan` keeping READY_TO_PUSH when the plan is ok with no
pushes; `_session` not dropping CLOSEOUT_OWED when debt is not owed.
"""

import sys
from pathlib import Path

from conftest import commit_all, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.plan  # noqa: E402
import batch.session_report  # noqa: E402
from batch.model import Finding, FindingReason  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch_report_support import SHA, make_item, make_run, no_debt  # noqa: E402
from pr.domains import RebaseSummary  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def _even(item):
    return TreeState(local=item.remote_sha, remote=item.remote_sha,
                     divergence=RefDivergence(ahead=0, behind=0, comparable=True),
                     checked_out=item.branch)


def _moved(item):
    return TreeState(local=item.remote_sha, remote="b" * 40,
                     divergence=RefDivergence(ahead=0, behind=1, comparable=True),
                     checked_out=item.branch)


def _reasons(entries):
    return [f.reason for e in entries for f in e.findings]


def test_fresh_even_tree_drops_ready_to_push_and_unowed_closeout(tmp_path):
    # Revert: skip _drop of READY_TO_PUSH when plan.ok and not plan.pushes.
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat")
    item.findings = [
        Finding(FindingReason.READY_TO_PUSH, "publish"),
        Finding(FindingReason.CLOSEOUT_OWED, "comments"),
        Finding(FindingReason.CI_FAILING, "ci"),
    ]
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=_even)
    assert FindingReason.READY_TO_PUSH not in _reasons(entries)
    assert FindingReason.CLOSEOUT_OWED not in _reasons(entries)
    assert FindingReason.CI_FAILING in _reasons(entries)


def test_fresh_moved_remote_drops_ready_to_push_for_push_refused(tmp_path):
    # Revert: keep READY_TO_PUSH when the fresh plan refuses.
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat",
                     remote_sha=SHA)
    item.findings = [Finding(FindingReason.READY_TO_PUSH, "publish")]
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=_moved)
    assert FindingReason.READY_TO_PUSH not in _reasons(entries)
    assert FindingReason.PUSH_REFUSED in _reasons(entries)


def test_already_published_tree_clears_stored_rebase_and_push_findings(tmp_path):
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat")
    item.findings = [
        Finding(FindingReason.STALE_GENERATED, "rebase"),
        Finding(FindingReason.CONFLICT_HANDWRITTEN, "rebase"),
        Finding(FindingReason.CONFLICT_GENERATED, "rebase"),
        Finding(FindingReason.PUSH_REFUSED, "publish"),
    ]
    published = "b" * 40
    tree = TreeState(
        local=published, remote=published,
        divergence=RefDivergence(ahead=0, behind=0, comparable=True),
        checked_out=item.branch,
    )
    assert published != item.remote_sha
    entries = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: tree)
    assert entries == []


def test_commit_on_top_of_rebase_head_clears_stale_generated(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("one\n")
    commit_all(repo, "base")
    (repo / "a.txt").write_text("two\n")
    commit_all(repo, "rebase tip")
    rebase_head = git_out(repo, "rev-parse", "HEAD").strip()
    (repo / "other.txt").write_text("unrelated\n")
    commit_all(repo, "unrelated follow-up")
    unrelated_head = git_out(repo, "rev-parse", "HEAD").strip()
    (repo / "a.pb.go").write_text("regenerated\n")
    commit_all(repo, "regenerate")
    head_after = git_out(repo, "rev-parse", "HEAD").strip()
    wt = str(repo)
    pre = "p" * 40

    def _tree(local):
        return TreeState(
            local=local, remote=SHA,
            divergence=RefDivergence(ahead=2, behind=1, comparable=True),
            checked_out="feat",
        )

    item = make_item(1, worktree=wt, repo_dir=wt, branch="feat", remote_sha=SHA,
                     pre_rebase_head=pre)
    item.findings = [Finding(FindingReason.STALE_GENERATED, "rebase", files=["a.pb.go"],
                             data={"rebase_head": rebase_head})]
    unrelated = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(unrelated_head))
    assert FindingReason.STALE_GENERATED in _reasons(unrelated)

    item.findings = [Finding(FindingReason.STALE_GENERATED, "rebase", files=["a.pb.go"],
                             data={"rebase_head": rebase_head})]
    after = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(head_after))
    assert FindingReason.STALE_GENERATED not in _reasons(after)

    item.findings = [Finding(FindingReason.STALE_GENERATED, "rebase", files=["a.pb.go"],
                             data={"rebase_head": rebase_head})]
    at_head = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(rebase_head))
    assert FindingReason.STALE_GENERATED in _reasons(at_head)

    item.findings = [Finding(
        FindingReason.STALE_GENERATED, "rebase", files=["a.pb.go"],
        created_at="2000-01-01T00:00:00+00:00")]
    legacy = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(head_after))
    assert FindingReason.STALE_GENERATED not in _reasons(legacy)


def test_clean_rebase_state_clears_stored_conflict_finding(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("one\n")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2000-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:00+00:00")
    commit_all(repo, "base")
    (repo / "a.txt").write_text("two\n")
    commit_all(repo, "rebase tip")
    rebase_head = git_out(repo, "rev-parse", "HEAD").strip()
    updated_at = "2000-01-01T00:00:00+00:00"
    wt = str(repo)
    pre = "p" * 40

    def _tree(local):
        return TreeState(
            local=local, remote=SHA,
            divergence=RefDivergence(ahead=2, behind=1, comparable=True),
            checked_out="feat",
        )

    def _state(*, files_stale=()):
        return PRState(
            identity=PRIdentity(repo="o/r", branch="feat", pr_number=1,
                                head_sha=rebase_head, worktree_root=wt),
            rebase=RebaseSummary(
                status="clean", aborted=False,
                files_stale=list(files_stale),
                updated_at=updated_at,
            ),
        )

    monkeypatch.setattr(batch.plan, "pr_state", lambda repo_dir, branch: _state())
    item = make_item(1, worktree=wt, repo_dir=wt, branch="feat", remote_sha=SHA,
                     pre_rebase_head=pre)
    item.findings = [
        Finding(FindingReason.CONFLICT_HANDWRITTEN, "rebase"),
        Finding(FindingReason.CONFLICT_GENERATED, "rebase"),
    ]
    cleared = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(rebase_head))
    assert FindingReason.CONFLICT_HANDWRITTEN not in _reasons(cleared)
    assert FindingReason.CONFLICT_GENERATED not in _reasons(cleared)

    monkeypatch.setattr(
        batch.plan, "pr_state",
        lambda repo_dir, branch: _state(files_stale=["x.pb.go"]))
    item.findings = []
    derived = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(rebase_head))
    assert FindingReason.STALE_GENERATED in _reasons(derived)

    (repo / "x.pb.go").write_text("regenerated\n")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:01+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2000-01-01T00:00:01+00:00")
    commit_all(repo, "follow-up")
    head_after = git_out(repo, "rev-parse", "HEAD").strip()
    item.findings = []
    after = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: _tree(head_after))
    assert FindingReason.STALE_GENERATED not in _reasons(after)


def test_offline_replan_keeps_stored_push_refused(tmp_path):
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat",
                     remote_sha=SHA)
    item.findings = [Finding(FindingReason.PUSH_REFUSED, "publish",
                             data={"refusal": "remote_moved"})]
    tree = TreeState(local=SHA, remote=SHA,
                     divergence=RefDivergence(ahead=0, behind=0, comparable=True),
                     checked_out=item.branch, observed=False)
    entries = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=lambda i: tree)
    assert FindingReason.PUSH_REFUSED in _reasons(entries)
