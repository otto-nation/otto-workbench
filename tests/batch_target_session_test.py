"""`batch.target_session`: a target session's identity, and notes that only append.

Revert: make session_id return SESSION_ID_PREFIX + target_dir.name; the
validity and length tests fail. Make it use hash(); the cross-process test fails.
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx  # noqa: E402
import batch.target_session  # noqa: E402
import core.run_events  # noqa: E402
import pr.state  # noqa: E402
import pr.target  # noqa: E402
from batch.model import Verdict  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr_target_support import state_with  # noqa: E402

# pi 1.5.2 assertValidSessionId.
PI_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


@pytest.mark.parametrize("branch", ["", "feat/x", "x" * 400, "ends_", "ends-", "Ünï/{}@~"])
def test_the_session_id_is_a_valid_fixed_length_pi_id(branch):
    sid = batch.target_session.session_id(pr.target.target_dir("o-r-0123abcd", branch))
    assert PI_ID.fullmatch(sid), sid
    assert len(sid) == len("target-") + 16


def test_the_id_hashes_the_target_directory_name_only():
    name = "o-r-0123abcd-feat-x"
    expected = "target-" + hashlib.sha256(name.encode()).hexdigest()[:16]
    assert batch.target_session.session_id(Path("/a") / name) == expected
    assert batch.target_session.session_id(Path("/b") / name) == expected


def test_the_id_is_the_same_in_another_process():
    target = pr.target.target_dir("o-r-0123abcd", "feat/x")
    probe = ("import sys, pathlib; sys.path.insert(0, sys.argv[1]); import batch.target_session as t;"
             " print(t.session_id(pathlib.Path(sys.argv[2])))")
    out = subprocess.run([sys.executable, "-c", probe, str(LIB_DIR), str(target)],
                         capture_output=True, text=True, check=True, timeout=60,
                         env={**os.environ, "PYTHONHASHSEED": "12345"})
    assert out.stdout.strip() == batch.target_session.session_id(target)


def test_the_recipe_carries_the_target_dir_and_the_expected_id(tmp_path):
    target = tmp_path / "o-r-0123abcd-feat-x"
    ctx = make_ctx(repo="o/r", branch="feat/x", worktree_root=tmp_path / "wt", target_dir=target)
    sid = batch.target_session.session_id(target)
    assert batch.target_session.recipe(ctx).to_dict() == {
        "schema_version": 1, "session_id": sid, "name": "target o/r feat/x",
        "cwd": str(tmp_path / "wt"),
        "argv": ["pi", "--session-id", sid, "--name", "target o/r feat/x"],
        "env": {"WORKBENCH_TARGET_DIR": str(target), "WORKBENCH_SESSION_ID": sid},
    }


def test_no_worktree_has_no_session(tmp_path):
    with pytest.raises(batch.target_session.TargetSessionError, match="no worktree holds feat/x"):
        batch.target_session.recipe(make_ctx(branch="feat/x", worktree_root=None,
                                             target_dir=tmp_path / "t"))


def _saved(tmp_path):
    target = tmp_path / "t"
    state = state_with(items=[ItemOutcome(id="PRRT_1", outcome=FixOutcome.NEEDS_HUMAN)])
    pr.state.save_state(target, state)
    return state, target


def test_a_note_appends_one_decision_and_writes_nothing_else(tmp_path):
    state, target = _saved(tmp_path)
    before = sorted((p.name, p.read_bytes()) for p in target.iterdir())
    rec = batch.target_session.note(state, target, "7", "ci_failing:ci", Verdict.DEFERRED,
                                    "  flaky runner ", ["pr ci --fix"], run="inv-1")
    assert {k: rec[k] for k in ("kind", "run", "item", "finding", "verdict", "reason", "commands")} == {
        "kind": "decision", "run": "inv-1", "item": "o/r#7", "finding": "ci_failing:ci",
        "verdict": "deferred", "reason": "flaky runner", "commands": ["pr ci --fix"]}
    assert core.run_events.read(target).events == [rec]
    after = sorted((p.name, p.read_bytes()) for p in target.iterdir()
                   if p.name != core.run_events.EVENTS_FILE)
    assert after == before


@pytest.mark.parametrize("given, finding, reason, named", [
    ("o/r#9", "ci_failing:ci", "x", "o/r#9"),
    ("o/r#7", "review_findings:review", "x", "review_findings:review"),
    ("o/r#7", "ci_failing:ci", "   ", "reason"),
])
def test_a_bad_value_is_named_and_appends_nothing(tmp_path, given, finding, reason, named):
    state, target = _saved(tmp_path)
    with pytest.raises(batch.target_session.TargetSessionError, match=re.escape(named)):
        batch.target_session.note(state, target, given, finding, Verdict.FIXED, reason, run="i")
    assert core.run_events.read(target).events == []


def test_a_target_with_no_pr_recorded_refuses(tmp_path):
    with pytest.raises(batch.target_session.TargetSessionError, match="no PR is recorded"):
        batch.target_session.note(None, tmp_path, "7", "ci_failing:ci", Verdict.FIXED, "x", run="i")
