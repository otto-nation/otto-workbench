"""Default-mode conflict stop: abort to a clean tree, or continue generated-only."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import commit_all, git_out, init_repo, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend  # noqa: E402
import git.client  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.repo_regen  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import rebase.stash  # noqa: E402
import rebase.types  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_cached_repo_reads():
    rebase.repo_regen.clear_caches()
    yield
    rebase.repo_regen.clear_caches()


def _write(repo: Path, name: str, body: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _conflicted(
    tmp_path, files: dict[str, tuple[str, str, str]], *, dirty: bool = False,
) -> tuple[Path, str]:
    """A feat-onto-main rebase paused with conflicts. files: name -> (base, main, feat).

    ``dirty`` leaves an untracked file auto-stashed before the rebase starts,
    since git refuses to stash once the index holds unmerged paths.
    """
    repo = init_repo(tmp_path / "repo")
    for name, (base, _, _) in files.items():
        _write(repo, name, base)
    commit_all(repo, "base")
    git_out(repo, "checkout", "-b", "feat")
    for name, (_, _, feat) in files.items():
        _write(repo, name, feat)
    commit_all(repo, "feat")
    pre = git_out(repo, "rev-parse", "HEAD").strip()
    git_out(repo, "checkout", "main")
    for name, (_, main, _) in files.items():
        _write(repo, name, main)
    commit_all(repo, "main2")
    git_out(repo, "checkout", "feat")
    if dirty:
        _write(repo, "dirty.txt", "unstaged\n")
        assert rebase.stash.auto_stash(str(repo)) is True
    r = git.client.run("rebase", "main", cwd=str(repo))
    assert not r.ok
    return repo, pre


def _ctx(repo: Path):
    return make_ctx(worktree_root=repo, branch="feat", current_branch="feat")


def _tally(pre: str) -> rebase.types.ResolutionTally:
    return rebase.types.ResolutionTally(pre_rebase_head=pre)


def _report(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_handwritten_conflict_aborts_clean(tmp_path, capsys):
    """a.go is AI_MERGE, go.sum is REGENERATE — default mode aborts both."""
    repo, pre = _conflicted(tmp_path, {
        "a.go": ("package base\n", "package main\n", "package feat\n"),
        "go.sum": ("sum-base\n", "sum-main\n", "sum-feat\n"),
    })
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
        rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
        target_ref="main",
    )
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["aborted"] is True
    assert report["tree"] == "clean"
    assert report["classes"] == {"a.go": "ai_merge", "go.sum": "regenerate"}
    assert not rebase.inspect.rebase_in_progress(str(repo))
    assert git_out(repo, "status", "--porcelain") == ""
    assert git_out(repo, "rev-parse", "HEAD").strip() == pre == report["pre_rebase_head"]


def test_generated_only_with_declared_regenerator_continues(tmp_path):
    repo, pre = _conflicted(tmp_path, {
        "gen.txt": (
            "DO NOT EDIT\nbase\n",
            "DO NOT EDIT\nmain\n",
            "DO NOT EDIT\nfeat\n",
        ),
    })
    (repo / "regen.py").write_text(
        "from pathlib import Path\n"
        "Path('gen.txt').write_text('DO NOT EDIT\\nregen\\n')\n"
    )
    _write(repo, ".workbench.yml", "rebase:\n  regenerate:\n    - python3 regen.py\n")
    rebase.repo_regen.clear_caches()
    tally = _tally(pre)
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.REBASE_ONLY,
        rebase.inspect.detect_conflicts(str(repo)), tally,
        target_ref="main",
    )
    assert rc is None
    assert tally.resolution == "generated"
    assert tally.stale == []
    assert not rebase.inspect.rebase_in_progress(str(repo))
    assert "regen" in (repo / "gen.txt").read_text()


def test_conventional_generate_task_does_not_qualify(tmp_path, capsys):
    repo, pre = _conflicted(tmp_path, {
        "gen.txt": (
            "DO NOT EDIT\nbase\n",
            "DO NOT EDIT\nmain\n",
            "DO NOT EDIT\nfeat\n",
        ),
    })
    with mock.patch.object(rebase.repo_regen, "mise_has_task", return_value=True):
        rc = rebase.lifecycle.step_conflicts(
            str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
            rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
            target_ref="main",
        )
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["aborted"] is True
    assert report["tree"] == "clean"


def test_generated_only_with_regenerate_attribute_continues(tmp_path):
    repo, pre = _conflicted(tmp_path, {
        "gen.txt": (
            "DO NOT EDIT\nbase\n",
            "DO NOT EDIT\nmain\n",
            "DO NOT EDIT\nfeat\n",
        ),
    })
    (repo / "regen.py").write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('gen.txt').write_text('DO NOT EDIT\\nregen\\n')\n"
    )
    (repo / "regen.py").chmod(0o755)
    _write(
        repo, ".gitattributes",
        "gen.txt linguist-generated=true regenerate=./regen.py\n",
    )
    rebase.repo_regen.clear_caches()
    tally = _tally(pre)
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.REBASE_ONLY,
        rebase.inspect.detect_conflicts(str(repo)), tally,
        target_ref="main",
    )
    assert rc is None
    assert tally.resolution == "generated"
    assert tally.stale == []
    assert not rebase.inspect.rebase_in_progress(str(repo))
    assert "regen" in (repo / "gen.txt").read_text()


def test_unattributed_generated_file_aborts_without_declared_fallback(
    tmp_path, capsys,
):
    """A generated file with no regenerate= attribute stays stale.

    Revert-check: falling through to a declared/conventional command when the
    repo has neither would queue a rebuild this file does not have, and this
    case would continue instead of aborting.
    """
    repo, pre = _conflicted(tmp_path, {
        "CHANGELOG.md": (
            "DO NOT EDIT\nbase\n",
            "DO NOT EDIT\nmain\n",
            "DO NOT EDIT\nfeat\n",
        ),
    })
    _write(
        repo, ".gitattributes",
        "gen.txt linguist-generated=true regenerate=./regen.py\n",
    )
    rebase.repo_regen.clear_caches()
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
        rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
        target_ref="main",
    )
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["aborted"] is True
    assert "CHANGELOG.md" in report["files_stale"]


def test_failed_rebuild_aborts(tmp_path, capsys):
    repo, pre = _conflicted(tmp_path, {
        "gen.txt": (
            "DO NOT EDIT\nbase\n",
            "DO NOT EDIT\nmain\n",
            "DO NOT EDIT\nfeat\n",
        ),
    })
    _write(repo, ".workbench.yml", "rebase:\n  regenerate:\n    - false\n")
    rebase.repo_regen.clear_caches()
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
        rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
        target_ref="main",
    )
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["aborted"] is True
    assert "gen.txt" in report["files_stale"]


def test_default_mode_never_calls_backend(tmp_path, capsys):
    repo, pre = _conflicted(tmp_path, {
        "a.go": ("package base\n", "package main\n", "package feat\n"),
    })

    def boom(*_a, **_k):
        raise AssertionError("default mode must not call the AI backend")

    with mock.patch.object(agent.backend, "is_available", side_effect=boom), \
         mock.patch.object(rebase.resolve_ai, "resolve_single_file", side_effect=boom):
        rc = rebase.lifecycle.step_conflicts(
            str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
            rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
            target_ref="main",
        )
    assert rc == rebase.types.CONFLICTS_EXIT
    assert _report(capsys)["aborted"] is True


def test_stop_on_conflict_keeps_paused_tree(tmp_path, capsys):
    repo, pre = _conflicted(tmp_path, {
        "a.go": ("package base\n", "package main\n", "package feat\n"),
    }, dirty=True)
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
        rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
        target_ref="main", pause_on_conflict=True,
    )
    rebase.stash.restore(str(repo), rebase.types.RunMode.PUSH)
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["tree"] == "paused"
    assert report["aborted"] is False
    assert rebase.inspect.rebase_in_progress(str(repo))
    assert rebase.stash.auto_stash_ref(str(repo))


def test_resumed_rebase_never_aborts(tmp_path, capsys):
    repo, pre = _conflicted(tmp_path, {
        "a.go": ("package base\n", "package main\n", "package feat\n"),
    })
    rc = rebase.lifecycle.step_conflicts(
        str(repo), _ctx(repo), rebase.types.RunMode.PUSH,
        rebase.inspect.detect_conflicts(str(repo)), _tally(pre),
        target_ref="main", pause_on_conflict=True,
    )
    report = _report(capsys)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["tree"] == "paused"
    assert report["aborted"] is False
    assert rebase.inspect.rebase_in_progress(str(repo))
