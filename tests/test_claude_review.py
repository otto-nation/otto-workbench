"""Tests for claude-review Python script — helper functions, archive, GC, summary."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ai" / "claude" / "bin" / "claude-review"
LIB_DIR = str(REPO_ROOT / "ai" / "claude" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from review_common import count_severity, json_summary, review_file_path
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
):
    Path(path).write_text(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}\n'
        f'{{"type":"result","subtype":"success","is_error":false,'
        f'"duration_ms":{duration_ms},"total_cost_usd":{cost},'
        f'"usage":{{"input_tokens":{input_tokens},"output_tokens":{output_tokens},'
        f'"cache_read_input_tokens":{cache_read},"cache_creation_input_tokens":{cache_create}}}}}\n'
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


def test_review_file_basic(cr, reviews_dir, monkeypatch):
    import review_common
    monkeypatch.setattr(review_common, "REVIEWS_DIR", reviews_dir)
    result = review_file_path("org/my-repo", "42")
    assert result == reviews_dir / "my-repo-42" / "review.md"


def test_review_file_repo_with_hyphens(cr, reviews_dir, monkeypatch):
    import review_common
    monkeypatch.setattr(review_common, "REVIEWS_DIR", reviews_dir)
    result = review_file_path("org/my-cool-repo", "1")
    assert result == reviews_dir / "my-cool-repo-1" / "review.md"


def test_review_file_deep_nested_repo(cr, reviews_dir, monkeypatch):
    import review_common
    monkeypatch.setattr(review_common, "REVIEWS_DIR", reviews_dir)
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
    assert "M tokens" in result


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


def test_format_usage_includes_cache_tokens(cr, tmp_path):
    log = str(tmp_path / "cache.jsonl")
    _make_session_log(
        log, cost=1.00, input_tokens=100, output_tokens=200,
        duration_ms=10000, cache_read=5000, cache_create=3000,
    )
    result = cr._format_usage(log)
    assert "8k tokens" in result


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
    assert data["verdict"] == "changes_requested"


def test_json_summary_approve_no_must_fix(cr, tmp_path):
    review = tmp_path / "review.md"
    review.write_text(
        "## Should fix\n- **[S1]** path:1 — improvement\n"
        "## Nit\n- **[N1]** path:2 — style\n"
    )
    result = json_summary("org/repo", "10", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["verdict"] == "approve"
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
    assert data["verdict"] == "approve"


def test_json_summary_self_review_no_pr(cr, tmp_path):
    review = tmp_path / "self-review.md"
    review.write_text("## Must fix\n- **[M1]** path:1 — bug\n")
    result = json_summary("org/repo", "", str(review))
    data = json.loads(result.removeprefix("REVIEW_SUMMARY:"))
    assert data["pr_number"] is None
    assert data["verdict"] == "changes_requested"


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


def test_confirm_eof_defaults_yes(cr, monkeypatch):
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    assert cr._confirm("Continue?") is True


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


# ── gc_dir_is_all_stale ──────────────────────────────────────────────────────


def test_gc_dir_all_stale(cr, tmp_path):
    d = tmp_path / "stale-dir"
    d.mkdir()
    f = d / "old.jsonl"
    f.write_text("{}")
    os.utime(str(f), (1622505600, 1622505600))
    assert review_gc.gc_dir_is_all_stale(d) is True


def test_gc_dir_has_recent_files(cr, tmp_path):
    d = tmp_path / "mixed-dir"
    d.mkdir()
    old = d / "old.jsonl"
    old.write_text("{}")
    os.utime(str(old), (1622505600, 1622505600))
    (d / "new.jsonl").write_text("{}")
    assert review_gc.gc_dir_is_all_stale(d) is False


def test_gc_dir_empty(cr, tmp_path):
    d = tmp_path / "empty-dir"
    d.mkdir()
    assert review_gc.gc_dir_is_all_stale(d) is False


# ── gc_clean_intermediates ───────────────────────────────────────────────────


def test_gc_clean_intermediates_removes_stale(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "group-1.jsonl", "synthesis.jsonl"):
        f = d / name
        f.write_text("{}")
        os.utime(str(f), (1622505600, 1622505600))
    (d / "meta.json").write_text("{}")

    count = review_gc.gc_clean_intermediates(d)
    assert count == 3
    assert not (d / "group-1.md").exists()
    assert (d / "meta.json").exists()


def test_gc_clean_intermediates_preserves_recent(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "holistic.jsonl"):
        (d / name).write_text("{}")

    count = review_gc.gc_clean_intermediates(d)
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
    assert cr.DEFAULT_MAX_PARALLEL == 4
    assert review_gc.GC_STALE_DAYS == 7
    assert review_gc.PRUNE_MAX_FILES == 10
    assert len(cr.SEVERITY_PREFIXES) == 4
    assert len(cr.SEVERITY_JSON_KEYS) == 4


