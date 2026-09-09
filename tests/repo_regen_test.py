"""Tests for rebase.repo_regen — what a repo declares it rebuilds, and how."""

import contextlib
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from config import workbench_config
from git import client as git_client
from git import regenerate as regen
from rebase import repo_regen


@pytest.fixture(autouse=True)
def _forget_cached_repo_reads():
    """Each test drives its own tmp_path repo; a cached answer would outlive it."""
    repo_regen.clear_caches()
    yield
    repo_regen.clear_caches()


@contextlib.contextmanager
def _repo_declaring(commands, *, root, mise_task=False):
    """Stand in for the two sources ``repo_regenerators`` consults."""
    config = workbench_config.WorkbenchConfig(
        rebase=workbench_config.RebaseConfig(regenerate=list(commands)),
    )
    with mock.patch.object(workbench_config, "load_config_or_default",
                           return_value=config), \
         mock.patch.object(git_client, "out", return_value=str(root)), \
         mock.patch.object(repo_regen, "mise_has_task", return_value=mise_task):
        yield


def test_repo_regenerators_prefers_the_declared_commands(tmp_path):
    with _repo_declaring(["mise run generate", "mise run generate:i18n"],
                         root=tmp_path, mise_task=True):
        regens = repo_regen.repo_regenerators(str(tmp_path))

    assert [r.cmd for r in regens] == [
        ("mise", "run", "generate"),
        ("mise", "run", "generate:i18n"),
    ]


def test_repo_regenerators_falls_back_to_the_conventional_task(tmp_path):
    """No declaration, but the repo has a `generate` task — use it."""
    with _repo_declaring([], root=tmp_path, mise_task=True):
        regens = repo_regen.repo_regenerators(str(tmp_path))

    assert [r.cmd for r in regens] == [("mise", "run", "generate")]


def test_repo_regenerators_empty_when_nothing_declares_one(tmp_path):
    """No key and no conventional task means the rebuild is unknown.

    Guessing here would commit wrong generated output, so the caller reports
    the file stale instead.
    """
    with _repo_declaring([], root=tmp_path, mise_task=False):
        assert repo_regen.repo_regenerators(str(tmp_path)) == ()


def test_queue_repo_regeneration_collapses_files_into_one_run(tmp_path):
    """Four generated files must not become four regeneration runs."""
    queue = regen.RegenQueue()
    with _repo_declaring(["mise run generate"], root=tmp_path):
        for name in ("a_pb2.py", "b_pb.ts", "models.go", ".queries.hash"):
            assert repo_regen.queue_repo_regeneration(name, str(tmp_path), queue)

    jobs = list(queue)
    assert len(jobs) == 1
    assert jobs[0].cmd == ("mise", "run", "generate")
    assert len(jobs[0].files) == 4


def test_queue_repo_regeneration_false_when_unknown(tmp_path):
    queue = regen.RegenQueue()
    with _repo_declaring([], root=tmp_path, mise_task=False):
        assert not repo_regen.queue_repo_regeneration("x.gen", str(tmp_path), queue)

    assert list(queue) == []


class TestMiseHasTask:
    """The conventional-task fallback only fires when the repo really has one.

    Asked of `mise tasks ls` rather than parsed out of a config file, so these
    pin the parse of its output — a task whose name merely starts with the one
    we want must not answer yes.
    """

    @staticmethod
    @contextlib.contextmanager
    def _tasks_listing(stdout, *, installed=True, returncode=0):
        result = None if stdout is None else mock.Mock(
            returncode=returncode, stdout=stdout)
        with mock.patch("shutil.which",
                        return_value="/usr/bin/mise" if installed else None), \
             mock.patch.object(regen, "try_run", return_value=result):
            yield

    def test_a_named_task_is_found(self, tmp_path):
        with self._tasks_listing("generate  Rebuild everything\nlint  Check\n"):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is True

    def test_a_longer_name_is_not_a_match(self, tmp_path):
        """`generate:i18n` is a different task from `generate`."""
        with self._tasks_listing("generate:i18n  Rebuild translations\n"):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is False

    def test_no_such_task(self, tmp_path):
        with self._tasks_listing("lint  Check\n"):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is False

    def test_mise_not_installed(self, tmp_path):
        with self._tasks_listing("generate  Rebuild\n", installed=False):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is False

    def test_a_listing_that_could_not_run(self, tmp_path):
        with self._tasks_listing(None):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is False

    def test_a_listing_that_failed(self, tmp_path):
        with self._tasks_listing("", returncode=1):
            assert repo_regen.mise_has_task(str(tmp_path), "generate") is False
