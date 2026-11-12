"""`--finish --post` refuses when HEAD moved and the commit is not on the remote."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch_git_support import remote_and_clone, remote_tip
from conftest import commit_all, git_out, make_ctx
from review_threads_support import _fix, _no_published_summary  # noqa: F401
from gh.pr_data import PRData
import cli.review_threads
import git.client
import pr.comments
import pr.state
from pr.fix import ItemOutcome
from pr.state import PRIdentity, PRState
import review.closeout
import review.comment_threads


def _item():
    return ItemOutcome(id="t1")


def _save(work, target, *, head_sha, commit_sha="", commit_status=None):
    pr.state.save_state(target, PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1,
            head_sha=head_sha, worktree_root=str(work),
        ),
        fix=_fix(items=[_item()], head_sha=head_sha, commit_sha=commit_sha,
                 commit_status=commit_status),
    ))


def _verified_thread(tid="T_verified"):
    return {
        "id": tid, "isResolved": False, "path": "handler.go", "line": 42,
        "comments": {"totalCount": 3, "nodes": [
            {"id": f"c-{tid}-1", "databaseId": 1000,
             "author": {"login": "alice"}, "body": "Fix this",
             "createdAt": "2026-06-14T12:00:00Z"},
            {"id": f"c-{tid}-2", "databaseId": 1001,
             "author": {"login": "isaacg"}, "body": "Done",
             "createdAt": "2026-06-14T13:00:00Z"},
            {"id": f"c-{tid}-3", "databaseId": 1002,
             "author": {"login": "alice"}, "body": "lgtm",
             "createdAt": "2026-06-14T14:00:00Z"},
        ]},
    }


def _pr_data(threads):
    return PRData(
        viewer_login="isaacg", head_sha="abc1234", head_ref="feat",
        base_ref="main", review_threads=threads, threads_complete=True,
    )


def test_head_moved_unpushed_is_a_push_block(tmp_path, publishing_on):
    pair = remote_and_clone(tmp_path)
    snap = remote_tip(pair.origin, "feat")
    (pair.work / "op.txt").write_text("op\n")
    commit_all(pair.work, "op")
    target = pair.work / "target"
    ctx = make_ctx(repo="owner/repo", branch="feat", pr_number=1,
                   worktree_root=pair.work, head_sha=git_out(pair.work, "rev-parse", "HEAD").strip(),
                   target_dir=target)
    _save(pair.work, target, head_sha=snap)
    with patch.object(review.closeout, "post_pending_fix_replies") as replies:
        block = review.closeout.unpublished_commit(ctx)
    assert block is not None
    assert not block.orphaned
    assert git.client.same_commit(block.sha, git_out(pair.work, "rev-parse", "HEAD").strip(),
                                  cwd=pair.work)
    replies.assert_not_called()


def test_finish_post_refuses_before_resolving(tmp_path, publishing_on, capsys):
    pair = remote_and_clone(tmp_path)
    snap = remote_tip(pair.origin, "feat")
    (pair.work / "op.txt").write_text("op\n")
    commit_all(pair.work, "op")
    target = pair.work / "target"
    ctx = make_ctx(repo="owner/repo", branch="feat", pr_number=1,
                   worktree_root=pair.work, head_sha=git_out(pair.work, "rev-parse", "HEAD").strip(),
                   target_dir=target)
    _save(pair.work, target, head_sha=snap)
    args = cli.review_threads.build_parser().parse_args(["--finish", "--post"])
    with patch.object(review.comment_threads, "fetch_pr_data",
                      return_value=_pr_data([_verified_thread()])), \
            patch.object(pr.comments, "resolve_thread") as resolve, \
            patch.object(review.closeout, "finish_deferred_work") as fin:
        code = review.comment_threads.run_threads(MagicMock(), args, ctx)
    captured = capsys.readouterr()
    last = captured.err.strip().splitlines()[-1]
    assert code == 1
    assert "nothing was published: push" in last
    assert captured.out.strip() == ""
    resolve.assert_not_called()
    fin.assert_not_called()


def test_a_pushed_commit_is_not_a_block(tmp_path, publishing_on):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    target = pair.work / "target"
    ctx = make_ctx(repo="owner/repo", branch="feat", pr_number=1,
                   worktree_root=pair.work, head_sha=tip, target_dir=target)
    _save(pair.work, target, head_sha=tip, commit_sha=tip, commit_status="pushed")
    assert review.closeout.unpublished_commit(ctx) is None


def test_bare_finish_prints_no_json(tmp_path, capsys):
    work = tmp_path / "wt"
    (work / ".git").mkdir(parents=True)
    ctx = make_ctx(repo="owner/repo", pr_number=1, branch="feat",
                   worktree_root=work, head_sha="abc1234",
                   target_dir=tmp_path / "target")
    args = cli.review_threads.build_parser().parse_args(["--finish"])
    with patch.object(review.comment_threads, "fetch_pr_data",
                      return_value=_pr_data([])), \
            patch.object(review.closeout, "finish_deferred_work", return_value=True):
        code = review.comment_threads.run_threads(MagicMock(), args, ctx)
    assert code == 0
    assert capsys.readouterr().out.strip() == ""
