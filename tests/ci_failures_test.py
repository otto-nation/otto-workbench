"""Tests for ci_failures library."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ci_failures import (
    FailureKind, Outcome, classify_job, FailureItem, FailureGroup, RunState,
    compute_progression, sync_ci_domain, render_dashboard, extract_failure_context,
    _MAX_CONTEXT_CHARS,
)
from pr_state import CIDomain


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
    assert "100" in updated.runs
    assert updated.latest_run_id == 100


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
    domain.runs["100"] = prior_run
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
    synced_item = updated.runs["200"].failures["shellcheck"].items[0]
    assert synced_item.diagnosis == "root cause found"
    assert synced_item.fix_sha == "abc"


# ── Dashboard Rendering Tests ─────────────────────────────────────────────

def test_render_dashboard_basic():
    item = _make_item("a", file="bin/foo.sh", line=42, annotation="SC2086: Double quote")
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=123, run_number=7, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={"shellcheck": group},
    )
    progression = {"a": Outcome.NEW}
    dashboard = render_dashboard(run, progression)
    assert "Run #7" in dashboard
    assert "abc1234" in dashboard
    assert "lint" in dashboard.lower()
    assert "1 new" in dashboard.lower()


def test_render_dashboard_all_pass():
    run = RunState(
        run_id=123, run_number=7, head_sha="abc1234",
        status="completed", conclusion="success",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={},
    )
    dashboard = render_dashboard(run, {})
    assert "pass" in dashboard.lower() or "success" in dashboard.lower()


def test_render_dashboard_mixed_progression():
    items = [
        _make_item("a", file="a.sh", line=1),
        _make_item("b", file="b.sh", line=2),
        _make_item("c", file="c.sh", line=3),
    ]
    group = FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=tuple(items))
    run = RunState(
        run_id=456, run_number=8, head_sha="def5678",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T15:00:00+00:00",
        failures={"shellcheck": group},
    )
    progression = {"a": Outcome.NEW, "b": Outcome.PERSISTING, "c": Outcome.REGRESSED}
    dashboard = render_dashboard(run, progression)
    assert "1 new" in dashboard.lower()
    assert "1 persisting" in dashboard.lower()
    assert "1 regressed" in dashboard.lower()


# ── Log Extraction Tests ─────────────────────────────────────────────────

def test_extract_failure_context_empty():
    assert extract_failure_context("", FailureKind.TEST) == ""


def test_extract_failure_context_go_test():
    log = "\n".join([
        "2026-06-22T17:22:01Z === RUN   TestFoo",
        "2026-06-22T17:22:01Z     foo_test.go:42: expected 1, got 2",
        "2026-06-22T17:22:01Z --- FAIL: TestFoo (0.01s)",
        "2026-06-22T17:22:01Z FAIL\tsvc-foo/pkg\t0.015s",
        "2026-06-22T17:22:01Z FAIL",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "--- FAIL: TestFoo" in result
    assert "expected 1, got 2" in result


def test_extract_failure_context_pytest():
    log = "\n".join([
        "collected 5 items",
        "tests/test_auth.py::test_login PASSED",
        "tests/test_auth.py::test_validate FAILED",
        "AssertionError: assert 'invalid' == 'valid'",
        "1 failed, 1 passed",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "FAILED" in result
    assert "AssertionError" in result


def test_extract_failure_context_build_error():
    log = "\n".join([
        "Step 1/5 : FROM golang:1.21",
        "Step 2/5 : COPY . .",
        "Step 3/5 : RUN go build ./...",
        "error: undefined: SomeFunction",
        "error: build failed",
    ])
    result = extract_failure_context(log, FailureKind.BUILD)
    assert "undefined: SomeFunction" in result


def test_extract_failure_context_infra_timeout():
    log = "\n".join([
        "Pulling docker image...",
        "Waiting for cache restore...",
        "The job running on runner timed out after 360 minutes",
        "Post job cleanup...",
    ])
    result = extract_failure_context(log, FailureKind.INFRA)
    assert "timed out" in result


def test_extract_failure_context_strips_timestamps():
    log = "2026-06-22T17:22:01.8039480Z --- FAIL: TestFoo (0.01s)\n"
    result = extract_failure_context(log, FailureKind.TEST)
    assert result.startswith("--- FAIL:")
    assert "2026-06-22T" not in result


def test_extract_failure_context_falls_back_to_tail():
    lines = [f"line {i}" for i in range(200)]
    log = "\n".join(lines)
    result = extract_failure_context(log, FailureKind.TEST)
    assert "line 199" in result
    assert "line 120" in result
    assert "line 0" not in result


def test_extract_failure_context_truncates_large_output():
    log = "x" * (_MAX_CONTEXT_CHARS + 1000)
    result = extract_failure_context(log, FailureKind.BUILD)
    assert len(result) <= _MAX_CONTEXT_CHARS


def test_extract_failure_context_go_fail_tab():
    log = "\n".join([
        "ok  \tsvc-foo/pkg/a\t0.5s",
        "FAIL\tsvc-foo/pkg/b\t0.3s",
        "ok  \tsvc-foo/pkg/c\t0.1s",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "FAIL\tsvc-foo/pkg/b" in result
