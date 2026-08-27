"""Tests for the Python half of the project registry (ai/lib/workbench_projects.py).

The shell half and the agreement between the two live in tests/projects.bats;
what is here is the behaviour only this side has — the callers that register are
a SessionStart hook and the `pr` CLI, so nothing may raise and nothing may fork.
"""

import shutil
import sys
from pathlib import Path

import pytest

from conftest import run_checked

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths  # noqa: E402
import workbench_projects  # noqa: E402

# Captured before the autouse fixture empties it, so the one test that needs the
# real rule can put it back.
DEFAULT_TEMP_ROOTS = workbench_projects.TEMP_ROOTS


@pytest.fixture(autouse=True)
def _allow_temp_paths(monkeypatch):
    """Let a test register the repos it builds, which are all under tmp_path.

    $TMPDIR goes with TEMP_ROOTS: pytest's tmp_path lives under it, so leaving
    it in place would refuse every repo a test can legally create.
    """
    monkeypatch.setattr(workbench_projects, "TEMP_ROOTS", ())
    monkeypatch.delenv("TMPDIR", raising=False)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_checked(["git", "init", "-q", str(path)])
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
        run_checked(["git", "init", "--bare", "-q", str(container / ".git")])
        assert not workbench_projects.register(container)

    def test_a_worktree_inside_a_container_is_registered(self, tmp_path):
        container = tmp_path / "container"
        run_checked(["git", "init", "--bare", "-q", str(container / ".git")])
        assert workbench_projects.register(make_repo(container / "main"))

    def test_a_relative_path_is_refused(self):
        assert not workbench_projects.register("relative/path")

    def test_a_path_holding_the_field_separator_is_refused(self, tmp_path):
        # The tab is what tells the path field from the repo identity, so a
        # path that carries one is indistinguishable from a line that already
        # has one — the membership check would compare against the truncated
        # path forever and every command run there would append another line.
        repo = make_repo(tmp_path / "al\tpha")
        assert not workbench_projects.register(repo)
        assert workbench_projects.registered() == []

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

    def test_the_private_twin_of_a_temp_root_is_excluded(self, monkeypatch):
        # /tmp and /var/folders are symlinks into /private on macOS, and callers
        # hand over a path git already resolved — so the resolved spelling is
        # the one that actually turns up.
        monkeypatch.setattr(workbench_projects, "TEMP_ROOTS", DEFAULT_TEMP_ROOTS)
        assert workbench_projects.excluded(Path("/private/tmp/some-repo"))
        assert workbench_projects.excluded(Path("/private/var/folders/xx/some-repo"))

    def test_the_state_root_is_excluded(self):
        assert workbench_projects.excluded(workbench_paths.state_dir() / "reviews" / "wt")

    def test_a_state_root_reached_through_a_symlink_is_excluded(self, tmp_path, monkeypatch):
        # The roots come from env vars, which a caller may well have written
        # with a symlink in them. The guard keeps throwaway review worktrees out
        # of a file the machine profile renders, so it must not fail open.
        real = tmp_path / "real-state"
        real.mkdir()
        link = tmp_path / "link-state"
        link.symlink_to(real)
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(link))
        assert workbench_projects.excluded(real / "reviews" / "wt")

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
        shutil.rmtree(alpha)
        assert workbench_projects.registered() == [beta]

    def test_a_repo_appended_twice_is_read_once(self, tmp_path):
        # Registration is an append guarded by a membership check rather than a
        # lock, so a session hook and a `pr` invocation starting together in one
        # repo can each append. Absorbed on read, not paid for on every write.
        repo = make_repo(tmp_path / "alpha")
        registry = workbench_projects.registry_path()
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(f"{repo}\n{repo}\n")
        assert workbench_projects.registered() == [repo]

    def test_the_backfill_marker_is_not_a_path(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        workbench_projects.register(repo)
        with open(workbench_projects.registry_path(), "a") as handle:
            handle.write("# backfilled from somewhere\n")
        assert workbench_projects.registered() == [repo]

    def test_a_line_carrying_a_repo_id_reads_as_its_path(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        registry = workbench_projects.registry_path()
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(f"{repo}\t{repo}/.git\n")
        assert workbench_projects.registered() == [repo]

    def test_register_is_a_no_op_for_a_path_that_carries_a_repo_id(self, tmp_path):
        repo = make_repo(tmp_path / "alpha")
        registry = workbench_projects.registry_path()
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(f"{repo}\t{repo}/.git\n")
        assert workbench_projects.register(repo)
        assert registry.read_text() == f"{repo}\t{repo}/.git\n"


class TestPathOwnership:
    def test_the_path_comes_from_workbench_paths(self):
        assert workbench_projects.registry_path() == workbench_paths.projects_registry()

    def test_the_registry_sits_in_the_state_root(self):
        assert workbench_paths.projects_registry().parent == workbench_paths.state_dir()
