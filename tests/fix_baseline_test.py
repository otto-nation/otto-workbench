"""A red verify run, asked again at the tree the pass started from.

A pass that inherits a red tree used to have every fix it claimed demoted: the
failure from a hand commit before the pass read as the pass's own. The engine
now re-runs the same selection at a snapshot of the pre-pass tree and labels a
failure both runs share as inherited.
"""

import os
import re
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import core.publishing  # noqa: E402
import core.trail  # noqa: E402
import fix.engine  # noqa: E402
import fix.suite  # noqa: E402
from config.workbench_config import FixConfig, WorkbenchConfig  # noqa: E402
from conftest import commit_all, git_out, init_repo  # noqa: E402

from fix_engine_support import StubAdapter, _answer, _reads, landed  # noqa: F401
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401

OLD = "FAILED tests/x_test.py::test_old - AssertionError"
NEW = "FAILED tests/x_test.py::test_new - AssertionError"


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "checks"
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return str(path)


def _adapter(wt: Path, command: str) -> StubAdapter:
    adapter = StubAdapter(wt)
    adapter.config = WorkbenchConfig(fix=FixConfig(verify_command=command, verify_timeout=60))
    return adapter


def _repo(tmp_path: Path) -> Path:
    wt = init_repo(tmp_path / "wt")
    (wt / "a.py").write_text("ok = 1\n")
    commit_all(wt, "base")
    return wt


def _editing(adapter, text):
    answer = _answer(adapter)

    def run_fix(*args, **kwargs):
        (adapter.workdir / "a.py").write_text(text)
        return answer(*args, **kwargs)
    return run_fix


def test_failing_tests_reads_pytest_and_bats_lines():
    out = f"{OLD}\nERROR tests/y_test.py::test_e\nnot ok 3 a bats case # in 1s\nok 4 fine\n"
    assert fix.suite.failing_tests(out) == (
        "a bats case", "tests/x_test.py::test_old", "tests/y_test.py::test_e")


def test_a_failure_the_pre_pass_tree_shares_does_not_withdraw_the_claim(
        tmp_path, landed, snapshots):
    wt = _repo(tmp_path)
    adapter = _adapter(wt, _script(tmp_path, f'echo "{OLD}"; exit 1'))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "ok = 2\n")):
        run = fix.engine.run(adapter)
    assert run.suite.status is fix.suite.SuiteStatus.PREEXISTING
    assert run.suite.inherited == ("tests/x_test.py::test_old",)
    assert run.outcomes[0].verified is None


def test_a_new_failure_beside_an_old_one_still_withdraws(tmp_path, landed, snapshots):
    wt = _repo(tmp_path)
    body = f'grep -q BROKEN a.py && echo "{NEW}"; echo "{OLD}"; exit 1'
    adapter = _adapter(wt, _script(tmp_path, body))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "BROKEN\n")):
        run = fix.engine.run(adapter)
    assert run.suite.status is fix.suite.SuiteStatus.RED
    assert run.suite.inherited == ("tests/x_test.py::test_old",)
    assert run.outcomes[0].verified is False
    assert "tests/x_test.py::test_old" in run.suite.note


def test_preexisting_still_holds_publishing(tmp_path, landed, snapshots, publishing_on):
    wt = _repo(tmp_path)
    adapter = _adapter(wt, _script(tmp_path, f'echo "{OLD}"; exit 1'))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "ok = 2\n")):
        run = fix.engine.run(adapter)
    assert run.suite.status is fix.suite.SuiteStatus.PREEXISTING
    assert "already red before this pass" in core.publishing.held()


def test_the_baseline_worktree_is_removed(tmp_path, landed, snapshots):
    wt = _repo(tmp_path)
    adapter = _adapter(wt, _script(tmp_path, f'echo "{OLD}"; exit 1'))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "ok = 2\n")):
        fix.engine.run(adapter)
    assert len(git_out(wt, "worktree", "list").splitlines()) == 1


def test_a_stale_baseline_registration_is_pruned(tmp_path, landed, snapshots):
    """A killed run leaves a registered worktree whose directory is gone."""
    wt = _repo(tmp_path)
    stale = tmp_path / "stale-tree"
    git_out(wt, "worktree", "add", "--detach", str(stale), "HEAD")
    shutil.rmtree(stale)
    adapter = _adapter(wt, _script(tmp_path, f'echo "{OLD}"; exit 1'))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "ok = 2\n")):
        fix.engine.run(adapter)
    assert len(git_out(wt, "worktree", "list").splitlines()) == 1


def test_a_baseline_that_cannot_select_leaves_the_run_red(tmp_path, landed, snapshots):
    """Exit 75 under the baseline flag is "no baseline", not an all-clear."""
    wt = _repo(tmp_path)
    body = ('[ -n "${WORKBENCH_FIX_BASELINE:-}" ] && exit 75; '
            f'echo "{OLD}"; exit 1')
    adapter = _adapter(wt, _script(tmp_path, body))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _editing(adapter, "ok = 2\n")):
        run = fix.engine.run(adapter)
    assert run.suite.status is fix.suite.SuiteStatus.RED
    assert run.suite.inherited == ()
    assert run.outcomes[0].verified is False


def test_a_baseline_run_that_declines_to_select_is_an_error(tmp_path):
    script = _script(tmp_path, f"exit {fix.suite.NO_SELECTION_EXIT}")
    assert fix.suite.run(tmp_path, script, 30, baseline=True).status is fix.suite.SuiteStatus.ERROR
    assert fix.suite.run(tmp_path, script, 30).status is fix.suite.SuiteStatus.RED


def test_only_the_baseline_run_is_told_it_is_one(tmp_path, monkeypatch):
    # A shell that exported the flag must not decide the outcome either way.
    monkeypatch.delenv(fix.suite.FIX_BASELINE_ENV, raising=False)
    out = tmp_path / "flag.txt"
    script = _script(tmp_path, f'printf "%s" "${{WORKBENCH_FIX_BASELINE:-}}" >> {out}')
    fix.suite.run(tmp_path, script, 30)
    fix.suite.run(tmp_path, script, 30, baseline=True)
    assert out.read_text() == "1"


def test_an_inherited_baseline_or_paths_flag_is_not_passed_on(tmp_path, monkeypatch):
    """A verify run nested under a baseline re-run is not itself one, and selects its own paths."""
    monkeypatch.setenv(fix.suite.FIX_BASELINE_ENV, "1")
    monkeypatch.setenv(fix.suite.FIX_PATHS_ENV, "/tmp/stale")
    out = tmp_path / "env.txt"
    script = _script(tmp_path, f'printf "%s|%s" "${{WORKBENCH_FIX_BASELINE:-}}" '
                               f'"${{WORKBENCH_FIX_PATHS_FILE:-}}" > {out}')
    fix.suite.run(tmp_path, script, 30)
    assert out.read_text() == "|"


def test_no_selection_exit_matches_run_tests():
    """One value in two languages: `bin/local/run-tests` exits it, `fix.suite` reads it."""
    text = (REPO_ROOT / "bin" / "local" / "run-tests").read_text()
    declared = re.search(r"^NO_SELECTION_EXIT=(\d+)$", text, re.M)
    assert declared and int(declared.group(1)) == fix.suite.NO_SELECTION_EXIT
    assert fix.suite.FIX_BASELINE_ENV in text


def test_the_preexisting_note_says_later_suites_may_not_have_run():
    result = fix.suite.SuiteResult(status=fix.suite.SuiteStatus.PREEXISTING, command="c",
                                   inherited=("t::a",))
    assert "first failing suite" in result.note


def test_the_verify_run_does_not_inherit_the_trail(tmp_path, monkeypatch):
    out = tmp_path / "env.txt"
    script = _script(tmp_path, f'printf "%s|%s|%s" "${{WORKBENCH_TRAIL_ROOT:-}}" '
                               f'"${{WORKBENCH_DEBUG:-}}" "${{WORKBENCH_FIX_PATHS_FILE:-}}" > {out}')
    monkeypatch.setenv(core.trail.TRAIL_ROOT_ENV, "0123456789ab")
    monkeypatch.setenv("WORKBENCH_DEBUG", "1")
    fix.suite.run(tmp_path, script, 30, paths_file="/tmp/p")
    assert out.read_text() == "||/tmp/p"
    assert os.environ[core.trail.TRAIL_ROOT_ENV] == "0123456789ab"


def test_preexisting_qualifies_the_tally_and_does_not_demote():
    result = fix.suite.SuiteResult(status=fix.suite.SuiteStatus.PREEXISTING, command="c",
                                   inherited=("t::a",))
    assert not result.demotes and result.ran
    assert fix.suite.qualify_tally("1 fixed, 0 skipped", result) == (
        "1 fixed, 0 skipped (unverified: the repo's checks were already red before this pass)")
