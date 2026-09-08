"""What a CI run's report says — the keys on stdout and the dashboard on stderr."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr import ci_report  # noqa: E402
from pr.ci_failures import (  # noqa: E402
    FailureGroup, FailureItem, FailureKind, Outcome, RunState,
)
from pr.ci_report import CIReport, render_dashboard, serialize_failures  # noqa: E402
from pr.ci_runs import JobCounts  # noqa: E402


def _make_item(item_id: str, **kwargs) -> FailureItem:
    defaults = dict(
        id=item_id, annotation="err", file="a.sh", line=1,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    defaults.update(kwargs)
    return FailureItem(**defaults)


def _make_run(**kwargs) -> RunState:
    defaults = dict(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={},
    )
    defaults.update(kwargs)
    return RunState(**defaults)


def _one_failure_run(**kwargs) -> RunState:
    item = _make_item("a", headline="main.go:9:2: missing import")
    group = FailureGroup(job="Analyze (go)", kind=FailureKind.BUILD, items=(item,))
    return _make_run(failures={"analyze-go": group}, **kwargs)


# ── Failure serialization ──────────────────────────────────────────────────

def test_serialize_failures_repeats_the_group_fields_onto_each_item():
    items = (_make_item("a"), _make_item("b"))
    group = FailureGroup(
        job="Generate & verify", kind=FailureKind.BUILD,
        items=items, failed_step="Generate & check drift",
    )
    rows = serialize_failures({"generate": group})
    assert [r["id"] for r in rows] == ["a", "b"]
    assert all(r["job"] == "Generate & verify" for r in rows)
    assert all(r["kind"] == "build" for r in rows)
    assert all(r["failed_step"] == "Generate & check drift" for r in rows)


def test_serialize_failures_reports_an_untracked_item_as_new():
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=(_make_item("a"),))
    rows = serialize_failures({"lint": group}, {"b": Outcome.PERSISTING})
    assert rows[0]["outcome"] == "new"


def test_serialize_failures_carries_the_progression_it_was_given():
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=(_make_item("a"),))
    rows = serialize_failures({"lint": group}, {"a": Outcome.PERSISTING})
    assert rows[0]["outcome"] == "persisting"


# ── Report keys ────────────────────────────────────────────────────────────

_SINGLE_SHOT_KEYS = [
    "repo", "branch", "pr_number", "run_id", "run_ids", "run_number",
    "head_sha", "conclusion", "behind_main", "failures", "progression",
    "resolved_since_prior",
]


def _build(**kwargs) -> CIReport:
    defaults = dict(
        repo="o/r", branch="feat", pr_number=42,
        run_state=_one_failure_run(), progression={"a": Outcome.NEW},
        prior_run=None, run_ids=[100], behind_main=0,
    )
    defaults.update(kwargs)
    return CIReport.build(**defaults)


def test_a_single_shot_report_carries_exactly_the_published_keys():
    assert list(_build().to_json()) == _SINGLE_SHOT_KEYS


def test_a_polled_report_adds_the_job_counts_and_nothing_else():
    counts = JobCounts(completed=3, failed=1, running=0, queued=0)
    report = _build(counts=counts).to_json()
    assert list(report) == _SINGLE_SHOT_KEYS + ["completed", "total"]
    assert report["completed"] == 3
    assert report["total"] == 3


def test_a_report_reads_its_run_ids_and_conclusion_off_the_run():
    report = _build(run_ids=[100, 200]).to_json()
    assert report["run_id"] == 100
    assert report["run_ids"] == [100, 200]
    assert report["run_number"] == 5
    assert report["head_sha"] == "abc1234"
    assert report["conclusion"] == "failure"


def test_a_report_with_no_prior_run_resolves_nothing():
    assert _build().to_json()["resolved_since_prior"] == []


def test_a_failure_the_prior_run_had_and_this_one_does_not_is_resolved():
    prior = FailureGroup(
        job="lint", kind=FailureKind.LINT,
        items=(_make_item("a"), _make_item("gone")),
    )
    report = _build(prior_run=_make_run(failures={"lint": prior})).to_json()
    assert report["resolved_since_prior"] == ["gone"]


def test_a_failure_still_present_is_not_reported_as_resolved():
    item = _make_item("a", headline="main.go:9:2: missing import")
    prior = FailureGroup(job="Analyze (go)", kind=FailureKind.BUILD, items=(item,))
    report = _build(prior_run=_make_run(failures={"analyze-go": prior})).to_json()
    assert report["resolved_since_prior"] == []


def test_a_report_serializes_its_progression_to_the_outcome_names():
    report = _build(progression={"a": Outcome.REGRESSED}).to_json()
    assert report["progression"] == {"a": "regressed"}
    assert report["failures"][0]["outcome"] == "regressed"


# ── Dashboard ──────────────────────────────────────────────────────────────

def test_render_dashboard_basic():
    item = _make_item("a", file="bin/foo.sh", line=42, annotation="SC2086: Double quote")
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    run = _make_run(run_id=123, run_number=7, failures={"shellcheck": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "Run #7" in dashboard
    assert "abc1234" in dashboard
    assert "lint" in dashboard.lower()
    assert "1 new" in dashboard.lower()


def test_render_dashboard_all_pass():
    run = _make_run(conclusion="success")
    dashboard = render_dashboard(run, {})
    assert "pass" in dashboard.lower() or "success" in dashboard.lower()


def test_render_dashboard_in_progress_no_failures():
    """In-progress runs with no failures yet should not say 'All checks passed'."""
    run = _make_run(status="in_progress", conclusion="")
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
    run = _make_run(run_id=456, run_number=8, failures={"shellcheck": group})
    progression = {"a": Outcome.NEW, "b": Outcome.PERSISTING, "c": Outcome.REGRESSED}
    dashboard = render_dashboard(run, progression)
    assert "1 new" in dashboard.lower()
    assert "1 persisting" in dashboard.lower()
    assert "1 regressed" in dashboard.lower()


def test_render_dashboard_with_headlines():
    item = _make_item("a", annotation="full context...",
                      headline="main.go:9:2: missing import")
    group = FailureGroup(job="Analyze (go)", kind=FailureKind.BUILD, items=(item,))
    run = _make_run(failures={"analyze-go": group})
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
    run = _make_run(failures={"build": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW, "b": Outcome.NEW, "c": Outcome.NEW})
    assert "same error (×3)" in dashboard
    assert dashboard.count("same error") == 1


def test_render_dashboard_truncates_at_five():
    items = [_make_item(f"item-{i}", headline=f"error {i}") for i in range(8)]
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=tuple(items))
    run = _make_run(failures={"lint": group})
    dashboard = render_dashboard(run, {f"item-{i}": Outcome.NEW for i in range(8)})
    assert "▸" in dashboard
    headline_lines = [line for line in dashboard.splitlines() if "▸" in line]
    assert len(headline_lines) == 5
    assert "… and 3 more" in dashboard


def test_render_dashboard_no_headline_falls_back_to_annotation():
    item = _make_item("a", annotation="SC2086: Double quote to prevent globbing")
    group = FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=(item,))
    run = _make_run(failures={"shellcheck": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "SC2086" in dashboard


def test_render_dashboard_shows_multiple_run_ids():
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(_make_item("a"),))
    run = _make_run(failures={"build": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, run_ids=[100, 200])
    assert "100" in dashboard
    assert "200" in dashboard
    assert "Workflow runs:" in dashboard


def test_render_dashboard_omits_run_ids_for_single_run():
    dashboard = render_dashboard(_make_run(), {}, run_ids=[100])
    assert "Workflow runs:" not in dashboard


def test_render_dashboard_shows_failed_step():
    item = _make_item("a", headline="Run 'mise run generate' locally and commit the changes.")
    group = FailureGroup(
        job="Generate & verify", kind=FailureKind.BUILD,
        items=(item,), failed_step="Generate & check drift",
    )
    run = _make_run(failures={"generate-verify": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "Generate & verify → Generate & check drift:" in dashboard


def test_render_dashboard_omits_arrow_without_failed_step():
    item = _make_item("a", headline="SC2086: Double quote")
    group = FailureGroup(job="shellcheck", kind=FailureKind.LINT, items=(item,))
    run = _make_run(failures={"shellcheck": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "shellcheck:" in dashboard
    assert "→" not in dashboard


def test_render_dashboard_show_status_in_progress():
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(_make_item("a"),))
    run = _make_run(status="in_progress", conclusion="", failures={"build": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— in progress" in dashboard
    assert "Run #5" in dashboard


def test_render_dashboard_show_status_complete():
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(_make_item("a"),))
    run = _make_run(failures={"build": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— complete" in dashboard


def test_render_dashboard_show_status_default_off():
    """Without show_status, header has no status suffix."""
    run = _make_run(status="in_progress", conclusion="")
    dashboard = render_dashboard(run, {})
    assert "— in progress" not in dashboard
    assert "— complete" not in dashboard


# ── Dashboard headline cap ─────────────────────────────────────────────────

def test_render_dashboard_truncates_a_long_annotation_it_falls_back_to():
    item = _make_item("a", annotation="x" * 500)
    group = FailureGroup(job="lint", kind=FailureKind.LINT, items=(item,))
    run = _make_run(failures={"lint": group})
    dashboard = render_dashboard(run, {"a": Outcome.NEW})
    assert "x" * ci_report._MAX_DASHBOARD_ANNOTATION in dashboard
    assert "x" * (ci_report._MAX_DASHBOARD_ANNOTATION + 1) not in dashboard
