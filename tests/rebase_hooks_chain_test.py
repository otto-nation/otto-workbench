"""`pr rebase` chains workbench hooks when a repo sets its own hooksPath."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.rewrites  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.hooks_chain  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.stash  # noqa: E402
import rebase.types  # noqa: E402


def _full(repo: Path, rev: str = "HEAD") -> str:
    return git_out(repo, "rev-parse", rev).strip()


def _commit(repo: Path, name: str, content: str | None = None) -> str:
    (repo / name).write_text(content if content is not None else f"{name}\n")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", f"add {name}")
    return _full(repo)


def _local_hooks(repo: Path) -> Path:
    hooks = repo / ".githooks"
    hooks.mkdir()
    git_in(repo, "config", "core.hooksPath", ".githooks")
    return hooks


def _hook(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _ctx(branch: str = "feat"):
    ctx = mock.MagicMock()
    ctx.branch = branch
    ctx.current_branch = branch
    ctx.repo = "owner/repo"
    ctx.pr_number = None
    return ctx


def _fresh(repo: Path) -> int:
    """`lifecycle.fresh` with landing stubbed out so only the replay runs."""
    with mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        return rebase.lifecycle.fresh(
            str(repo), _ctx(), rebase.types.RunMode.REBASE_ONLY,
            force=True, target_ref="main",
        )


@pytest.fixture
def repo(tmp_path, live_git_hooks):
    work = init_repo(tmp_path / "work")
    _commit(work, "base.txt")
    return work


def test_conflict_free_replay_is_recorded(repo):
    """A local hooksPath with no post-rewrite still records through `fresh`."""
    git_in(repo, "checkout", "-q", "-b", "feat")
    old = _commit(repo, "feat.txt")
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "main.txt")
    git_in(repo, "checkout", "-q", "feat")
    _local_hooks(repo)

    assert _fresh(repo) == 0
    assert old in git.rewrites.load(repo)
    assert git.rewrites.load(repo)[old] == [_full(repo)]


def test_conflict_resolved_continue_is_recorded(repo):
    """`rebase_continue` is the other call site; drop its wrap and this fails."""
    (repo / "f.txt").write_text("a\nb\nc\n")
    git_in(repo, "add", "f.txt")
    git_in(repo, "commit", "-q", "-m", "shared")
    git_in(repo, "checkout", "-q", "-b", "feat")
    old = _commit(repo, "f.txt", "a\nBRANCH\nc\n")
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "f.txt", "a\nMAIN\nc\n")
    git_in(repo, "checkout", "-q", "feat")
    _local_hooks(repo)

    started = git.client.run("rebase", "main", cwd=str(repo))
    assert not started.ok
    (repo / "f.txt").write_text("a\nMAIN\nBRANCH\nc\n")
    git_in(repo, "add", "f.txt")
    continued = rebase.lifecycle.rebase_continue(str(repo))
    assert continued.ok
    assert old in git.rewrites.load(repo)


def test_repo_post_rewrite_still_runs_with_the_same_stdin(repo):
    git_in(repo, "checkout", "-q", "-b", "feat")
    old = _commit(repo, "feat.txt")
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "main.txt")
    git_in(repo, "checkout", "-q", "feat")
    hooks = _local_hooks(repo)
    seen = repo / "prw.stdin"
    _hook(hooks / "post-rewrite",
          f'#!/usr/bin/env bash\ncat > "{seen}"\necho "$1" >> "{seen}"\n')

    assert _fresh(repo) == 0
    lines = seen.read_text().splitlines()
    assert f"{old} {_full(repo)}" in lines
    assert "rebase" in lines


def test_repo_prepare_commit_msg_and_other_hooks_still_run(repo):
    (repo / "f.txt").write_text("a\nb\nc\n")
    git_in(repo, "add", "f.txt")
    git_in(repo, "commit", "-q", "-m", "shared")
    git_in(repo, "checkout", "-q", "-b", "feat")
    _commit(repo, "f.txt", "a\nBRANCH\nc\n")
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "f.txt", "a\nMAIN\nc\n")
    git_in(repo, "checkout", "-q", "feat")
    hooks = _local_hooks(repo)
    pcm = repo / "pcm.ran"
    pre = repo / "prerebase.ran"
    _hook(hooks / "prepare-commit-msg",
          f'#!/usr/bin/env bash\necho prepare > "{pcm}"\n')
    _hook(hooks / "pre-rebase",
          f'#!/usr/bin/env bash\necho pre-rebase > "{pre}"\n')

    with rebase.hooks_chain.override(str(repo)) as extra:
        wrapper = Path(extra["core.hooksPath"])
        assert (wrapper / "pre-rebase").is_symlink()
        assert (wrapper / "pre-rebase").resolve() == (hooks / "pre-rebase").resolve()
        assert (wrapper / "post-rewrite").is_file()
        assert (wrapper / "prepare-commit-msg").is_file()
        assert (wrapper / "pre-commit").is_file()

    started = git.client.run("rebase", "main", cwd=str(repo))
    assert not started.ok
    (repo / "f.txt").write_text("a\nMAIN\nBRANCH\nc\n")
    git_in(repo, "add", "f.txt")
    assert rebase.lifecycle.rebase_continue(str(repo)).ok
    assert pcm.read_text().strip() == "prepare"


def test_dev_null_is_not_wrapped_and_warns(repo, capsys):
    git_in(repo, "config", "core.hooksPath", os.devnull)
    with rebase.hooks_chain.override(str(repo)) as extra:
        assert extra == {}
    rebase.hooks_chain.warn_if_shadowed(str(repo))
    err = capsys.readouterr().err
    assert os.devnull in err
    assert "disabled" in err
    assert "not recorded" in err


def test_workbench_dir_already_active_leaves_config_unchanged(repo):
    git_in(repo, "config", "core.hooksPath",
           str(rebase.hooks_chain.WORKBENCH_HOOKS_DIR))
    with rebase.hooks_chain.override(str(repo)) as extra:
        assert extra == {}


def test_unset_hooks_path_leaves_config_unchanged(tmp_path, live_git_hooks):
    work = init_repo(tmp_path / "plain")
    _commit(work, "base.txt")
    with rebase.hooks_chain.override(str(work)) as extra:
        assert extra == {}


def test_shadowed_dir_warns_and_names_what_hand_run_git_misses(repo, capsys):
    _local_hooks(repo)
    rebase.hooks_chain.warn_if_shadowed(str(repo))
    err = capsys.readouterr().err
    assert ".githooks" in err
    assert "history following" in err
    assert "prepare-commit-msg" in err
    assert "post-rewrite" in err
    assert "pre-push" in err
    assert "pr rebase" in err


def test_cmd_start_warns_at_the_start():
    """Dropping the call from cmd_start would silence the operator warning."""
    with mock.patch.object(rebase.hooks_chain, "warn_if_shadowed") as warn, \
         mock.patch.object(rebase.inspect, "rebase_in_progress",
                           return_value=False), \
         mock.patch.object(rebase.stash, "auto_stash", return_value=False), \
         mock.patch.object(rebase.lifecycle, "fresh", return_value=0):
        rebase.commands.cmd_start(
            "/fake", mock.MagicMock(), rebase.types.RunMode.PUSH,
            target_ref="main",
        )
    warn.assert_called_once_with("/fake")
