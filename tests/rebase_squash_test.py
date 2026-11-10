"""Decision tests for `rebase.squash.plan` — when the replay is folded, and the message."""

import os
import sys
from pathlib import Path
from unittest import mock

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import config.workbench_config  # noqa: E402
import gh.repo_settings  # noqa: E402
import rebase.pr_snapshot  # noqa: E402
import rebase.squash  # noqa: E402

_SQUASH_ONLY = gh.repo_settings.MergeMethods(squash=True, merge=False, rebase=False)
_MERGE_ALLOWED = gh.repo_settings.MergeMethods(squash=True, merge=True, rebase=False)


def _ctx():
    ctx = mock.MagicMock()
    ctx.repo = "owner/repo"
    return ctx


def _policy(value: str) -> None:
    root = Path(os.environ["WORKBENCH_CONFIG_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yml").write_text(f"rebase:\n  squash: {value}\n")


def _root(tmp_path) -> Path:
    repo = init_repo(tmp_path / "repo")
    (repo / "root.txt").write_text("root\n")
    git_in(repo, "add", "root.txt")
    git_in(repo, "commit", "-q", "-m", "chore: root")
    git_in(repo, "checkout", "-q", "-b", "feat")
    return repo


def _commit(repo: Path, path: str, body: str, message: str, *, author: str = "") -> None:
    (repo / path).write_text(body)
    git_in(repo, "add", path)
    extra = ("--author", author) if author else ()
    git_in(repo, "commit", "-q", *extra, "-m", message)


def _plan(repo: Path, *, snapshot=None, trail=None, replay_from=""):
    return rebase.squash.plan(
        str(repo), _ctx(), target_ref="main", replay_from=replay_from,
        snapshot=snapshot, trail=trail,
    )


def _body(repo: Path, sha: str) -> str:
    return git_out(repo, "log", "-1", "--format=%B", sha)


def test_fewer_than_two_commits_reads_neither_config_nor_github(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: a")
    with mock.patch.object(config.workbench_config, "load_config_or_default") as cfg, \
         mock.patch.object(gh.repo_settings, "merge_methods") as methods:
        assert _plan(repo) is None
    cfg.assert_not_called()
    methods.assert_not_called()


def test_never_policy_does_not_squash(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: a")
    _commit(repo, "b.txt", "b\n", "feat: b")
    _policy("never")
    trail = mock.MagicMock()
    with mock.patch.object(gh.repo_settings, "merge_methods") as methods:
        assert _plan(repo, trail=trail) is None
    methods.assert_not_called()
    reason = trail.decision.call_args.kwargs["reason"]
    assert reason is rebase.squash.SquashSkip.NEVER


def test_always_squashes_without_asking_github(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: a")
    _commit(repo, "b.txt", "b\n", "feat: b")
    _policy("always")
    with mock.patch.object(gh.repo_settings, "merge_methods") as methods:
        planned = _plan(repo)
    methods.assert_not_called()
    assert planned is not None
    assert planned.count == 2
    assert planned.git_config()["rebase.missingCommitsCheck"] == "ignore"
    assert planned.git_config()["rebase.updateRefs"] == "false"
    assert planned.git_env()["GIT_SEQUENCE_EDITOR"] == (
        f"printf 'pick {planned.commit}\\n' >"
    )


def _repeated_conflict(repo: Path) -> None:
    _commit(repo, "foo.txt", "a\n", "feat: 1")
    _commit(repo, "foo.txt", "b\n", "feat: 2")
    git_in(repo, "checkout", "-q", "main")
    (repo / "foo.txt").write_text("main\n")
    git_in(repo, "add", "foo.txt")
    git_in(repo, "commit", "-q", "-m", "chore: main")
    git_in(repo, "checkout", "-q", "feat")


def test_auto_squashes_a_squash_only_repo_with_a_repeated_conflict_file(tmp_path):
    repo = _root(tmp_path)
    _repeated_conflict(repo)
    with mock.patch.object(gh.repo_settings, "merge_methods", return_value=_SQUASH_ONLY):
        planned = _plan(repo)
    assert planned is not None
    assert planned.count == 2


def test_auto_does_not_squash_without_a_repeated_file(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: a")
    _commit(repo, "b.txt", "b\n", "feat: b")
    git_in(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main-a\n")
    (repo / "b.txt").write_text("main-b\n")
    git_in(repo, "add", "a.txt", "b.txt")
    git_in(repo, "commit", "-q", "-m", "chore: main")
    git_in(repo, "checkout", "-q", "feat")
    trail = mock.MagicMock()
    with mock.patch.object(gh.repo_settings, "merge_methods") as methods:
        assert _plan(repo, trail=trail) is None
    methods.assert_not_called()
    assert trail.decision.call_args.kwargs["reason"] is (
        rebase.squash.SquashSkip.NO_REPEATED_CONFLICT
    )


def test_auto_does_not_squash_when_merge_commits_are_allowed(tmp_path):
    repo = _root(tmp_path)
    _repeated_conflict(repo)
    trail = mock.MagicMock()
    with mock.patch.object(gh.repo_settings, "merge_methods", return_value=_MERGE_ALLOWED):
        assert _plan(repo, trail=trail) is None
    assert trail.decision.call_args.kwargs["reason"] is (
        rebase.squash.SquashSkip.OTHER_MERGE_METHODS
    )


def test_auto_fails_safe_when_merge_settings_are_unreadable(tmp_path):
    repo = _root(tmp_path)
    _repeated_conflict(repo)
    trail = mock.MagicMock()
    with mock.patch.object(gh.repo_settings, "merge_methods", return_value=None):
        assert _plan(repo, trail=trail) is None
    trail.warn.assert_called()
    assert trail.warn.call_args.args[:2] == (
        "squash", "merge settings unreadable — not squashing",
    )
    assert trail.decision.call_args.kwargs["reason"] is (
        rebase.squash.SquashSkip.SETTINGS_UNREAD
    )


def test_auto_skips_a_multi_author_branch(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "foo.txt", "a\n", "feat: 1")
    _commit(
        repo, "foo.txt", "b\n", "feat: 2",
        author="Other <other@example.com>",
    )
    git_in(repo, "checkout", "-q", "main")
    (repo / "foo.txt").write_text("main\n")
    git_in(repo, "add", "foo.txt")
    git_in(repo, "commit", "-q", "-m", "chore: main")
    git_in(repo, "checkout", "-q", "feat")
    trail = mock.MagicMock()
    with mock.patch.object(gh.repo_settings, "merge_methods") as methods:
        assert _plan(repo, trail=trail) is None
    methods.assert_not_called()
    assert trail.decision.call_args.kwargs["reason"] is (
        rebase.squash.SquashSkip.MULTIPLE_AUTHORS
    )


def test_message_uses_the_pr_title_then_bullets_then_footers(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: one\n\nBREAKING CHANGE: gone\n")
    _commit(repo, "b.txt", "b\n", "feat: two\n\nNot-Breaking: kept - still exported\n")
    _policy("always")
    snapshot = rebase.pr_snapshot.PRSnapshot(state="OPEN", title="feat: from the pr")
    planned = _plan(repo, snapshot=snapshot)
    assert planned is not None
    body = _body(repo, planned.commit)
    assert body.startswith("feat: from the pr\n\n- feat: one\n- feat: two\n")
    assert "BREAKING CHANGE: gone" in body
    assert "Not-Breaking: kept - still exported" in body


def test_message_falls_back_to_the_first_subject_without_a_snapshot(tmp_path):
    repo = _root(tmp_path)
    _commit(repo, "a.txt", "a\n", "feat: one")
    _commit(repo, "b.txt", "b\n", "feat: two")
    _policy("always")
    planned = _plan(repo, snapshot=None)
    assert planned is not None
    body = _body(repo, planned.commit)
    assert body.startswith("feat: one\n\n- feat: one\n- feat: two\n")


def test_breaking_change_and_not_breaking_footers_survive(tmp_path):
    repo = _root(tmp_path)
    _commit(
        repo, "a.txt", "a\n",
        "feat: one\n\nbody of one\n\nBREAKING CHANGE: the api\n",
    )
    _commit(
        repo, "b.txt", "b\n",
        "feat: two\n\nNot-Breaking: old-name - still on the surface\n"
        "BREAKING CHANGE: the api\n",
    )
    _policy("always")
    planned = _plan(repo)
    assert planned is not None
    body = _body(repo, planned.commit)
    assert body.count("BREAKING CHANGE: the api") == 1
    assert "Not-Breaking: old-name - still on the surface" in body
    bullets, _, footers = body.strip().partition("\n\n- feat: one\n- feat: two\n\n")
    assert bullets == "feat: one"
    assert "BREAKING CHANGE: the api" in footers
