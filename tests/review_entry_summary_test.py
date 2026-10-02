"""Tests for review.summary — usage formatting, the JSON summary, findings and verdict lines."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from pr.domains import ReviewStatus, ReviewVerdict
from review.summary import (
    build_review_summary, format_findings_line, format_usage, format_verdict,
    json_summary,
)

# cr is a pytest fixture: imported by name so tests here can request it.
from review_entry_support import cr


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


# ── _format_usage ─────────────────────────────────────────────────────────────


def test_format_usage_single_log(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(log, cost=1.50, input_tokens=100, output_tokens=200, duration_ms=65000)
    result = format_usage(log)
    assert "$1.50" in result
    assert "300" in result
    assert "1m 5s" in result


def test_format_usage_multiple_logs(cr, tmp_path):
    log1 = str(tmp_path / "session1.jsonl")
    log2 = str(tmp_path / "session2.jsonl")
    _make_session_log(log1, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=60000)
    _make_session_log(log2, cost=2.00, input_tokens=300, output_tokens=400, duration_ms=120000)
    result = format_usage(log1, log2)
    assert "$3.00" in result
    assert "1.0k" in result
    assert "3m 0s" in result


def test_format_usage_no_result_lines(cr, tmp_path):
    log = str(tmp_path / "no-result.jsonl")
    Path(log).write_text('{"type":"assistant","message":{}}\n')
    assert format_usage(log) == ""


def test_format_usage_empty_file(cr, tmp_path):
    log = str(tmp_path / "empty.jsonl")
    Path(log).write_text("")
    assert format_usage(log) == ""


def test_format_usage_nonexistent_file(cr, tmp_path):
    assert format_usage(str(tmp_path / "does-not-exist.jsonl")) == ""


def test_format_usage_mixed_existing_and_missing(cr, tmp_path):
    log = str(tmp_path / "real.jsonl")
    _make_session_log(log, cost=2.50, input_tokens=500, output_tokens=500, duration_ms=30000)
    result = format_usage(log, str(tmp_path / "missing.jsonl"))
    assert "$2.50" in result
    assert "1.0k" in result


def test_format_usage_no_args(cr):
    assert format_usage() == ""


def test_format_usage_tokens_under_1k_raw(cr, tmp_path):
    log = str(tmp_path / "small.jsonl")
    _make_session_log(log, cost=0.10, input_tokens=200, output_tokens=300, duration_ms=5000)
    result = format_usage(log)
    assert "500 tokens" in result


def test_format_usage_tokens_over_1k_suffix(cr, tmp_path):
    log = str(tmp_path / "medium.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=800, output_tokens=700, duration_ms=10000)
    result = format_usage(log)
    assert "1.5k tokens" in result


def test_format_usage_tokens_over_1m_suffix(cr, tmp_path):
    log = str(tmp_path / "large.jsonl")
    _make_session_log(
        log, cost=10.00, input_tokens=500000, output_tokens=600000,
        duration_ms=300000, cache_read=100000, cache_create=50000,
    )
    result = format_usage(log)
    assert "1.2M tokens" in result
    assert "(100.0k cached)" in result


def test_format_usage_duration_seconds_only(cr, tmp_path):
    log = str(tmp_path / "short.jsonl")
    _make_session_log(log, cost=0.50, input_tokens=100, output_tokens=100, duration_ms=45000)
    result = format_usage(log)
    assert "45s" in result


def test_format_usage_duration_minutes_and_seconds(cr, tmp_path):
    log = str(tmp_path / "long.jsonl")
    _make_session_log(log, cost=5.00, input_tokens=1000, output_tokens=1000, duration_ms=125000)
    result = format_usage(log)
    assert "2m 5s" in result


def test_format_usage_cost_rounds_to_2_decimals(cr, tmp_path):
    log = str(tmp_path / "cost.jsonl")
    _make_session_log(log, cost=3.456, input_tokens=100, output_tokens=100, duration_ms=1000)
    result = format_usage(log)
    assert "$3.46" in result


def test_format_usage_separates_cache_from_fresh(cr, tmp_path):
    log = str(tmp_path / "cache.jsonl")
    _make_session_log(
        log, cost=1.00, input_tokens=100, output_tokens=200,
        duration_ms=10000, cache_read=5000, cache_create=3000,
    )
    result = format_usage(log)
    assert "8.3k tokens" in result
    assert "(5.0k cached)" in result


def test_format_usage_no_cache_omits_parenthetical(cr, tmp_path):
    log = str(tmp_path / "no-cache.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=10000)
    result = format_usage(log)
    assert "300 tokens" in result
    assert "cached" not in result


def test_format_usage_wall_clock_override(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(log, cost=1.00, input_tokens=100, output_tokens=200, duration_ms=600000)
    result = format_usage(log, wall_clock_ms=120000)
    assert "2m 0s" in result
    assert "10m" not in result


def test_format_usage_total_includes_cache_reads(cr, tmp_path):
    log = str(tmp_path / "session.jsonl")
    _make_session_log(
        log, cost=1.0, input_tokens=100, output_tokens=200,
        duration_ms=10000, cache_read=10000,
    )
    result = format_usage(log)
    assert "10.3k tokens" in result
    assert "(10.0k cached)" in result


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
    result = format_usage(log)
    assert "2.1k tokens" in result
    assert "(1.0k cached)" in result


# ── json_summary ──────────────────────────────────────────────────────────────


_FD_PROBE_RANGE = 256  # comfortably above what a test process opens


def _open_fd_count() -> int:
    """How many descriptors this process holds open.

    `resource.getrlimit` is not it — the interest is in what is *used*, not
    what is permitted. Probing each descriptor is portable where `/proc/self/fd`
    is not, and the range is small enough that the cost does not matter.
    """
    import fcntl
    open_fds = 0
    for fd in range(_FD_PROBE_RANGE):
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError:
            continue
        open_fds += 1
    return open_fds


def test_json_summary_leaves_stdout_where_it_found_it(cr, capfd):
    """The redirect is undone, so a later pass in the same process can print.

    Under `--json-summary` stdout carries the summary alone, so the run's own
    logs are pointed at stderr for the duration. As a subprocess the fd table
    died with the child and nothing had to be undone; in one process, `pr fix`
    runs describe after review and `pr status` dumps JSON — both would write
    their stdout to stderr, and a consumer reading the JSON would get nothing.
    """
    with cr._json_summary_stdout(True) as fd:
        assert fd is not None
        print("a log line from inside the run")

    print("the next pass's stdout")
    captured = capfd.readouterr()
    assert "the next pass's stdout" in captured.out
    assert "a log line from inside the run" not in captured.out


def test_json_summary_restores_stdout_when_the_run_raises(cr, capfd):
    with pytest.raises(RuntimeError):
        with cr._json_summary_stdout(True):
            raise RuntimeError("the run failed mid-review")

    print("the next pass's stdout")
    assert "the next pass's stdout" in capfd.readouterr().out


def test_json_summary_stdout_survives_a_second_use_in_the_same_process(cr, capfd):
    """`pr fix` runs review, then describe, in one process; both may redirect.

    Two cycles rather than one, and the descriptor count either side, because
    the two ways this breaks are not both visible in the output. Restoring
    the wrong descriptor shows up as misdirected text, which the single-cycle
    tests above already catch. *Leaking* the saved one does not show up in
    the output at all — every assertion about what landed where still holds,
    and a run only fails once it has exhausted the process's descriptors,
    which is a hang or an OSError somewhere unrelated much later.
    """
    before = _open_fd_count()

    with cr._json_summary_stdout(True):
        print("review pass log line")

    with cr._json_summary_stdout(True):
        print("describe pass log line")

    print("stdout after both passes")
    captured = capfd.readouterr()
    assert "stdout after both passes" in captured.out
    assert "review pass log line" not in captured.out
    assert "describe pass log line" not in captured.out
    assert _open_fd_count() == before, (
        "the saved stdout descriptor was not closed — a pass that redirects "
        "leaks one per invocation, which nothing in the output reveals"
    )


def test_no_json_summary_leaves_stdout_alone(cr, capfd):
    with cr._json_summary_stdout(False) as fd:
        assert fd is None
        print("an ordinary run prints to stdout")
    assert "an ordinary run prints to stdout" in capfd.readouterr().out


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


def test_json_summary_needs_discussion_no_must_fix(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Should fix\n- **[S1]** path:1 — improvement\n"
        "## Nit\n- **[N1]** path:2 — style\n"
    )
    result = json_summary("org/repo", "10", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.NEEDS_DISCUSSION.value
    assert data["findings"]["total"] == 2


def test_json_summary_approve_no_blocking_findings(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Nit\n- **[N1]** path:2 — style\n")
    result = json_summary("org/repo", "10", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.APPROVE.value


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
    """No review file is no verdict — not an approval of a review never written."""
    result = json_summary("org/repo", "42", str(tmp_path / "nonexistent.md"))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["findings"]["total"] == 0
    assert data["verdict"] == ""


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
    """Prose cannot under-report findings that block — the counts win."""
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** path:1 — bug\n\n"
        "## Verdict\nApprove — looks fine.\n"
    )
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


def test_json_summary_verdict_keeps_stronger_prose(cr, tmp_path):
    """Counts cannot discard a stronger call the agent stated."""
    review = tmp_path / "review.md"
    review.write_text(
        "## Nit\n- **[N1]** path:1 — style\n\n"
        "## Verdict\nRequest changes — the approach needs rework.\n"
    )
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value


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
    review_dir = _self_review_dir(tmp_path, "full")

    result = build_review_summary("owner/test-repo", "", str(review_dir / "review.md"))

    assert result.findings["must_fix"] == 1
    assert result.verdict == ""
    assert result.review_type == "full"


def test_an_incremental_self_review_states_no_verdict(cr, tmp_path):
    """The two fields are orthogonal — being incremental does not restore a verdict."""
    review_dir = _self_review_dir(tmp_path, "incremental")

    result = build_review_summary("owner/test-repo", "", str(review_dir / "review.md"))

    assert result.verdict == ""
    assert result.review_type == "incremental"


def test_a_pr_review_still_requests_changes(cr, tmp_path):
    """The same finding under `mode: pr` keeps the verdict it always had."""
    review_dir = _self_review_dir(tmp_path, "full")
    (review_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/test-repo", "head_sha": "abc",
        "review_type": "full", "mode": "pr",
    }))

    result = build_review_summary("owner/test-repo", "1", str(review_dir / "review.md"))

    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED.value


def test_an_unknown_meta_vocabulary_reads_as_absent(cr, tmp_path):
    """meta.json outlives the code that wrote it.

    A member this version does not know reads as unset rather than raising, so
    one unrecognised field does not cost the whole summary.
    """
    review_dir = _self_review_dir(tmp_path, "full")
    (review_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/test-repo", "head_sha": "abc",
        "review_type": "sampled", "mode": "audit",
    }))

    result = build_review_summary("owner/test-repo", "1", str(review_dir / "review.md"))

    assert result.review_type is None
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED.value


def test_json_summary_includes_failure_detail(cr, tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-1"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("# Review\n<!-- head_sha: abc -->\n## Summary\nNo findings.\n")
    pipeline = review_dir / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)"},
    }))
    result = build_review_summary("owner/test-repo", "1", str(review_file))
    assert result.status == ReviewStatus.PARTIAL.value
    assert "1/2 groups failed" in result.failure_detail


# ── _format_findings_line / _format_verdict ───────────────────────────────────


def test_format_findings_line_no_findings(tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove\n")
    report = build_review_summary("org/repo", "1", str(review))
    assert format_findings_line(report) == ""


def test_format_findings_line_nits_only(tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Nit\n"
        "- **[N1]** `file.py:10` — style\n"
        "- **[N2]** `file.py:20` — naming\n"
        "\n## Verdict\nApprove\n"
    )
    report = build_review_summary("org/repo", "1", str(review))
    assert format_findings_line(report) == "2 nit"


def test_format_findings_line_mixed(tmp_path):
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
    report = build_review_summary("org/repo", "1", str(review))
    assert format_findings_line(report) == "1 must fix, 1 should fix, 2 nit"


def test_format_findings_line_nonexistent_file():
    report = build_review_summary("org/repo", "1", "/nonexistent/review.md")
    assert format_findings_line(report) == ""


def test_format_verdict_approve(tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nApprove\n")
    report = build_review_summary("org/repo", "1", str(review))
    assert format_verdict(report) == "Approve"


def test_format_verdict_with_must_fix(tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Must fix\n- **[M1]** `file.py:10` — bug\n\n## Verdict\nApprove\n"
    )
    report = build_review_summary("org/repo", "1", str(review))
    assert format_verdict(report) == "Request changes"


def test_format_verdict_explicit_disapprove(tmp_path):
    review = tmp_path / "review.md"
    review.write_text("## Verdict\nDisapprove\n")
    report = build_review_summary("org/repo", "1", str(review))
    assert format_verdict(report) == "Disapprove"


def test_format_verdict_nonexistent_file():
    report = build_review_summary("org/repo", "1", "/nonexistent/review.md")
    assert format_verdict(report) == ""


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
        "done": ["synthesis"], "failed": {"synthesis": "all groups failed"},
    }))
    result = json_summary("org/repo", "42", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["status"] == ReviewStatus.ERROR.value
