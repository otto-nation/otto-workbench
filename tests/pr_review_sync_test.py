"""Tests for the review domain's single writer and the typed summary report."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx
from agent.registry import PHASES
import cli.review_modes
from core.phases import Phase
import pr.state
from pr.domains import ReviewStatus, ReviewSummary, ReviewVerdict
from pr.review_sync import sync_review_domain
import review.completion
from review.paths import FILENAME_RECORD, ReviewMeta, write_review_meta
from review.summary import ReviewSummaryReport, build_review_summary, json_summary
from review_record_support import finding, record, store_record


def test_sync_review_domain_is_the_writer(tmp_path):
    ctx = make_ctx(
        repo="acme/widget", branch="feat/login", pr_number=7,
        worktree_root=tmp_path / "wt", head_sha="abc",
        target_dir=tmp_path / "pr" / "feat",
    )
    report = ReviewSummaryReport(
        review_file="/r.md",
        review_type="full",
        head_sha="abc",
        reviewed_through="def",
        workable=3,
        findings={
            "must_fix": 1, "should_fix": 0, "nit": 2, "idiom": 0, "total": 3,
        },
        verdict=ReviewVerdict.CHANGES_REQUESTED.value,
        status=ReviewStatus.COMPLETED.value,
        recoverable=False,
        cost_usd=1.5,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
        cache_write_tokens=1,
    )

    written = sync_review_domain(ctx, report)

    assert written is not None
    assert written.finding_counts == {"must_fix": 1, "nit": 2}
    assert written.total_tokens == 36
    loaded = pr.state.load_state(ctx.target_dir)
    assert loaded.review.finding_counts == {"must_fix": 1, "nit": 2}
    assert loaded.review.verdict == ReviewVerdict.CHANGES_REQUESTED.value
    assert loaded.review.cost_usd == pytest.approx(1.5)
    assert loaded.review.recoverable is False
    assert written.reviewed_through == "def"
    assert loaded.review.reviewed_through == "def"
    assert written.workable == 3
    assert loaded.review.workable == 3
    assert loaded.identity.pr_number == 7
    assert loaded.identity.head_sha == "abc"


def test_sync_review_domain_stores_reviewed_through_as_is(tmp_path):
    """The head_sha fallback lives on verdict_sha, not on the write."""
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    written = sync_review_domain(
        ctx, ReviewSummaryReport(head_sha="abc", reviewed_through=""),
    )
    assert written is not None
    assert written.reviewed_through == ""
    assert written.head_sha == "abc"
    assert written.verdict_sha() == "abc"


def test_sync_review_domain_keeps_prior_type_when_the_report_omits_it(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport(review_type="incremental"))
    sync_review_domain(ctx, ReviewSummaryReport(review_file="later.md"))

    assert pr.state.load_state(ctx.target_dir).review.review_type == "incremental"


def test_sync_review_domain_defaults_missing_type_to_full(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport())

    assert pr.state.load_state(ctx.target_dir).review.review_type == "full"


def test_sync_review_domain_records_unknown_recoverable(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport(review_file="r.md"))

    assert pr.state.load_state(ctx.target_dir).review.recoverable is None


def test_sync_review_domain_lands_with_the_pr_not_the_caller(tmp_path):
    caller = tmp_path / "repo-root"
    caller.mkdir()
    target = tmp_path / "pr" / "widget-feat-login"
    ctx = make_ctx(
        repo="acme/widget", branch="feat/login", pr_number=2973,
        worktree_root=caller, head_sha="pr-sha",
        target_dir=target,
    )

    sync_review_domain(ctx, ReviewSummaryReport(
        review_file="r.md", verdict="approve", head_sha="pr-sha",
    ))

    assert (target / pr.state.STATE_FILE).is_file()
    assert not list(caller.rglob(pr.state.STATE_FILE))
    written = pr.state.load_state(target)
    assert written.identity.pr_number == 2973
    assert written.identity.worktree_root == str(caller)


def test_review_summary_report_to_json_matches_the_legacy_dict(tmp_path):
    """to_json() is the dict json_summary used to build inline.

    Key order and values must stay byte-identical to the previous return so
    REVIEW_SUMMARY consumers and the golden json_summary tests keep passing.
    """
    review = store_record(
        tmp_path / "review.md", record(finding("N1", "path:1 — style", path="")),
    )

    report = build_review_summary("org/repo", "42", str(review))
    expected = {
        "repo": "org/repo",
        "pr_number": 42,
        "head_sha": None,
        "head_ref": None,
        "base_ref": None,
        "review_type": None,
        "review_file": str(review),
        "review_content": review.read_text(),
        "findings": {
            "must_fix": 0, "should_fix": 0, "nit": 1, "idiom": 0, "total": 1,
        },
        "verdict": ReviewVerdict.APPROVE.value,
        "status": ReviewStatus.COMPLETED.value,
        "failure_detail": "",
        "recoverable": False,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "duration_ms": 0,
    }
    assert report.to_json() == expected
    assert list(report.to_json()) == list(expected)
    assert json.dumps(report.to_json()) == json.dumps(expected)
    assert json_summary("org/repo", "42", str(review)) == (
        "REVIEW_SUMMARY:" + json.dumps(expected)
    )


def _stale_review_md(tmp_path: Path) -> Path:
    review = tmp_path / "review.md"
    review.write_text("## Nit\n- **[N1]** path:1 — style\n")
    (tmp_path / "meta.json").write_text(json.dumps({"head_sha": "abc123def456"}))
    return review


def _assert_review_unchecked(target_dir: Path) -> None:
    state = pr.state.load_state(target_dir)
    domain = state.review if state is not None else ReviewSummary()
    assert not domain.updated_at
    assert domain.status != ReviewStatus.COMPLETED.value
    assert domain.readiness(state).unchecked == ("review",)


def test_record_domain_on_a_stale_dir_leaves_review_unchecked(tmp_path):
    """A review.md + meta.json without review.json is not a completed snapshot.

    Syncing the empty completed report `build_review_summary` used to return
    stamped updated_at, and `pr status` then showed a present, current, clean
    review — the same as a directory with no review.md must not.
    """
    review_file = _stale_review_md(tmp_path)
    assert build_review_summary("org/repo", "42", str(review_file)) is None

    ctx = make_ctx(
        repo="org/repo", branch="feat/x", pr_number=42,
        worktree_root=tmp_path / "wt", head_sha="abc123def456",
        target_dir=tmp_path / "pr" / "feat",
    )
    review.completion.record_domain(ctx, review_file, trail=MagicMock())
    _assert_review_unchecked(ctx.target_dir)


def test_repair_on_a_stale_dir_leaves_review_unchecked(tmp_path, capsys):
    review = _stale_review_md(tmp_path)
    ctx = make_ctx(
        repo="org/repo", branch="feat/x", pr_number=42,
        worktree_root=tmp_path / "wt", head_sha="abc123def456",
        target_dir=tmp_path / "pr" / "feat",
    )
    with patch("cli.review_modes.find_review_file", return_value=review):
        rc = cli.review_modes.repair([], ctx, bin_dir=tmp_path)
    assert rc != 0
    err = capsys.readouterr().err
    assert "No group finding files" in err
    assert f"{review} predates review.json" in err
    assert "No review file found" not in err
    _assert_review_unchecked(ctx.target_dir)
    assert not (tmp_path / FILENAME_RECORD).exists()


def test_repair_with_no_review_file_says_none_was_found(tmp_path, capsys):
    ctx = make_ctx(
        repo="org/repo", branch="feat/x", pr_number=42,
        worktree_root=tmp_path / "wt", head_sha="abc123def456",
        target_dir=tmp_path / "pr" / "feat",
    )
    with patch("cli.review_modes.find_review_file", return_value=None), \
         patch("cli.review_modes.review_file_path",
               return_value=tmp_path / "absent" / "review.md"):
        rc = cli.review_modes.repair([], ctx, bin_dir=tmp_path)
    assert rc == 1
    err = capsys.readouterr().err
    assert "No review file found, trying rebuild..." in err
    assert "predates review.json" not in err


def test_repair_on_a_stale_dir_with_group_files_rebuilds_and_syncs(tmp_path):
    review = _stale_review_md(tmp_path)
    write_review_meta(tmp_path, ReviewMeta(
        repo="org/repo", pr_number=42, head_sha="abc123def456",
    ))
    group = tmp_path / PHASES[Phase.GROUP].output_filename.format(1)
    group.write_text(json.dumps({
        "findings": [{"id": "N1", "path": "a.py", "line": 1, "body": "x"}],
    }))
    ctx = make_ctx(
        repo="org/repo", branch="feat/x", pr_number=42,
        worktree_root=tmp_path / "wt", head_sha="abc123def456",
        target_dir=tmp_path / "pr" / "feat",
    )
    with patch("cli.review_modes.find_review_file", return_value=review):
        rc = cli.review_modes.repair([], ctx, bin_dir=tmp_path)
    assert rc == 0
    assert (tmp_path / FILENAME_RECORD).is_file()
    state = pr.state.load_state(ctx.target_dir)
    assert state is not None
    assert state.review.status == ReviewStatus.COMPLETED.value
    assert state.review.updated_at
    assert state.review.readiness(state).unchecked == ()


def test_record_domain_syncs_a_stored_review(tmp_path):
    review_file = store_record(
        tmp_path / "review.md", record(finding("N1", "path:1 — style", path="")),
    )
    ctx = make_ctx(
        repo="org/repo", branch="feat/x", pr_number=42,
        worktree_root=tmp_path / "wt", head_sha="abc123def456",
        target_dir=tmp_path / "pr" / "feat",
    )
    review.completion.record_domain(ctx, review_file, trail=MagicMock())
    state = pr.state.load_state(ctx.target_dir)
    assert state is not None
    assert state.review.status == ReviewStatus.COMPLETED.value
    assert state.review.updated_at
    assert state.review.readiness(state).unchecked == ()
