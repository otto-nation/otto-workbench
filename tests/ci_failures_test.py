"""Tests for ci_failures library."""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ci_failures import (
    FailureKind, Outcome, classify_job, FailureItem, FailureGroup, RunState,
    empty_state, load_state, save_state, state_to_dict, state_from_dict,
    compute_progression,
)


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


def test_classify_job_shellcheck():
    assert classify_job("lint / shellcheck", []) == FailureKind.LINT


def test_classify_job_pytest():
    assert classify_job("test / pytest", []) == FailureKind.TEST


def test_classify_job_bats():
    assert classify_job("test / bats", []) == FailureKind.TEST


def test_classify_job_docker_build():
    assert classify_job("build / docker", []) == FailureKind.BUILD


def test_classify_job_unknown_defaults_to_build():
    assert classify_job("deploy / staging", []) == FailureKind.BUILD


def test_classify_job_infra_override_from_annotations():
    annotations = ["Error: connection refused to registry.npmjs.org"]
    assert classify_job("test / pytest", annotations) == FailureKind.INFRA


def test_classify_job_timeout_is_infra():
    annotations = ["The job running on runner timed out"]
    assert classify_job("lint / shellcheck", annotations) == FailureKind.INFRA


def test_classify_job_case_insensitive():
    assert classify_job("ShellCheck", []) == FailureKind.LINT
    assert classify_job("PYTEST", []) == FailureKind.TEST


def test_classify_job_no_infra_override_without_signature():
    annotations = ["SC2086: Double quote to prevent globbing"]
    assert classify_job("lint / shellcheck", annotations) == FailureKind.LINT


def test_empty_state_fields():
    state = empty_state("owner/repo", "isaac/feat/foo", pr_number=42)
    assert state.repo == "owner/repo"
    assert state.branch == "isaac/feat/foo"
    assert state.pr_number == 42
    assert state.runs == {}
    assert state.latest_run_id is None


def test_empty_state_no_pr():
    state = empty_state("owner/repo", "main")
    assert state.pr_number is None


def test_load_state_missing_file():
    result = load_state(Path("/nonexistent/worktree"))
    assert result is None


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = empty_state("owner/repo", "main", pr_number=1)
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.repo == "owner/repo"
        assert loaded.branch == "main"
        assert loaded.pr_number == 1
        assert loaded.runs == {}


def test_save_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "nested" / "worktree"
        state = empty_state("repo", "branch")
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None


def test_state_roundtrip_with_run():
    state = empty_state("owner/repo", "branch", pr_number=5)
    item = FailureItem(
        id="sc2086-bin-foo-42", annotation="SC2086: Double quote",
        file="bin/foo.sh", line=42, diagnosis="Unquoted var",
        fix_sha="abc123", outcome=Outcome.FIXED,
    )
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=999, run_number=7, head_sha="def456",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={"shellcheck": group},
    )
    state.runs[999] = run
    state.latest_run_id = 999

    as_dict = state_to_dict(state)
    restored = state_from_dict(as_dict)
    assert restored.latest_run_id == 999
    assert 999 in restored.runs
    restored_run = restored.runs[999]
    assert restored_run.head_sha == "def456"
    assert "shellcheck" in restored_run.failures
    restored_group = restored_run.failures["shellcheck"]
    assert restored_group.kind == FailureKind.LINT
    assert len(restored_group.items) == 1
    assert restored_group.items[0].outcome == Outcome.FIXED
    assert restored_group.items[0].fix_sha == "abc123"


# ── Progression Tests ──────────────────────────────────────────────────────

def _make_item(item_id: str, **kwargs) -> FailureItem:
    defaults = dict(
        id=item_id, annotation="err", file="a.sh", line=1,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    defaults.update(kwargs)
    return FailureItem(**defaults)


def _make_group(job: str, kind: FailureKind, item_ids: list[str]) -> FailureGroup:
    return FailureGroup(
        job=job, kind=kind,
        items=tuple(_make_item(i) for i in item_ids),
    )


def test_progression_all_new_when_no_prior():
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a", "b"])}
    result = compute_progression(current, {})
    assert result["a"] == Outcome.NEW
    assert result["b"] == Outcome.NEW


def test_progression_persisting():
    prior = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.PERSISTING


def test_progression_resolved():
    prior = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a", "b"])}
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.PERSISTING
    assert "b" not in result  # resolved items not in current


def test_progression_resolved_items():
    prior = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a", "b"])}
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.PERSISTING


def test_progression_regressed():
    prior_item = _make_item("a", fix_sha="abc123", outcome=Outcome.FIXED)
    prior = {"shellcheck": FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=(prior_item,))}
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.REGRESSED


def test_progression_mixed():
    prior = {"sc": _make_group("sc", FailureKind.LINT, ["a", "b"])}
    current = {"sc": _make_group("sc", FailureKind.LINT, ["a", "c"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.PERSISTING
    assert result["c"] == Outcome.NEW
