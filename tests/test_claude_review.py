"""Tests for claude-review Python script — helper functions, archive, GC, summary."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ai" / "claude" / "bin" / "claude-review"
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from pr_state import ReviewStatus, ReviewVerdict
from review_common import (
    count_severity, json_summary, parse_review_verdict,
    read_pipeline_status, read_pipeline_warnings, review_file_path,
)
import review_gc


@pytest.fixture(scope="session")
def cr():
    bin_dir = str(SCRIPT_PATH.parent)
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("claude_review", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("claude_review", loader, origin=str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(SCRIPT_PATH)
    sys.modules["claude_review"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def reviews_dir(tmp_path, cr, monkeypatch):
    d = tmp_path / "reviews"
    d.mkdir()
    monkeypatch.setattr(cr, "REVIEWS_DIR", d)
    import review_common
    monkeypatch.setattr(review_common, "REVIEWS_DIR", d)
    monkeypatch.setattr(review_gc, "REVIEWS_DIR", d)
    return d


def _make_session_log(
    path, cost=1.0, input_tokens=100, output_tokens=200,
    duration_ms=60000, cache_read=0, cache_create=0,
    model_usage=None,
):
    model_usage_part = f',"modelUsage":{json.dumps(model_usage)}' if model_usage else ""
    Path(path).write_text(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}\n'
        f'{{"type":"result","subtype":"success","is_error":false,'
        f'"duration_ms":{duration_ms},"total_cost_usd":{cost},'
        f'"usage":{{"input_tokens":{input_tokens},"output_tokens":{output_tokens},'
        f'"cache_read_input_tokens":{cache_read},"cache_creation_input_tokens":{cache_create}}}'
        f'{model_usage_part}}}\n'
    )


# ── _is_pr_ref ────────────────────────────────────────────────────────────────


def test_is_pr_ref_bare_number(cr):
    assert cr._is_pr_ref("42") is True


def test_is_pr_ref_github_url(cr):
    assert cr._is_pr_ref("https://github.com/org/repo/pull/123") is True


def test_is_pr_ref_branch_name(cr):
    assert cr._is_pr_ref("isaac/feat/dream_scripts") is False


def test_is_pr_ref_branch_with_numbers(cr):
    assert cr._is_pr_ref("isaac/fix/PR-123-review") is False


def test_is_pr_ref_empty(cr):
    assert cr._is_pr_ref("") is False


# ── review_file_path ─────────────────────────────────────────────────────────


def test_review_file_basic(cr, reviews_dir):
    result = review_file_path("org/my-repo", "42")
    assert result == reviews_dir / "my-repo-42" / "review.md"


def test_review_file_repo_with_hyphens(cr, reviews_dir):
    result = review_file_path("org/my-cool-repo", "1")
    assert result == reviews_dir / "my-cool-repo-1" / "review.md"


def test_review_file_deep_nested_repo(cr, reviews_dir):
    result = review_file_path("deep/nested/repo", "7")
    assert result == reviews_dir / "repo-7" / "review.md"


# ── _format_usage ─────────────────────────────────────────────────────────────


def test_format_usage_single_log(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(log, cost=1.50, input_tokens=100, output_tokens=200, duration_ms=65000)
    result = cr._format_usage(log)
    assert "$1.50" in result
    assert "300" in result
    assert "1m 5s" in result


def test_format_usage_multiple_logs(cr, tmp_path):
    log1 = str(tmp_path / "session1.jsonl")
    log2 = str(tmp_path / "session2.jsonl")
    _make_session_log(log1, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=60000)
    _make_session_log(log2, cost=2.00, input_tokens=300, output_tokens=400, duration_ms=120000)
    result = cr._format_usage(log1, log2)
    assert "$3.00" in result
    assert "1k" in result
    assert "3m 0s" in result


def test_format_usage_no_result_lines(cr, tmp_path):
    log = str(tmp_path / "no-result.jsonl")
    Path(log).write_text('{"type":"assistant","message":{}}\n')
    assert cr._format_usage(log) == ""


def test_format_usage_empty_file(cr, tmp_path):
    log = str(tmp_path / "empty.jsonl")
    Path(log).write_text("")
    assert cr._format_usage(log) == ""


def test_format_usage_nonexistent_file(cr, tmp_path):
    assert cr._format_usage(str(tmp_path / "does-not-exist.jsonl")) == ""


def test_format_usage_mixed_existing_and_missing(cr, tmp_path):
    log = str(tmp_path / "real.jsonl")
    _make_session_log(log, cost=2.50, input_tokens=500, output_tokens=500, duration_ms=30000)
    result = cr._format_usage(log, str(tmp_path / "missing.jsonl"))
    assert "$2.50" in result
    assert "1k" in result


def test_format_usage_no_args(cr):
    assert cr._format_usage() == ""


def test_format_usage_tokens_under_1k_raw(cr, tmp_path):
    log = str(tmp_path / "small.jsonl")
    _make_session_log(log, cost=0.10, input_tokens=200, output_tokens=300, duration_ms=5000)
    result = cr._format_usage(log)
    assert "500 tokens" in result


def test_format_usage_tokens_over_1k_suffix(cr, tmp_path):
    log = str(tmp_path / "medium.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=800, output_tokens=700, duration_ms=10000)
    result = cr._format_usage(log)
    assert "1k tokens" in result


def test_format_usage_tokens_over_1m_suffix(cr, tmp_path):
    log = str(tmp_path / "large.jsonl")
    _make_session_log(
        log, cost=10.00, input_tokens=500000, output_tokens=600000,
        duration_ms=300000, cache_read=100000, cache_create=50000,
    )
    result = cr._format_usage(log)
    assert "1.2M tokens" in result
    assert "(100k cached)" in result


def test_format_usage_duration_seconds_only(cr, tmp_path):
    log = str(tmp_path / "short.jsonl")
    _make_session_log(log, cost=0.50, input_tokens=100, output_tokens=100, duration_ms=45000)
    result = cr._format_usage(log)
    assert "45s" in result


def test_format_usage_duration_minutes_and_seconds(cr, tmp_path):
    log = str(tmp_path / "long.jsonl")
    _make_session_log(log, cost=5.00, input_tokens=1000, output_tokens=1000, duration_ms=125000)
    result = cr._format_usage(log)
    assert "2m 5s" in result


def test_format_usage_cost_rounds_to_2_decimals(cr, tmp_path):
    log = str(tmp_path / "cost.jsonl")
    _make_session_log(log, cost=3.456, input_tokens=100, output_tokens=100, duration_ms=1000)
    result = cr._format_usage(log)
    assert "$3.46" in result


def test_format_usage_separates_cache_from_fresh(cr, tmp_path):
    log = str(tmp_path / "cache.jsonl")
    _make_session_log(
        log, cost=1.00, input_tokens=100, output_tokens=200,
        duration_ms=10000, cache_read=5000, cache_create=3000,
    )
    result = cr._format_usage(log)
    assert "8k tokens" in result
    assert "(5k cached)" in result


def test_format_usage_no_cache_omits_parenthetical(cr, tmp_path):
    log = str(tmp_path / "no-cache.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=10000)
    result = cr._format_usage(log)
    assert "300 tokens" in result
    assert "cached" not in result


def test_format_usage_wall_clock_override(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=600000)
    result = cr._format_usage(log, wall_clock_ms=120000)
    assert "2m 0s" in result
    assert "10m" not in result


def test_format_usage_total_includes_cache_reads(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(
        log, cost=1.0, input_tokens=100, output_tokens=200,
        duration_ms=10000, cache_read=10000,
    )
    result = cr._format_usage(log)
    assert "10k tokens" in result
    assert "(10k cached)" in result


def test_format_usage_model_usage_tokens(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(
        log, cost=2.0, input_tokens=100, output_tokens=200, duration_ms=10000,
        model_usage={
            "claude-sonnet-4-20250514": {
                "inputTokens": 500, "outputTokens": 300,
                "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 200,
            },
            "claude-haiku-4-5-20251001": {
                "inputTokens": 100, "outputTokens": 50,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
            },
        },
    )
    result = cr._format_usage(log)
    assert "2k tokens" in result
    assert "(1k cached)" in result


# ── count_severity ────────────────────────────────────────────────────────────


def test_count_severity_must_fix(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n"
        "- **[M1]** path:1 — description\n"
        "- **[M2]** path:2 — description\n"
        "## Should fix\n"
        "- **[S1]** path:3 — description\n"
    )
    assert count_severity(review, "M") == 2


def test_count_severity_excludes_strikethrough(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n"
        "- **[M1]** path:1 — active\n"
        "- ~~**[M2]** path:2 — resolved~~\n"
    )
    assert count_severity(review, "M") == 1


def test_count_severity_checkbox_findings(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n"
        "- [ ] **[M1]** path:1 — with checkbox\n"
        "- **[M2]** path:2 — without checkbox\n"
    )
    assert count_severity(review, "M") == 2


def test_count_severity_missing_file(cr, tmp_path):
    assert count_severity(tmp_path / "nonexistent.md", "M") == 0


def test_count_severity_empty_file(cr, tmp_path):
    review = tmp_path / "empty.md"
    review.write_text("")
    assert count_severity(review, "M") == 0


# ── json_summary ──────────────────────────────────────────────────────────────


def test_json_summary_with_findings(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** path:1 — bug\n"
        "## Should fix\n- **[S1]** path:2 — improvement\n- **[S2]** path:3 — improvement\n"
        "## Nit\n- **[N1]** path:4 — style\n"
        "## Idioms\n- **[I1]** path:5 — idiom\n- **[I2]** path:6 — idiom\n"
    )
    result = json_summary("org/repo", "42", str(review))
    assert result.startswith("REVIEW_SUMMARY:")
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["repo"] == "org/repo"
    assert data["pr_number"] == 42
    assert data["findings"]["must_fix"] == 1
    assert data["findings"]["should_fix"] == 2
    assert data["findings"]["nit"] == 1
    assert data["findings"]["idiom"] == 2
    assert data["findings"]["total"] == 6
    assert data["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


def test_json_summary_approve_no_must_fix(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Should fix\n- **[S1]** path:1 — improvement\n"
        "## Nit\n- **[N1]** path:2 — style\n"
    )
    result = json_summary("org/repo", "10", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.APPROVE.value
    assert data["findings"]["total"] == 2


def test_json_summary_includes_metadata(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "org-repo-42"
    review_dir.mkdir(parents=True)
    review = review_dir / "review.md"
    review.write_text("## Should fix\n- **[S1]** path:1 — improvement\n")
    meta = review_dir / "meta.json"
    meta.write_text(json.dumps({
        "head_sha": "abc123def456",
        "head_ref": "feat/my-branch",
        "base_ref": "main",
        "review_type": "full",
    }))
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["head_sha"] == "abc123def456"
    assert data["head_ref"] == "feat/my-branch"
    assert data["base_ref"] == "main"
    assert data["review_type"] == "full"


def test_json_summary_null_metadata_without_meta_json(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Should fix\n- **[S1]** path:1 — improvement\n")
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["head_sha"] is None
    assert data["head_ref"] is None
    assert data["base_ref"] is None
    assert data["review_type"] is None


def test_json_summary_missing_review_file(cr, tmp_path):
    result = json_summary("org/repo", "42", str(tmp_path / "nonexistent.md"))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["findings"]["total"] == 0
    assert data["verdict"] == ReviewVerdict.APPROVE.value


def test_json_summary_self_review_no_pr(cr, tmp_path):
    review = tmp_path / "self-review.md"
    review.write_text("## Must fix\n- **[M1]** path:1 — bug\n")
    result = json_summary("org/repo", "", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["pr_number"] is None
    assert data["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


def test_json_summary_includes_session_costs(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-42"
    review_dir.mkdir(parents=True)
    review = review_dir / "review.md"
    review.write_text("## Nit\n- **[N1]** path:1 — style\n")
    _make_session_log(
        str(review_dir / "session.jsonl"),
        cost=5.25, input_tokens=1000, output_tokens=2000, duration_ms=90000,
    )
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["cost_usd"] == pytest.approx(5.25)
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 2000
    assert data["duration_ms"] == 90000


# ── parse_review_verdict ──────────────────────────────────────────────────────


def test_parse_review_verdict_disapprove(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Summary\nSome text\n\n## Verdict\nDisapprove — wrong approach entirely.\n")
    assert parse_review_verdict(review) == ReviewVerdict.DISAPPROVE.value


def test_parse_review_verdict_disapprove_lowercase(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\ndisapprove — this should be a config change.\n")
    assert parse_review_verdict(review) == ReviewVerdict.DISAPPROVE.value


def test_parse_review_verdict_approve_returns_empty(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nApprove — looks good.\n")
    assert parse_review_verdict(review) == ""


def test_parse_review_verdict_request_changes_returns_empty(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nRequest changes — 2 must-fix.\n")
    assert parse_review_verdict(review) == ""


def test_parse_review_verdict_no_verdict_section(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Summary\nSome findings.\n## Must fix\n- **[M1]** a:1 — bug\n")
    assert parse_review_verdict(review) == ""


def test_parse_review_verdict_no_file(cr, tmp_path):
    assert parse_review_verdict(tmp_path / "nonexistent.md") == ""


def test_parse_review_verdict_none_path(cr):
    assert parse_review_verdict(None) == ""


def test_json_summary_verdict_disapprove_from_review(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-42"
    review_dir.mkdir(parents=True)
    review = review_dir / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** path:1 — bug\n\n"
        "## Verdict\nDisapprove — fundamentally wrong approach.\n"
    )
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.DISAPPROVE.value


def test_json_summary_verdict_not_overridden_by_approve(cr, tmp_path):
    """When review says Approve, mechanical verdict (from counts) still wins."""
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** path:1 — bug\n\n"
        "## Verdict\nApprove — looks fine.\n"
    )
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


# ── read_pipeline_status ──────────────────────────────────────────────────────


def test_read_pipeline_status_no_dir(cr):
    assert read_pipeline_status(None) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_no_file(cr, tmp_path):
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_synthesis_ok(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "",
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_synthesis_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "all groups failed",
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value


def test_read_pipeline_status_mechanical_fallback(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "mechanical fallback",
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_budget_exceeded(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "budget exceeded",
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_groups_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_failed": {"1": "no result record in session log"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_corrupt_json(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text("not valid json")
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_partial_groups_failed_synthesis_ok(cr, tmp_path):
    """Groups failed but synthesis succeeded → partial, not error."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1, 3], "groups_failed": {"2": "quota exhausted (429)"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_partial_mechanical_fallback(cr, tmp_path):
    """Synthesis fell back to mechanical merge → partial."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "mechanical fallback",
        "groups_done": [1], "groups_failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_error_all_groups_failed(cr, tmp_path):
    """All groups failed → error (not partial)."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "all groups failed",
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)", "2": "quota exhausted (429)"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value


def test_read_pipeline_status_complete_no_failures(cr, tmp_path):
    """Clean pipeline → completed."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1, 2], "groups_failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


# ── build_failure_detail ──────────────────────────────────────────────────────


def test_build_failure_detail_no_dir(cr):
    from review_common import build_failure_detail
    assert build_failure_detail(None) == ""


def test_build_failure_detail_no_failures(cr, tmp_path):
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1, 2], "groups_failed": {},
    }))
    assert build_failure_detail(tmp_path) == ""


def test_build_failure_detail_groups_failed(cr, tmp_path):
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)", "3": "agent hit max turns (5)"},
    }))
    result = build_failure_detail(tmp_path)
    assert "2/3 groups failed" in result
    assert "quota exhausted (429)" in result
    assert "agent hit max turns" in result


def test_build_failure_detail_reads_typed_diagnoses(cr, tmp_path):
    """The format `_write_pipeline_state` actually produces."""
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1],
        "groups_failed": {
            "2": {"kind": "quota_exhausted", "no_write_tool": False,
                  "detail": "", "num_turns": None},
            "3": {"kind": "max_turns", "no_write_tool": False,
                  "detail": "", "num_turns": 5},
        },
    }))
    result = build_failure_detail(tmp_path)
    assert "2/3 groups failed" in result
    assert "quota exhausted (429)" in result
    assert "agent hit max turns (5)" in result


def test_build_failure_detail_synthesis_failed(cr, tmp_path):
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "mechanical fallback",
        "groups_done": [1], "groups_failed": {},
    }))
    result = build_failure_detail(tmp_path)
    assert "synthesis" in result.lower()


def test_build_failure_detail_all_groups_failed(cr, tmp_path):
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "all groups failed",
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)", "2": "quota exhausted (429)"},
    }))
    result = build_failure_detail(tmp_path)
    assert "all groups failed" in result


def test_the_two_readers_agree_on_the_all_failed_sentinel(cr, tmp_path):
    """Status and detail answer the same question the same way.

    Synthesis records `all groups failed` when no group produced usable output,
    and the state can still carry fewer failure entries than there are groups —
    a group that crashed before it registered one. The two readers used to
    compute the all-failed rule separately, and only the status reader honoured
    the sentinel, so the review said `error` and `1/2 groups failed` at once.
    """
    from review_common import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "all groups failed",
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)"},
    }))

    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value
    assert build_failure_detail(tmp_path).startswith("all groups failed:")


def _self_review_dir(tmp_path, review_type: str) -> Path:
    """A self-review with one must-fix finding and no verdict of its own."""
    review_dir = tmp_path / "reviews" / "test-repo-self"
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text(
        "# Review\n<!-- head_sha: abc -->\n"
        "## Must Fix\n- **[M1]** `a.py:1` — broken\n"
    )
    (review_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/test-repo", "head_sha": "abc",
        "review_type": review_type, "mode": "self",
    }))
    return review_dir


def test_a_self_review_states_no_verdict(cr, tmp_path):
    """Mode decides whether there is a verdict to give, not review type.

    A self-review is advisory — nothing to approve or block. The sidecar has
    always carried both fields, but the check read `review_type == "self"`,
    which the writer never produces: it writes full or incremental there and
    puts self under `mode`. So the branch never fired and a self-review with a
    must-fix finding claimed `changes_requested` against a PR it has no say in.
    """
    from review_common import build_review_summary
    review_dir = _self_review_dir(tmp_path, "full")

    result = build_review_summary("owner/test-repo", "", str(review_dir / "review.md"))

    assert result["findings"]["must_fix"] == 1
    assert result["verdict"] == ""
    assert result["review_type"] == "full"


def test_an_incremental_self_review_states_no_verdict(cr, tmp_path):
    """The two fields are orthogonal — being incremental does not restore a verdict."""
    from review_common import build_review_summary
    review_dir = _self_review_dir(tmp_path, "incremental")

    result = build_review_summary("owner/test-repo", "", str(review_dir / "review.md"))

    assert result["verdict"] == ""
    assert result["review_type"] == "incremental"


def test_a_pr_review_still_requests_changes(cr, tmp_path):
    """The same finding under `mode: pr` keeps the verdict it always had."""
    from review_common import build_review_summary
    review_dir = _self_review_dir(tmp_path, "full")
    (review_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/test-repo", "head_sha": "abc",
        "review_type": "full", "mode": "pr",
    }))

    result = build_review_summary("owner/test-repo", "1", str(review_dir / "review.md"))

    assert result["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


def test_an_unknown_meta_vocabulary_reads_as_absent(cr, tmp_path):
    """meta.json outlives the code that wrote it.

    A member this version does not know reads as unset rather than raising, so
    one unrecognised field does not cost the whole summary.
    """
    from review_common import build_review_summary
    review_dir = _self_review_dir(tmp_path, "full")
    (review_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/test-repo", "head_sha": "abc",
        "review_type": "sampled", "mode": "audit",
    }))

    result = build_review_summary("owner/test-repo", "1", str(review_dir / "review.md"))

    assert result["review_type"] is None
    assert result["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


def test_json_summary_includes_failure_detail(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-1"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("# Review\n<!-- head_sha: abc -->\n## Summary\nNo findings.\n")
    pipeline = review_dir / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)"},
    }))
    from review_common import build_review_summary
    result = build_review_summary("owner/test-repo", "1", str(review_file))
    assert result["status"] == ReviewStatus.PARTIAL.value
    assert "1/2 groups failed" in result["failure_detail"]


# ── read_pipeline_warnings ────────────────────────────────────────────────────


def test_read_pipeline_warnings_no_dir(cr):
    assert read_pipeline_warnings(None) == []


def test_read_pipeline_warnings_no_file(cr, tmp_path):
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_all_complete(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "holistic_done": True, "groups_done": [1],
        "groups_failed": {},        "synthesis_done": True, "synthesis_failed": "",
    }))
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_holistic_incomplete(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "holistic_done": False,    }))
    assert read_pipeline_warnings(tmp_path) == ["holistic phase"]


def test_read_pipeline_warnings_groups_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "holistic_done": True,        "groups_failed": {"1": "max turns", "2": "model error"},
    }))
    assert read_pipeline_warnings(tmp_path) == ["2 groups failed"]


def test_read_pipeline_warnings_single_group_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "holistic_done": True,        "groups_failed": {"1": "max turns"},
    }))
    assert read_pipeline_warnings(tmp_path) == ["1 group failed"]


def test_read_pipeline_warnings_multiple(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "holistic_done": False,
        "synthesis_failed": "all groups failed",
    }))
    warnings = read_pipeline_warnings(tmp_path)
    assert "holistic phase" in warnings
    assert "synthesis" in warnings


def test_read_pipeline_warnings_skipped_phases_no_warning_when_synthesis_done(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "holistic_done": False,        "synthesis_done": True, "synthesis_failed": "",
    }))
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_corrupt_json(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text("not valid json")
    assert read_pipeline_warnings(tmp_path) == []


# ── _format_findings_line / _format_verdict ───────────────────────────────────


def test_format_findings_line_no_findings(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove\n")
    assert cr._format_findings_line(str(review)) == ""


def test_format_findings_line_nits_only(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Nit\n"
        "- **[N1]** `file.py:10` — style\n"
        "- **[N2]** `file.py:20` — naming\n"
        "\n## Verdict\nApprove\n"
    )
    assert cr._format_findings_line(str(review)) == "2 nit"


def test_format_findings_line_mixed(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n"
        "- **[M1]** `file.py:10` — bug\n"
        "\n## Should fix\n"
        "- **[S1]** `file.py:20` — cleanup\n"
        "\n## Nit\n"
        "- **[N1]** `file.py:30` — style\n"
        "- **[N2]** `file.py:40` — style\n"
        "\n## Verdict\nChanges requested\n"
    )
    assert cr._format_findings_line(str(review)) == "1 must fix, 1 should fix, 2 nit"


def test_format_findings_line_nonexistent_file(cr):
    assert cr._format_findings_line("/nonexistent/review.md") == ""


def test_format_verdict_approve(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nApprove\n")
    assert cr._format_verdict(str(review)) == "Approve"


def test_format_verdict_with_must_fix(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** `file.py:10` — bug\n\n## Verdict\nApprove\n"
    )
    assert cr._format_verdict(str(review)) == "Changes requested"


def test_format_verdict_explicit_disapprove(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nDisapprove\n")
    assert cr._format_verdict(str(review)) == "Disapprove"


def test_format_verdict_nonexistent_file(cr):
    assert cr._format_verdict("/nonexistent/review.md") == ""


def test_json_summary_status_completed_no_pipeline(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Nit\n- **[N1]** path:1 — style\n")
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["status"] == ReviewStatus.COMPLETED.value


def test_json_summary_status_error_synthesis_failed(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-42"
    review_dir.mkdir(parents=True)
    review = review_dir / "review.md"
    review.write_text("## Nit\n- **[N1]** path:1 — style\n")
    pipeline = review_dir / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "all groups failed",
    }))
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["status"] == ReviewStatus.ERROR.value


# ── _archive_review ───────────────────────────────────────────────────────────


def test_archive_creates_prior_and_timestamped_archive(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("old review")
    session_log.write_text("old session")

    prior_path = cr._archive_review(review_file, str(session_log))

    assert os.path.isfile(prior_path)
    assert prior_path.endswith("prior.md")
    assert Path(prior_path).read_text() == "old review"
    assert not review_file.exists()
    assert not session_log.exists()
    archives = list((review_dir / "archives").glob("2*.md"))
    assert len(archives) == 1


def test_archive_no_existing_review_empty_prior(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-99"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"

    prior_path = cr._archive_review(review_file, str(session_log))
    assert prior_path == ""


def test_archive_prunes_old_archives(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-1"
    archive_dir = review_dir / "archives"
    archive_dir.mkdir(parents=True)

    for i in range(1, 6):
        (archive_dir / f"2025010{i}-120000.md").write_text(f"archive {i}")
        (archive_dir / f"2025010{i}-120000.session.jsonl").write_text(f"session {i}")

    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("current review")
    session_log.write_text("current session")

    cr._archive_review(review_file, str(session_log))

    md_archives = list(archive_dir.glob("2*.md"))
    assert len(md_archives) <= cr.ARCHIVE_KEEP_COUNT


def test_archive_intermediates_untouched(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-50"
    review_dir.mkdir(parents=True)
    (review_dir / "group-1.jsonl").write_text("group1")
    (review_dir / "group-1.md").write_text("group1md")
    (review_dir / "meta.json").write_text("meta")

    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"

    prior_path = cr._archive_review(review_file, str(session_log))

    assert prior_path == ""
    assert (review_dir / "group-1.jsonl").exists()
    assert (review_dir / "group-1.md").exists()
    assert (review_dir / "meta.json").exists()


def test_archive_post_jsonl(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-60"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("review")
    (review_dir / "post.jsonl").write_text("post data")

    cr._archive_review(review_file, str(review_dir / "session.jsonl"))

    assert not (review_dir / "post.jsonl").exists()
    post_archives = list((review_dir / "archives").glob("2*.post.jsonl"))
    assert len(post_archives) == 1


def test_archive_self_review_paths(cr, tmp_path):
    review_dir = tmp_path / "project" / "ignore" / "reviews"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "self-review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("self-review content")
    session_log.write_text("session data")

    prior_path = cr._archive_review(review_file, str(session_log))

    assert os.path.isfile(prior_path)
    assert "prior.md" in prior_path
    assert Path(prior_path).read_text() == "self-review content"
    assert not review_file.exists()
    assert not session_log.exists()


# ── _resolve_prior_review ────────────────────────────────────────────────────


def test_resolve_prior_resume_true_returns_existing_prior(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior_file = review_dir / "prior.md"
    review_file.write_text("## Review")
    prior_file.write_text("## Prior")

    result = cr._resolve_prior_review(review_file, "", True)
    assert result == str(prior_file)


def test_resolve_prior_resume_true_no_prior_returns_empty(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("## Review")

    result = cr._resolve_prior_review(review_file, "", True)
    assert result == ""


def test_resolve_prior_resume_false_archives(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-43"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("## Review")
    session_log.write_text("{}")

    result = cr._resolve_prior_review(review_file, str(session_log), False)

    assert not review_file.exists()
    assert result != ""


# ── _cleanup_prior_review ────────────────────────────────────────────────────


def test_cleanup_prior_removes_when_no_pipeline(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior = review_dir / "prior.md"
    prior.write_text("prior content")

    cr._cleanup_prior_review(review_file, str(prior))
    assert not prior.exists()


def test_cleanup_prior_keeps_when_pipeline_exists(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior = review_dir / "prior.md"
    pipeline = review_dir / "pipeline.json"
    prior.write_text("prior content")
    pipeline.write_text("{}")

    cr._cleanup_prior_review(review_file, str(prior))
    assert prior.exists()


def test_cleanup_prior_empty_path_is_noop(cr, tmp_path):
    review_file = tmp_path / "review.md"
    cr._cleanup_prior_review(review_file, "")


# ── gc_reviews ─────────────────────────────────────────────────────────────────


def test_gc_removes_orphaned_stale_dirs(cr, reviews_dir):
    orphan = reviews_dir / "test-repo-100"
    orphan.mkdir()
    (orphan / "pipeline.json").write_text("{}")
    (orphan / "group-1.jsonl").write_text("{}")
    for f in orphan.iterdir():
        os.utime(str(f), (1622505600, 1622505600))

    has_review = reviews_dir / "test-repo-200"
    has_review.mkdir()
    (has_review / "review.md").write_text("## Review")
    (has_review / "pipeline.json").write_text("{}")

    review_gc.gc_reviews(reviews_dir)

    assert not orphan.exists()
    assert has_review.exists()


def test_gc_removes_stale_intermediates(cr, reviews_dir):
    d = reviews_dir / "test-repo-300"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl"):
        p = d / f_name
        p.write_text("{}")
        os.utime(str(p), (1622505600, 1622505600))

    review_gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    assert not (d / "group-1.md").exists()
    assert not (d / "group-1.jsonl").exists()
    assert not (d / "holistic.md").exists()
    assert not (d / "holistic.jsonl").exists()


def test_gc_removes_stale_logs_for_every_phase(cr, reviews_dir):
    """The log half of the glob is derived from Phase — every phase that
    writes a session log of its own must be collected, not just the ones a
    hand-written list happened to name."""
    d = reviews_dir / "test-repo-320"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    log_names = ("holistic.jsonl", "scout.jsonl", "group-1.jsonl", "synthesis.jsonl", "disprove.jsonl", "fix.jsonl")
    for f_name in log_names:
        p = d / f_name
        p.write_text("{}")
        os.utime(str(p), (1622505600, 1622505600))

    review_gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    for f_name in log_names:
        assert not (d / f_name).exists(), f"{f_name} should have been collected"


def test_gc_preserves_recent_intermediates(cr, reviews_dir):
    d = reviews_dir / "test-repo-350"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl", "synthesis.jsonl"):
        (d / f_name).write_text("{}")

    review_gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl", "synthesis.jsonl"):
        assert (d / f_name).exists()


def test_gc_preserves_active_pipeline(cr, reviews_dir):
    d = reviews_dir / "test-repo-400"
    d.mkdir()
    (d / "pipeline.json").write_text("{}")
    (d / "group-1.jsonl").write_text("{}")

    review_gc.gc_reviews(reviews_dir)

    assert d.exists()
    assert (d / "group-1.jsonl").exists()


# ── stray files at the reviews root ──────────────────────────────────────────

STALE_MTIME = (1622505600, 1622505600)


def test_gc_removes_stale_stray_files(cr, reviews_dir):
    strays = ("check_hunks.py", "backfill_pr842.sql", "earning_pr829.go")
    for name in strays:
        p = reviews_dir / name
        p.write_text("scratch")
        os.utime(str(p), STALE_MTIME)

    cleaned = review_gc.gc_reviews(reviews_dir)

    assert cleaned == len(strays)
    for name in strays:
        assert not (reviews_dir / name).exists()


def test_gc_removes_stranded_flat_artifacts(cr, reviews_dir):
    """Suffixed leftovers from the flat layout whose `.md` is gone are unclaimable."""
    stranded = reviews_dir / "maximum-1403.holistic.jsonl"
    stranded.write_text("{}")
    os.utime(str(stranded), STALE_MTIME)

    review_gc.gc_reviews(reviews_dir)

    assert not stranded.exists()


def test_gc_keeps_flat_artifacts_the_migration_still_claims(cr, reviews_dir):
    """A flat `.md` at the root means the startup migration owns its siblings."""
    for name in ("maximum-1403.md", "maximum-1403.holistic.jsonl"):
        p = reviews_dir / name
        p.write_text("{}")
        os.utime(str(p), STALE_MTIME)

    review_gc.gc_reviews(reviews_dir)

    assert (reviews_dir / "maximum-1403.md").exists()
    assert (reviews_dir / "maximum-1403.holistic.jsonl").exists()


def test_gc_preserves_recent_stray_files(cr, reviews_dir):
    """A stray from a run still in flight is not garbage yet."""
    stray = reviews_dir / "check_hunks.py"
    stray.write_text("scratch")

    review_gc.gc_reviews(reviews_dir)

    assert stray.exists()


# ── prune_merged_reviews ─────────────────────────────────────────────────────


@patch("review_gc.subprocess.run")
def test_prune_removes_merged_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-42"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "session.jsonl").write_text("session data")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "42", "head_sha": "abc",
    }))

    old_time = time.time() - 8 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if "gh" in cmd[0] and "pr" in cmd:
            m.returncode = 0
            m.stdout = "MERGED\n"
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    review_gc.prune_merged_reviews(reviews_dir)

    assert not d.exists()


@patch("review_gc.subprocess.run")
def test_prune_keeps_open_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-99"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "99", "head_sha": "def",
    }))

    old_time = time.time() - 8 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if "gh" in cmd[0] and "pr" in cmd:
            m.returncode = 0
            m.stdout = "OPEN\n"
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    review_gc.prune_merged_reviews(reviews_dir)

    assert d.exists()
    assert (d / "review.md").exists()


@patch("review_gc.subprocess.run")
def test_prune_keeps_recent_merged_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-50"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "50", "head_sha": "abc",
    }))

    review_gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "recently-modified merged review should be retained"
    mock_run.assert_not_called()


@patch("review_gc.subprocess.run")
def test_prune_keeps_recent_failed_review(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-51"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "51", "head_sha": "abc",
    }))
    (d / "pipeline.json").write_text(json.dumps({
        "synthesis_failed": "all groups failed",
    }))

    old_time = time.time() - 15 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    mock_run.side_effect = lambda cmd, **kw: MagicMock(returncode=0, stdout="MERGED\n")
    review_gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "failed review within 30-day window should be retained"


@patch("review_gc.subprocess.run")
def test_prune_removes_old_failed_review(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-52"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "52", "head_sha": "abc",
    }))
    (d / "pipeline.json").write_text(json.dumps({
        "synthesis_failed": "all groups failed",
    }))

    old_time = time.time() - 35 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    mock_run.side_effect = lambda cmd, **kw: MagicMock(returncode=0, stdout="MERGED\n")
    review_gc.prune_merged_reviews(reviews_dir)

    assert not d.exists(), "failed review older than 30 days should be pruned"


# ── _confirm ──────────────────────────────────────────────────────────────────


def _patch_confirm_input(monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda _: answer)
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))


def test_confirm_yes(cr, monkeypatch):
    _patch_confirm_input(monkeypatch, "y")
    assert cr._confirm("Continue?") is True


def test_confirm_empty_defaults_yes(cr, monkeypatch):
    _patch_confirm_input(monkeypatch, "")
    assert cr._confirm("Continue?") is True


def test_confirm_no(cr, monkeypatch):
    _patch_confirm_input(monkeypatch, "n")
    assert cr._confirm("Continue?") is False


def test_confirm_eof_defaults_no(cr, monkeypatch):
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    assert cr._confirm("Continue?") is False


# ── CLI argument parsing ──────────────────────────────────────────────────────


def test_argparse_self_flag(cr):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--self", action="store_true", dest="self_review")
    parsed = parser.parse_args(["--self"])
    assert parsed.self_review is True


def test_argparse_json_summary_not_positional(cr):
    """--json-summary should be parsed as a flag, not treated as a PR number."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-summary", action="store_true")
    parser.add_argument("args", nargs="*")
    parsed = parser.parse_args(["--json-summary", "42"])
    assert parsed.json_summary is True
    assert parsed.args == ["42"]


# ── _dir_is_all_stale ────────────────────────────────────────────────────────


def test_gc_dir_all_stale(cr, tmp_path):
    d = tmp_path / "stale-dir"
    d.mkdir()
    f = d / "old.jsonl"
    f.write_text("{}")
    os.utime(str(f), (1622505600, 1622505600))
    assert review_gc._dir_is_all_stale(d) is True


def test_gc_dir_has_recent_files(cr, tmp_path):
    d = tmp_path / "mixed-dir"
    d.mkdir()
    old = d / "old.jsonl"
    old.write_text("{}")
    os.utime(str(old), (1622505600, 1622505600))
    (d / "new.jsonl").write_text("{}")
    assert review_gc._dir_is_all_stale(d) is False


def test_gc_dir_empty(cr, tmp_path):
    d = tmp_path / "empty-dir"
    d.mkdir()
    assert review_gc._dir_is_all_stale(d) is True


# ── _clean_intermediates ─────────────────────────────────────────────────────


def test_gc_clean_intermediates_removes_stale(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "group-1.jsonl", "synthesis.jsonl"):
        f = d / name
        f.write_text("{}")
        os.utime(str(f), (1622505600, 1622505600))
    (d / "meta.json").write_text("{}")

    count = review_gc._clean_intermediates(d)
    assert count == 3
    assert not (d / "group-1.md").exists()
    assert (d / "meta.json").exists()


def test_gc_clean_intermediates_preserves_recent(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "holistic.jsonl"):
        (d / name).write_text("{}")

    count = review_gc._clean_intermediates(d)
    assert count == 0
    assert (d / "group-1.md").exists()


# ── _generator_version ────────────────────────────────────────────────────────


def test_generator_version_returns_string(cr):
    ver = cr._generator_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


# ── Constants ─────────────────────────────────────────────────────────────────


def test_constants_match_expected(cr):
    assert cr.ARCHIVE_KEEP_COUNT == 3
    assert cr.DEFAULT_MAX_PARALLEL == 1
    assert review_gc.GC_STALE_DAYS == 7
    assert review_gc.PRUNE_MAX_FILES == 10
    assert len(cr.SEVERITY_PREFIXES) == 4
    assert len(cr.SEVERITY_JSON_KEYS) == 4


# ── _resolve_recover_sha ──────────────────────────────────────────────────────


def _write_partial_pipeline(review_dir: Path, head_sha: str = "abc1234") -> None:
    (review_dir / "pipeline.json").write_text(json.dumps({
        "head_sha": head_sha, "group_names": ["g1", "g2"],
        "synthesis_done": False, "synthesis_failed": "crashed",
        "groups_done": [1], "groups_failed": {},
    }))


def test_resolve_recover_sha_returns_recorded_sha(cr, tmp_path):
    _write_partial_pipeline(tmp_path)
    assert cr._resolve_recover_sha(tmp_path, "abc1234") == "abc1234"


def test_resolve_recover_sha_pins_when_head_moved(cr, tmp_path):
    """New commits must not abort recovery — the run completes at its own commit."""
    _write_partial_pipeline(tmp_path)
    assert cr._resolve_recover_sha(tmp_path, "def5678") == "abc1234"


def test_resolve_recover_sha_without_head(cr, tmp_path):
    """Empty head_sha means HEAD couldn't be determined — still pin to the record."""
    _write_partial_pipeline(tmp_path)
    assert cr._resolve_recover_sha(tmp_path, "") == "abc1234"


def test_resolve_recover_sha_untracked_state(cr, tmp_path):
    """State written before SHA tracking has nothing to pin to."""
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "group_names": ["g1"], "synthesis_done": False,
        "synthesis_failed": "crashed", "groups_done": [],
    }))
    assert cr._resolve_recover_sha(tmp_path, "abc1234") == ""


def test_resolve_recover_sha_without_pipeline_state(cr, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cr._resolve_recover_sha(tmp_path, "abc1234")
    assert exc.value.code == 1


def test_resolve_recover_sha_completed_review(cr, tmp_path):
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc1234", "group_names": ["g1"], "synthesis_done": True,
        "synthesis_failed": "", "groups_done": [1], "groups_failed": {},
    }))
    with pytest.raises(SystemExit) as exc:
        cr._resolve_recover_sha(tmp_path, "abc1234")
    assert exc.value.code == 0


# ── _pin_recover_worktree ─────────────────────────────────────────────────────


def test_pin_recover_worktree_noop_when_head_matches(cr, monkeypatch):
    head_sha = MagicMock(return_value="abc1234")
    monkeypatch.setattr(cr.pr_context, "head_sha", head_sha)
    detach = MagicMock()
    monkeypatch.setattr(cr.review_worktree, "detached_worktree_at", detach)

    assert cr._pin_recover_worktree("abc1234", "/wt", "/repo", "l") == ("/wt", None)
    assert detach.call_count == 0
    assert head_sha.call_args.args == ("/wt",)


def test_pin_recover_worktree_checks_out_pinned_commit(cr, monkeypatch):
    monkeypatch.setattr(cr.pr_context, "head_sha", lambda cwd=None: "def5678")
    pinned = cr.review_worktree.WorktreeResult(
        path="/repo/.worktrees/l", cleanup_ref="/repo/.worktrees/l", is_fallback=True)
    monkeypatch.setattr(
        cr.review_worktree, "detached_worktree_at",
        lambda sha, repo_dir, label: pinned,
    )

    path, result = cr._pin_recover_worktree("abc1234", "/wt", "/repo", "l")

    assert path == "/repo/.worktrees/l"
    assert result is pinned


def test_pin_recover_worktree_exits_when_commit_gone(cr, monkeypatch):
    monkeypatch.setattr(cr.pr_context, "head_sha", lambda cwd=None: "def5678")
    monkeypatch.setattr(
        cr.review_worktree, "detached_worktree_at", lambda *a, **kw: None)

    with pytest.raises(SystemExit) as exc:
        cr._pin_recover_worktree("abc1234", "/wt", "/repo", "l")
    assert exc.value.code == 1


def test_build_orchestrate_args_passes_recover_sha(cr, tmp_path):
    args = cr._build_orchestrate_args(
        pr_number="1", repo="owner/repo", review_file=tmp_path / "review.md",
        wt_path="/wt", session_log="", prior_review_path="", issue_link="",
        issue_context="", max_parallel=1, no_holistic=False, max_cost=None,
        model=None, recover_sha="abc1234",
    )
    assert args[args.index("--recover-sha") + 1] == "abc1234"


def test_build_orchestrate_args_omits_empty_recover_sha(cr, tmp_path):
    args = cr._build_orchestrate_args(
        pr_number="1", repo="owner/repo", review_file=tmp_path / "review.md",
        wt_path="/wt", session_log="", prior_review_path="", issue_link="",
        issue_context="", max_parallel=1, no_holistic=False, max_cost=None,
        model=None,
    )
    assert "--recover-sha" not in args


# ── --recover with --self ─────────────────────────────────────────────────────


def test_self_review_accepts_recover(cr, reviews_dir, monkeypatch):
    """--recover is a top-level mode; --self must not reject it."""
    monkeypatch.setattr(sys, "argv", ["claude-review", "--self", "--recover"])
    monkeypatch.setattr(cr, "_migrate_legacy_reviews", lambda: None)
    monkeypatch.setattr(cr, "_migrate_flat_reviews", lambda: None)
    run_self = MagicMock()
    monkeypatch.setattr(cr, "_run_self_review", run_self)

    cr.main()

    assert run_self.call_count == 1
    assert run_self.call_args[0][0].recover is True


def test_self_review_recover_reads_head_after_worktree_switch(cr, reviews_dir, monkeypatch):
    """Checking out the target moves HEAD — the recover sha must come from the new worktree."""
    ctx = SimpleNamespace(
        repo="owner/repo", pr_number=None, branch="feat/x", head_sha="stale00",
    )
    monkeypatch.setattr(cr, "_resolve_wt_path", lambda repo_dir, pr_input: "/orig/wt")
    monkeypatch.setattr(cr, "_resolve_branch_input", lambda pr_input, repo_dir: pr_input)
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: ctx)
    monkeypatch.setattr(
        cr.review_worktree, "switch_to_branch",
        lambda branch, wt: cr.review_worktree.WorktreeResult(
            path="/switched/wt", cleanup_ref=branch, is_fallback=False),
    )
    monkeypatch.setattr(
        cr.pr_context, "head_sha",
        lambda cwd=None: "fresh11" if cwd == "/switched/wt" else "stale00",
    )
    monkeypatch.setattr(cr, "_cleanup_self_review_worktree", lambda *a, **kw: None)
    body = MagicMock()
    monkeypatch.setattr(cr, "_run_self_review_body", body)

    cr._run_self_review(SimpleNamespace(
        positional=["feat/x"], issue=None, max_parallel=1, skip_user_verification=True,
        force=False, no_holistic=False, no_scout=False, disprove=None, max_cost=None,
        model=None, repo_dir="", fix=False, effort="medium", max_groups=None,
        generated=False, recover=True, debug=False,
    ))

    assert body.call_args.kwargs["head_sha"] == "fresh11"


def test_self_review_body_validates_recover(cr, tmp_path):
    """recover=True reaches _resolve_recover_sha — no pipeline state aborts the run."""
    with pytest.raises(SystemExit) as exc:
        cr._run_self_review_body(
            "owner/repo", "", str(tmp_path), "", 1,
            False, None, None, False, MagicMock(),
            self_review_dir=tmp_path, branch_name="feat/x",
            recover=True, head_sha="abc1234",
        )
    assert exc.value.code == 1


def test_self_review_body_runs_recover_in_pinned_worktree(cr, tmp_path, monkeypatch):
    """New commits since the failed run: orchestrate runs against the pinned checkout."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(cr.pr_context, "head_sha", lambda cwd=None: "def5678")
    pinned = cr.review_worktree.WorktreeResult(
        path="/pinned/wt", cleanup_ref="/pinned/wt", is_fallback=True)
    monkeypatch.setattr(
        cr.review_worktree, "detached_worktree_at",
        lambda sha, repo_dir, label: pinned,
    )
    cleanup = MagicMock()
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", cleanup)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(cr.review_issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(cr.review_issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    run = MagicMock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(cr.subprocess, "run", run)
    monkeypatch.setattr(cr, "_fail_orchestration",
                        MagicMock(side_effect=SystemExit(1)))

    with pytest.raises(SystemExit):
        cr._run_self_review_body(
            "owner/repo", "", str(tmp_path), "", 1,
            False, None, None, False, MagicMock(),
            self_review_dir=tmp_path, branch_name="feat/x",
            recover=True, head_sha="def5678",
        )

    orchestrate_args = run.call_args[0][0]
    assert orchestrate_args[orchestrate_args.index("--repo-dir") + 1] == "/pinned/wt"
    assert orchestrate_args[orchestrate_args.index("--recover-sha") + 1] == "abc1234"
    assert cleanup.call_args[0][0] is pinned


def test_self_review_body_rejects_fix_on_drifted_recover(cr, tmp_path, monkeypatch):
    """Fixes written to a throwaway checkout would be discarded — refuse up front."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(cr.pr_context, "head_sha", lambda cwd=None: "def5678")

    with pytest.raises(SystemExit) as exc:
        cr._run_self_review_body(
            "owner/repo", "", str(tmp_path), "", 1,
            False, None, None, True, MagicMock(),
            self_review_dir=tmp_path, branch_name="feat/x",
            recover=True, head_sha="def5678",
        )
    assert exc.value.code == 1


def test_self_review_body_allows_fix_when_recover_has_not_drifted(cr, tmp_path, monkeypatch):
    """No drift means no throwaway checkout, so --fix edits the real worktree."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(cr.pr_context, "head_sha", lambda cwd=None: "abc1234")
    detach = MagicMock()
    monkeypatch.setattr(cr.review_worktree, "detached_worktree_at", detach)
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", MagicMock())
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(cr.review_issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(cr.review_issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    run = MagicMock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(cr.subprocess, "run", run)
    monkeypatch.setattr(cr, "_fail_orchestration",
                        MagicMock(side_effect=SystemExit(1)))

    with pytest.raises(SystemExit):
        cr._run_self_review_body(
            "owner/repo", "", str(tmp_path), "", 1,
            False, None, None, True, MagicMock(),
            self_review_dir=tmp_path, branch_name="feat/x",
            recover=True, head_sha="abc1234",
        )

    orchestrate_args = run.call_args[0][0]
    assert orchestrate_args[orchestrate_args.index("--repo-dir") + 1] == str(tmp_path)
    assert "--fix" in orchestrate_args
    assert detach.call_count == 0


# ── _check_stale_review ───────────────────────────────────────────────────────


def test_check_stale_review_auto_recovers_on_failures(cr, tmp_path, monkeypatch):
    """Same HEAD + pipeline failures → no prompt, returns silently (auto-recover)."""
    review_file = tmp_path / "review.md"
    review_file.write_text("# Review\n<!-- head_sha: abc123 -->\n## Summary\n")
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc123", "group_names": ["g1", "g2"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)"},
    }))

    # Mock gh to return matching HEAD
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "abc123\n", "returncode": 0})())
    # Verify _confirm is never called — auto-recovery must skip the prompt
    monkeypatch.setattr(cr, "_confirm", MagicMock(side_effect=AssertionError("_confirm called unexpectedly")))

    cr._check_stale_review("owner/repo", "1", review_file, force=False)


def test_check_stale_review_prompts_on_clean_same_head(cr, tmp_path, monkeypatch):
    """Same HEAD + no failures → still prompts 'Re-review anyway?'."""
    review_file = tmp_path / "review.md"
    review_file.write_text("# Review\n<!-- head_sha: abc123 -->\n## Summary\n")
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc123", "group_names": ["g1"],
        "synthesis_done": True, "synthesis_failed": "",
        "groups_done": [1], "groups_failed": {},
    }))

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "abc123\n", "returncode": 0})())
    monkeypatch.setattr(cr, "_confirm", lambda msg: False)

    with pytest.raises(SystemExit):
        cr._check_stale_review("owner/repo", "1", review_file, force=False)


