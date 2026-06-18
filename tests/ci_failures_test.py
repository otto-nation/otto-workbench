"""Tests for ci_failures library."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ci_failures import FailureKind, Outcome


def test_failure_kind_members():
    assert FailureKind.LINT.value == "lint"
    assert FailureKind.TEST.value == "test"
    assert FailureKind.BUILD.value == "build"
    assert FailureKind.INFRA.value == "infra"
    assert FailureKind.FLAKY.value == "flaky"


def test_outcome_members():
    assert Outcome.NEW.value == "new"
    assert Outcome.PERSISTING.value == "persisting"
    assert Outcome.REGRESSED.value == "regressed"
    assert Outcome.RESOLVED.value == "resolved"
    assert Outcome.FIXED.value == "fixed"


def test_failure_item_fields():
    from ci_failures import FailureItem
    item = FailureItem(
        id="sc2086-bin-foo-42",
        annotation="SC2086: Double quote to prevent globbing",
        file="bin/foo.sh",
        line=42,
        diagnosis=None,
        fix_sha=None,
        outcome=None,
    )
    assert item.id == "sc2086-bin-foo-42"
    assert item.file == "bin/foo.sh"
    assert item.line == 42
    assert item.diagnosis is None


def test_failure_item_is_frozen():
    from ci_failures import FailureItem
    item = FailureItem(id="x", annotation="y", file=None, line=None,
                       diagnosis=None, fix_sha=None, outcome=None)
    try:
        item.id = "z"
        assert False, "Should have raised"
    except AttributeError:
        pass


def test_failure_group_fields():
    from ci_failures import FailureItem, FailureGroup
    item = FailureItem(id="x", annotation="y", file="a.sh", line=1,
                       diagnosis=None, fix_sha=None, outcome=None)
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    assert group.job == "lint / shellcheck"
    assert group.kind == FailureKind.LINT
    assert len(group.items) == 1


def test_run_state_fields():
    from ci_failures import RunState
    run = RunState(
        run_id=123, run_number=7, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00", failures={},
    )
    assert run.run_id == 123
    assert run.conclusion == "failure"


def test_ci_state_fields():
    from ci_failures import CIState
    state = CIState(
        repo="owner/repo", pr_number=42,
        branch="isaac/feat/foo", runs={}, latest_run_id=None,
    )
    assert state.repo == "owner/repo"
    assert state.pr_number == 42
    assert state.latest_run_id is None
