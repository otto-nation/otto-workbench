"""Tests for ci_failures library: the failure model, job classification, progression
and syncing a run into the CI domain."""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr.ci_failures import (
    FailureKind,
    Outcome,
    classify_job,
    FailureItem,
    FailureGroup,
    RunState,
    compute_progression,
    sync_ci_domain,
    LogMarker,
    LOG_MARKERS,
)
from pr.domains import CIDomain


def test_failure_item_fields():
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
    assert item.headline is None


def test_failure_item_headline():
    item = FailureItem(
        id="x", annotation="full context", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
        headline="main.go:9:2: replacement directory ../lib-go does not exist",
    )
    assert item.headline == "main.go:9:2: replacement directory ../lib-go does not exist"


def test_failure_item_is_frozen():
    import pytest
    item = FailureItem(id="x", annotation="y", file=None, line=None,
                       diagnosis=None, fix_sha=None, outcome=None)
    with pytest.raises(AttributeError):
        item.id = "z"


def test_failure_group_fields():
    item = FailureItem(id="x", annotation="y", file="a.sh", line=1,
                       diagnosis=None, fix_sha=None, outcome=None)
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    assert group.job == "lint / shellcheck"
    assert group.kind == FailureKind.LINT
    assert len(group.items) == 1


def test_run_state_fields():
    run = RunState(
        run_id=123, run_number=7, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00", failures={},
    )
    assert run.run_id == 123
    assert run.conclusion == "failure"


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


def test_progression_prior_item_absent_from_result():
    prior = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a", "b"])}
    current = {"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])}
    result = compute_progression(current, prior)
    assert result["a"] == Outcome.PERSISTING
    assert "b" not in result  # resolved items not in current


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


# ── State Sync Tests (using CIDomain) ─────────────────────────────────────

def test_sync_ci_domain_adds_new_run():
    domain = CIDomain()
    run = RunState(
        run_id=100, run_number=1, head_sha="aaa",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00", failures={},
    )
    updated = sync_ci_domain(domain, run)
    assert 100 in updated.runs
    assert updated.latest_run_id == 100


def test_sync_ci_domain_prunes_the_oldest_runs_numerically():
    """Run ids that differ in digit count catch a lexical sort: "10" sorts
    before "9", so string keys would prune the newest runs instead."""
    domain = CIDomain()
    for run_id in range(2, 14):
        sync_ci_domain(domain, RunState(
            run_id=run_id, run_number=run_id, head_sha=f"sha{run_id}",
            status="completed", conclusion="failure",
            fetched_at="2026-06-18T00:00:00+00:00", failures={},
        ))
    assert sorted(domain.runs) == list(range(4, 14))


def test_sync_ci_domain_preserves_prior_diagnosis():
    diagnosed_item = _make_item("a", diagnosis="root cause found", fix_sha="abc")
    prior_group = FailureGroup(
        job="shellcheck", kind=FailureKind.LINT, items=(diagnosed_item,),
    )
    prior_run = RunState(
        run_id=100, run_number=1, head_sha="aaa",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T00:00:00+00:00",
        failures={"shellcheck": prior_group},
    )
    domain = CIDomain()
    domain.runs[100] = prior_run
    domain.latest_run_id = 100

    new_item = _make_item("a")
    new_group = FailureGroup(
        job="shellcheck", kind=FailureKind.LINT, items=(new_item,),
    )
    new_run = RunState(
        run_id=200, run_number=2, head_sha="bbb",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T15:00:00+00:00",
        failures={"shellcheck": new_group},
    )

    updated = sync_ci_domain(domain, new_run)
    assert updated.latest_run_id == 200
    synced_item = updated.runs[200].failures["shellcheck"].items[0]
    assert synced_item.diagnosis == "root cause found"
    assert synced_item.fix_sha == "abc"


def test_sync_ci_domain_summarizes_the_run_it_stored():
    domain = CIDomain()
    run = RunState(
        run_id=300, run_number=9, head_sha="ccc",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T16:00:00+00:00",
        failures={
            "shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a", "b"]),
            "pytest": _make_group("pytest", FailureKind.TEST, ["c"]),
        },
    )

    updated = sync_ci_domain(domain, run)
    assert updated.conclusion == "failure"
    assert updated.failure_count == 3
    assert updated.failure_kinds == {"lint": 2, "test": 1}
    assert updated.last_run_id == 300
    assert updated.last_run_number == 9
    assert updated.updated_at == "2026-06-18T16:00:00+00:00"


def test_sync_ci_domain_clears_the_summary_when_the_branch_goes_green():
    """A later green run must not leave the prior run's failure counts behind —
    a reader that never opens `runs` would keep reporting failures that are gone."""
    domain = CIDomain()
    sync_ci_domain(domain, RunState(
        run_id=300, run_number=9, head_sha="ccc",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T16:00:00+00:00",
        failures={"shellcheck": _make_group("shellcheck", FailureKind.LINT, ["a"])},
    ))

    updated = sync_ci_domain(domain, RunState(
        run_id=301, run_number=10, head_sha="ddd",
        status="completed", conclusion="success",
        fetched_at="2026-06-18T17:00:00+00:00", failures={},
    ))
    assert updated.conclusion == "success"
    assert updated.failure_count == 0
    assert updated.failure_kinds == {}
    assert updated.updated_at == "2026-06-18T17:00:00+00:00"


# ── LogMarker Tests ──────────────────────────────────────────────────────


def test_log_marker_fields():
    marker = LogMarker("test-marker", re.compile(r"error"), FailureKind.BUILD, before=3, after=15)
    assert marker.name == "test-marker"
    assert marker.kind == FailureKind.BUILD
    assert marker.before == 3
    assert marker.after == 15


def test_log_marker_defaults():
    marker = LogMarker("test-default", re.compile(r"x"), FailureKind.TEST)
    assert marker.before == 10
    assert marker.after == 30


def test_log_markers_registry_not_empty():
    assert len(LOG_MARKERS) > 0
    for m in LOG_MARKERS:
        assert m.name
        assert m.kind in FailureKind


# ── source_run_id Tests ──────────────────────────────────────────────────


def test_failure_item_source_run_id():
    item = FailureItem(
        id="x", annotation="y", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
        source_run_id=42,
    )
    assert item.source_run_id == 42


def test_failure_item_source_run_id_default():
    item = FailureItem(
        id="x", annotation="y", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    assert item.source_run_id is None


# ── context Tests ──────────────────────────────────────────────────────


def test_failure_item_context():
    item = FailureItem(
        id="x", annotation="Process completed with exit code 1", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
        context="Run 'mise run generate' locally and commit",
    )
    assert item.context == "Run 'mise run generate' locally and commit"


def test_failure_item_context_default():
    item = FailureItem(
        id="x", annotation="y", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    assert item.context is None


# ── failed_step Tests ───────────────────────────────────────────────────


def test_failure_group_failed_step():
    item = _make_item("a")
    group = FailureGroup(
        job="Generate & verify", kind=FailureKind.BUILD,
        items=(item,), failed_step="Generate & check drift",
    )
    assert group.failed_step == "Generate & check drift"


def test_failure_group_failed_step_default():
    item = _make_item("a")
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=(item,))
    assert group.failed_step is None
