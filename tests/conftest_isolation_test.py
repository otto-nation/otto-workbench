"""The sandboxes prove themselves: no test writes to the real state root, and
no git command a test runs fires the machine's hooks. The shared subprocess
runner every fixture goes through is pinned here too.

Contention is the other half. `run_checked` covers the git a *fixture* runs and
raises where nobody can miss it; the hooks below cover the git the *code under
test* runs, which `proc.run` hands back as an ordinary failure result and which
therefore reaches the reader as whatever assertion trips next. Those tests drive
the hook functions directly, and one of them checks that pytest has actually
registered them — a misnamed hook is a function the suite still tests and the
runner never calls."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from core import proc
from core import workbench_paths

import conftest
from conftest import (
    MachineContention, git_in, git_out, init_worktree,
    pytest_runtest_makereport, pytest_runtest_setup, run_checked,
)


def test_state_root_is_sandboxed_per_test(tmp_path):
    """Every test gets its own state root, so nothing lands in ~/.local/state."""
    assert os.environ["WORKBENCH_STATE_DIR"] == str(tmp_path / "state")


def test_state_dir_resolves_through_the_sandbox(tmp_path):
    """The env var is the whole mechanism — it reaches subprocesses too."""
    assert workbench_paths.state_dir() == tmp_path / "state"


# ── git hooks ───────────────────────────────────────────────────────────────


def _reject_commits_from(hooks: Path) -> Path:
    """A hooks directory whose `pre-commit` refuses every commit."""
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/usr/bin/env bash\necho rejected-by-hook >&2\nexit 1\n")
    hook.chmod(0o755)
    return hooks


def _commit_in(repo: Path) -> subprocess.CompletedProcess:
    """Commit a file, reporting the outcome rather than raising on rejection.

    The commit is spelled out rather than run through `git_in` because `git_in`
    raises on a non-zero exit, and a rejected commit is the result half these
    tests are asserting on. The staging step above has no such reason.
    """
    (repo / "f.txt").write_text("one")
    git_in(repo, "add", "--", "f.txt")
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "one"],
        capture_output=True, text=True)


def test_a_test_repo_runs_no_hook(tmp_path):
    """A hook planted where git looks by default does not fire."""
    repo = init_worktree(tmp_path / "repo")
    _reject_commits_from(repo / ".git" / "hooks")

    assert _commit_in(repo).returncode == 0


def test_the_sandbox_is_what_stops_the_hook(tmp_path, live_git_hooks):
    """The same repo with the sandbox lifted, so the test above is known to be
    reporting the fixture rather than passing for some other reason.

    Worth pinning because on a machine that has a global `core.hooksPath` the
    planted hook is bypassed either way, and the test above would keep passing
    long after the fixture stopped working.
    """
    repo = init_worktree(tmp_path / "repo")
    _reject_commits_from(repo / ".git" / "hooks")

    assert _commit_in(repo).returncode != 0


def test_a_container_worktree_runs_no_hook(container):
    """The bare clone is a repo of its own that `init_worktree` never sees, and
    its worktrees read its config — so it is the case a per-repo write missed."""
    _reject_commits_from(container / ".git" / "hooks")

    assert _commit_in(container / "main").returncode == 0


# ── the shared subprocess runner ────────────────────────────────────────────
#
# A command that exits non-zero and a command that never finished are different
# diagnoses, and telling them apart is the whole point of the runner. Under an
# oversubscribed machine the second kind arrives in arbitrary tests that never
# repeat, which reads as a real defect until somebody proves otherwise.


def test_git_out_returns_what_the_command_printed(tmp_path):
    repo = init_worktree(tmp_path / "repo")

    assert git_out(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"


def test_a_git_error_keeps_the_message_git_wrote(tmp_path):
    """`check=True` renders as an exit code alone, which loses the diagnosis."""
    repo = init_worktree(tmp_path / "repo")

    with pytest.raises(AssertionError) as excinfo:
        git_out(repo, "rev-parse", "--verify", "refs/heads/nope")

    assert not isinstance(excinfo.value, MachineContention)
    assert "exit 128" in str(excinfo.value)
    assert "Needed a single revision" in str(excinfo.value)


def test_a_signal_death_is_named_as_contention(tmp_path):
    """The SIGPIPE that started #970: a `git add -A` that reported -13 with
    empty output, which a plain runner shows as `failed (-13)` and nothing else."""
    with pytest.raises(MachineContention) as excinfo:
        run_checked(["bash", "-c", "kill -PIPE $$"])

    message = str(excinfo.value)
    assert "SIGPIPE (signal 13)" in message
    assert "machine problem, not a test defect" in message


def test_a_timeout_is_named_as_contention():
    with pytest.raises(MachineContention) as excinfo:
        run_checked(["sleep", "30"], timeout=0.1)

    message = str(excinfo.value)
    assert "timed out after 0.1s" in message
    assert "machine problem, not a test defect" in message


def test_contention_reads_as_a_failure_rather_than_an_error():
    """An AssertionError subclass, so pytest reports it beside the other
    failures instead of as a collection-time explosion nobody attributes."""
    assert issubclass(MachineContention, AssertionError)


def _reported(*, failed: bool):
    """Drive conftest's makereport wrapper over a report and return the report.

    A hookwrapper is a generator: pluggy runs it to the yield, hands back the
    outcome and lets it finish. Standing in for pluggy is what lets these tests
    say what the hook does to a failing report without arranging a failing test
    to carry it.
    """
    report = SimpleNamespace(failed=failed, sections=[])
    wrapper = pytest_runtest_makereport(item=None, call=None)
    next(wrapper)
    with pytest.raises(StopIteration):
        wrapper.send(SimpleNamespace(get_result=lambda: report))
    return report


def _starve():
    """A production `proc.run` the machine ends, as the code under test would."""
    proc.run(["sleep", "5"], timeout=0.1)


def test_a_starved_production_command_is_named_on_the_failure():
    """The gap #1124 opened on: `land` ran its own `git commit`, the commit lost
    its slot, and the mock assertion that tripped afterwards read as real."""
    pytest_runtest_setup(item=None)
    _starve()

    (name, content), = _reported(failed=True).sections

    assert MachineContention.__name__ in name
    assert "sleep 5 — timed out after 0.1s" in content
    assert "machine problem, not a test defect" in content


def test_a_passing_test_is_not_annotated():
    """A production command can be starved in a test that still passes, and
    saying so there is noise attached to nothing."""
    pytest_runtest_setup(item=None)
    _starve()

    assert _reported(failed=False).sections == []


def test_a_failure_with_nothing_killed_is_not_annotated():
    """Otherwise every real failure carries the note and it stops meaning anything."""
    pytest_runtest_setup(item=None)

    assert _reported(failed=True).sections == []


def test_each_test_starts_with_an_empty_record():
    """A kill left over from the previous test points the reader at contention
    that had nothing to do with the failure in front of them."""
    _starve()

    pytest_runtest_setup(item=None)

    assert _reported(failed=True).sections == []


def test_the_hooks_are_registered_with_pytest(pytestconfig):
    """A hook spelled wrong is a function these tests exercise and pytest never
    calls, which is the one failure the tests above cannot see."""
    hooks = pytestconfig.pluginmanager.hook
    for hook in (hooks.pytest_runtest_setup, hooks.pytest_runtest_makereport):
        assert any(impl.plugin is conftest for impl in hook.get_hookimpls())
