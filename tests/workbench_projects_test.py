"""Tests for the Python half of the project registry (ai/lib/workbench_projects.py).

The shell half and the agreement between the two live in tests/projects.bats;
what is here is the behaviour only this side has — the callers that register are
a SessionStart hook and the `pr` CLI, so nothing may raise and nothing may fork.
"""

import subprocess
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths  # noqa: E402
import workbench_projects  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_temp_paths(monkeypatch):
    """Let a test register the repos it builds, which are all under tmp_path."""
    monkeypatch.setattr(workbench_projects, "TEMP_ROOTS", ())


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


class TestRegistration:
    def test_a_registered_repo_comes_back(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        assert workbench_projects.register(repo)
        assert workbench_projects.registered() == [repo]

    def test_registering_twice_leaves_one_line(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        workbench_projects.register(repo)
        workbench_projects.register(repo)
        assert workbench_projects.registered() == [repo]

    def test_a_plain_directory_is_refused(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert not workbench_projects.register(plain)
        assert workbench_projects.registered() == []

    def test_a_bare_repos_container_is_refused(self, tmp_path):
        container = tmp_path / "container"
        container.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", str(container / ".git")],
                       check=True, capture_output=True)
        assert not workbench_projects.register(container)

    def test_a_worktree_inside_a_container_is_registered(self, tmp_path):
        container = tmp_path / "container"
        subprocess.run(["git", "init", "--bare", "-q", str(container / ".git")],
                       check=True, capture_output=True)
        assert workbench_projects.register(make_repo(container / "main"))

    def test_a_relative_path_is_refused(self):
        assert not workbench_projects.register("relative/path")

    def test_nothing_to_register_is_not_an_error(self):
        assert not workbench_projects.register(None)

    def test_an_unwritable_state_root_is_swallowed(self, tmp_path, monkeypatch):
        # The caller is a session hook. A registry that cannot be written must
        # cost the user a bookkeeping entry, not their session.
        repo = make_repo(tmp_path / "alpha")
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(blocked))
        assert not workbench_projects.register(repo)


class TestExclusion:
    def test_temp_paths_are_excluded_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workbench_projects, "TEMP_ROOTS",
                            ("/tmp", "/var/folders", "/private/var/folders"))
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        assert workbench_projects.excluded(tmp_path / "alpha")

    def test_the_state_root_is_excluded(self):
        assert workbench_projects.excluded(workbench_paths.state_dir() / "reviews" / "wt")

    def test_the_cache_root_is_excluded(self):
        assert workbench_projects.excluded(workbench_paths.cache_dir() / "wt")


class TestReads:
    def test_no_registry_reads_as_empty(self):
        assert workbench_projects.registered() == []

    def test_a_deleted_repo_is_skipped(self, tmp_path):
        alpha = make_repo(tmp_path / "alpha")
        beta = make_repo(tmp_path / "beta")
        workbench_projects.register(alpha)
        workbench_projects.register(beta)
        subprocess.run(["rm", "-rf", str(alpha)], check=True)
        assert workbench_projects.registered() == [beta]

    def test_the_backfill_marker_is_not_a_path(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        workbench_projects.register(repo)
        with open(workbench_projects.registry_path(), "a") as handle:
            handle.write("# backfilled from somewhere\n")
        assert workbench_projects.registered() == [repo]


class TestPathOwnership:
    def test_the_path_comes_from_workbench_paths(self):
        assert workbench_projects.registry_path() == workbench_paths.projects_registry()

    def test_the_registry_sits_in_the_state_root(self):
        assert workbench_paths.projects_registry().parent == workbench_paths.state_dir()
