"""Tests for ci_failures library."""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ci_failures import (
    FailureKind, Outcome, classify_job, FailureItem, FailureGroup, RunState,
    compute_progression, sync_ci_domain, render_dashboard,
    extract_failure_context, extract_headline, extract_tap_failures,
    LogMarker, LOG_MARKERS, SourceLocation, _MAX_CONTEXT_CHARS,
)
from pr_domains import CIDomain


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


def test_render_dashboard_in_progress_no_failures():
    """In-progress runs with no failures yet should not say 'All checks passed'."""
    run = RunState(
        run_id=123, run_number=7, head_sha="abc1234",
        status="in_progress", conclusion="",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={},
    )
    dashboard = render_dashboard(run, {})
    assert "still running" in dashboard.lower()
    assert "all checks passed" not in dashboard.lower()


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


def test_extract_failure_context_go_compiler():
    log = "\n".join([
        "go build ./...",
        "cmd/server/main.go:9:2: replacement directory ../lib-go does not exist",
        "cmd/server/main.go:10:2: replacement directory ../lib-go does not exist",
    ])
    result = extract_failure_context(log, FailureKind.BUILD)
    assert "replacement directory ../lib-go does not exist" in result


def test_extract_failure_context_gha_error():
    log = "\n".join([
        "Setting up job...",
        "##[error]Process completed with exit code 1.",
        "Cleaning up orphan processes",
    ])
    result = extract_failure_context(log, FailureKind.BUILD)
    assert "##[error]" in result


# ── TAP Extraction Tests ─────────────────────────────────────────────────

# Trimmed from run #2893, job 98945392702 — the shape that defeated the old
# extraction. The one real failure is near the top; the last lines before the
# runner's exit message are BW02 warning traces naming a test that passed, and
# `gha-error`'s two-line window landed on those.
_BATS_RUN_2893 = "\n".join([
    "2026-08-28T18:34:33.5540874Z ok 1647 output.sh version guard contains helpful message in 23ms",
    "2026-08-28T18:34:33.5541835Z not ok 1648 bats_skip survives a setup that sources lib/ui.sh in 71ms",
    "2026-08-28T18:34:33.5542700Z # (in test file tests/ui_facade.bats, line 164)",
    "2026-08-28T18:34:33.5543479Z #   `[[ \"$output\" == *\"# skip dependency missing\"* ]]' failed",
    "2026-08-28T18:34:33.5544239Z ok 1651 output.sh accepts current bash in 27ms",
    "2026-08-28T18:35:03.5717242Z ",
    "2026-08-28T18:35:03.5717379Z The following warnings were encountered during tests:",
    "2026-08-28T18:35:03.5717717Z # bats warning: Executed 1992 instead of expected 1994 tests",
    "2026-08-28T18:35:03.5726059Z BW02: Using flags on `run` requires at least BATS_VERSION=1.5.0.",
    "2026-08-28T18:35:03.5726707Z       (from function `bats_warn_minimum_guaranteed_version' in file /usr/lib/bats-core/warnings.bash, line 32,",
    "2026-08-28T18:35:03.5727234Z        from function `run' in file /usr/lib/bats-core/test_functions.bash, line 351,",
    "2026-08-28T18:35:03.5727631Z        in test file tests/install_targeted.bats, line 150)",
    "2026-08-28T18:35:03.7583872Z ##[error]Process completed with exit code 1.",
])


def _as_log_failed(log: str) -> str:
    """The same log as `gh run view --log-failed` renders it — job/step prefixed."""
    return "\n".join(f"Tests (bats)\tRun bats tests\t{line}" for line in log.splitlines())


def test_extract_tap_failures_anchors_on_the_failing_assertion():
    failures = extract_tap_failures(_BATS_RUN_2893)
    assert len(failures) == 1
    assert failures[0].location == SourceLocation("tests/ui_facade.bats", 164)
    assert failures[0].name == "bats_skip survives a setup that sources lib/ui.sh"


def test_extract_tap_failures_ignores_trailing_warning_traces():
    failures = extract_tap_failures(_BATS_RUN_2893)
    assert all("install_targeted" not in f.location.file for f in failures)


def test_extract_tap_failures_reads_log_failed_prefixed_lines():
    prefixed = extract_tap_failures(_as_log_failed(_BATS_RUN_2893))
    assert prefixed == extract_tap_failures(_BATS_RUN_2893)


def test_extract_tap_failures_prefers_the_test_file_over_the_helper():
    log = "\n".join([
        "not ok 4 failing later with helper in 1ms",
        "# (from function `helper_fn' in file tests/helper.bash, line 22,",
        "#  in test file tests/b.bats, line 18)",
        "#   `helper_fn' failed",
    ])
    assert extract_tap_failures(log)[0].location == SourceLocation("tests/b.bats", 18)


def test_extract_tap_failures_surfaces_every_failure():
    log = "\n".join([
        "1..4",
        "ok 1 passing first in 3ms",
        "not ok 2 failing early in 3ms",
        "# (in test file tests/a.bats, line 10)",
        "ok 3 passing later in 1ms",
        "not ok 4 failing late in 1ms",
        "# (in test file tests/b.bats, line 18)",
    ])
    failures = extract_tap_failures(log)
    assert [f.location for f in failures] == [
        SourceLocation("tests/a.bats", 10), SourceLocation("tests/b.bats", 18),
    ]


def test_extract_tap_failures_without_a_location():
    failures = extract_tap_failures("not ok 1 setup_file failed in 2ms")
    assert failures[0].location is None
    assert failures[0].name == "setup_file failed"


def test_extract_tap_failures_empty_for_a_non_tap_log():
    assert extract_tap_failures("--- FAIL: TestFoo (0.01s)\nFAIL") == ()


def test_extract_failure_context_bats_uses_the_tap_block():
    result = extract_failure_context(_BATS_RUN_2893, FailureKind.TEST)
    assert result.startswith("not ok 1648 ")
    assert "tests/ui_facade.bats, line 164" in result
    assert "##[error]" not in result
    assert "install_targeted" not in result


def test_extract_failure_context_strips_the_log_failed_job_step_prefix():
    prefixed = extract_failure_context(_as_log_failed(_BATS_RUN_2893), FailureKind.TEST)
    assert prefixed == extract_failure_context(_BATS_RUN_2893, FailureKind.TEST)


def test_prefix_stripping_leaves_go_fail_lines_alone():
    log = "FAIL\tgithub.com/x/y\t0.123s\n--- FAIL: TestThing (0.00s)"
    result = extract_failure_context(log, FailureKind.TEST)
    assert "FAIL\tgithub.com/x/y\t0.123s" in result


def test_extract_headline_tap_fail():
    context = "\n".join([
        "not ok 1648 bats_skip survives a setup that sources lib/ui.sh in 71ms",
        "# (in test file tests/ui_facade.bats, line 164)",
    ])
    assert extract_headline(context).startswith("not ok 1648 bats_skip survives")


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


# ── Headline Extraction Tests ────────────────────────────────────────────


def test_extract_headline_go_compiler():
    context = "\n".join([
        "go build ./...",
        "cmd/server/main.go:9:2: replacement directory ../lib-go does not exist",
        "FAIL",
    ])
    headline = extract_headline(context)
    assert headline == "cmd/server/main.go:9:2: replacement directory ../lib-go does not exist"


def test_extract_headline_gha_error():
    context = "##[error]Process completed with exit code 1."
    headline = extract_headline(context)
    assert headline == "Process completed with exit code 1."


def test_extract_headline_error_prefix():
    context = "\n".join([
        "running build...",
        "error: undefined symbol 'foo'",
    ])
    headline = extract_headline(context)
    assert headline == "error: undefined symbol 'foo'"


def test_extract_headline_fatal_prefix():
    context = "fatal: not a git repository"
    headline = extract_headline(context)
    assert headline == "fatal: not a git repository"


def test_extract_headline_go_test_fail():
    context = "\n".join([
        "=== RUN   TestFoo",
        "--- FAIL: TestFoo (0.01s)",
    ])
    headline = extract_headline(context)
    assert headline == "--- FAIL: TestFoo (0.01s)"


def test_extract_headline_go_pkg_fail():
    context = "FAIL\tsvc-foo/pkg\t0.3s"
    headline = extract_headline(context)
    assert headline == "FAIL\tsvc-foo/pkg\t0.3s"


def test_extract_headline_no_match():
    context = "\n".join([
        "Setting up job...",
        "Downloading dependencies...",
        "All good here",
    ])
    assert extract_headline(context) is None


def test_extract_headline_empty():
    assert extract_headline("") is None
    assert extract_headline(None) is None


def test_extract_headline_truncates():
    long_msg = "cmd/main.go:1:1: " + "x" * 300
    headline = extract_headline(long_msg)
    assert len(headline) == 200


def test_extract_headline_ts_error():
    context = "src/app.ts(42,5): error TS2304: Cannot find name 'foo'."
    headline = extract_headline(context)
    assert "error TS2304" in headline


def test_extract_headline_panic():
    context = "\n".join([
        "goroutine 1 [running]:",
        "panic: runtime error: index out of range",
    ])
    headline = extract_headline(context)
    assert headline == "panic: runtime error: index out of range"


# ── Dashboard with Headlines Tests ──────────────────────────────────────


def test_render_dashboard_with_headlines():
    item = _make_item("a", annotation="full context...",
                      headline="main.go:9:2: missing import")
    group = FailureGroup(job="Analyze (go)", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"analyze-go": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "Analyze (go):" in dashboard
    assert "main.go:9:2: missing import" in dashboard


def test_render_dashboard_deduplicates_headlines():
    items = [
        _make_item("a", headline="same error"),
        _make_item("b", headline="same error"),
        _make_item("c", headline="same error"),
    ]
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=tuple(items))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW, "b": Outcome.NEW, "c": Outcome.NEW})
    assert "same error (×3)" in dashboard
    assert dashboard.count("same error") == 1


def test_render_dashboard_truncates_at_five():
    items = [_make_item(f"item-{i}", headline=f"error {i}") for i in range(8)]
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=tuple(items))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"lint": group},
    )
    dashboard = render_dashboard(run, {f"item-{i}": Outcome.NEW for i in range(8)})
    assert "▸" in dashboard
    headline_lines = [l for l in dashboard.splitlines() if "▸" in l]
    assert len(headline_lines) == 5
    assert "… and 3 more" in dashboard


def test_render_dashboard_no_headline_falls_back_to_annotation():
    item = _make_item("a", annotation="SC2086: Double quote to prevent globbing")
    group = FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"shellcheck": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "SC2086" in dashboard


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


# ── Multi-run Dashboard Tests ────────────────────────────────────────────


def test_render_dashboard_shows_multiple_run_ids():
    item = _make_item("a")
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, run_ids=[100, 200])
    assert "100" in dashboard
    assert "200" in dashboard
    assert "Workflow runs:" in dashboard


def test_render_dashboard_omits_run_ids_for_single_run():
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={},
    )
    dashboard = render_dashboard(run, {}, run_ids=[100])
    assert "Workflow runs:" not in dashboard


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


def test_render_dashboard_shows_failed_step():
    item = _make_item("a", headline="Run 'mise run generate' locally and commit the changes.")
    group = FailureGroup(
        job="Generate & verify", kind=FailureKind.BUILD,
        items=(item,), failed_step="Generate & check drift",
    )
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"generate-verify": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "Generate & verify → Generate & check drift:" in dashboard


def test_render_dashboard_omits_arrow_without_failed_step():
    item = _make_item("a", headline="SC2086: Double quote")
    group = FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"shellcheck": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "shellcheck:" in dashboard
    assert "→" not in dashboard


def test_render_dashboard_show_status_in_progress():
    item = _make_item("a")
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="in_progress", conclusion="",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— in progress" in dashboard
    assert "Run #5" in dashboard


def test_render_dashboard_show_status_complete():
    item = _make_item("a")
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— complete" in dashboard


def test_render_dashboard_show_status_default_off():
    """Without show_status, header has no status suffix."""
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="in_progress", conclusion="",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={},
    )
    dashboard = render_dashboard(run, {})
    assert "— in progress" not in dashboard
    assert "— complete" not in dashboard


# ── Drift/Codegen Marker Tests ──────────────────────────────────────────


def test_extract_failure_context_codegen_drift():
    log = "\n".join([
        "Step 1: Generating protos...",
        "Step 2: Checking drift...",
        "The following files are out of date:",
        "  lib-proto/gen/ts/account/v1/account_pb.ts",
        "  lib-proto/gen/ts/auth/v1/auth_pb.ts",
        " 105 files changed, 105 insertions(+), 105 deletions(-)",
        "",
        "  Run 'mise run generate' locally and commit the changes.",
    ])
    result = extract_failure_context(log, FailureKind.BUILD)
    assert "locally and commit" in result
    assert "mise run generate" in result


def test_extract_failure_context_diff_stat():
    log = "\n".join([
        "Checking generated files...",
        " file1.go | 5 ++---",
        " file2.go | 3 ++-",
        " 2 files changed, 5 insertions(+), 3 deletions(-)",
        "Generated code is out of date.",
    ])
    result = extract_failure_context(log, FailureKind.BUILD)
    assert "2 files changed" in result


def test_extract_headline_codegen_drift():
    context = "\n".join([
        "  lib-proto/gen/ts/account/v1/account_pb.ts",
        " 105 files changed, 105 insertions(+), 105 deletions(-)",
        "",
        "  Run 'mise run generate' locally and commit the changes.",
    ])
    headline = extract_headline(context)
    assert "105 files changed" in headline


def test_extract_headline_codegen_action_line():
    context = "  Run 'mise run generate' locally and commit the changes."
    headline = extract_headline(context)
    assert "locally and commit" in headline


# ── ANSI Stripping Tests ─────────────────────────────────────────────────


def test_extract_failure_context_strips_ansi():
    log = "\x1b[31mFAIL\x1b[0m\tgithub.com/foo/bar\t0.3s\n"
    result = extract_failure_context(log, FailureKind.TEST)
    assert "FAIL" in result
    assert "\x1b[" not in result


def test_extract_headline_after_ansi_strip():
    # extract_headline matches through ANSI codes — callers should pre-clean via extract_failure_context
    context = "\x1b[31m--- FAIL:\x1b[0m TestFoo (0.01s)"
    headline = extract_headline(context)
    assert headline is not None
    assert "FAIL:" in headline


# ── Go Test Pattern Fix Tests ────────────────────────────────────────────


def test_extract_failure_context_go_fail_spaces():
    log = "\n".join([
        "ok  \tsvc-foo/pkg/a\t0.5s",
        "FAIL    svc-foo/pkg/b    0.3s",
        "ok  \tsvc-foo/pkg/c\t0.1s",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "FAIL" in result
    assert "svc-foo/pkg/b" in result


def test_extract_failure_context_go_testsum_fail():
    log = "\n".join([
        "=== RUN   TestFoo",
        "    foo_test.go:42: expected 1, got 2",
        "=== FAIL: TestFoo (0.01s)",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "=== FAIL" in result
    assert "expected 1, got 2" in result


def test_extract_failure_context_service_error():
    log = "\n".join([
        "[heartbeat] container startup (6/10m)...",
        "[authz-postgres] 2026-07-23 17:54:24.198 UTC [99] ERROR:  permission denied to reassign objects",
        "[vault] lease revocation failed",
        "FAIL\tgithub.com/foo/tests\t284.560s",
    ])
    result = extract_failure_context(log, FailureKind.TEST)
    assert "ERROR:" in result or "FAIL" in result
