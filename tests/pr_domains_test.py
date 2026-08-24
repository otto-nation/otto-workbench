"""Tests for what each domain says about itself — render_status and readiness."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_domains
import pr_state
from proc import CmdResult

# When the run being described happened. Any non-empty stamp means "written",
# which is the only thing render_status and readiness read it for.
_REBASE_RUN = "2026-06-20T00:00:00Z"


# ── The protocol ──────────────────────────────────────────────────────────


def test_a_domain_that_declares_nothing_says_nothing():
    """The default is silence, so a domain opts in to both by overriding."""
    d = pr_domains.Domain(updated_at="t")
    assert d.render_status() == []
    assert d.readiness() == pr_domains.Readiness()


@pytest.mark.parametrize("name,cls", sorted(pr_state._domains().items()))
def test_an_unwritten_domain_blocks_nothing(name, cls):
    """A state nobody has written yet reports "not checked", never a blocker.

    Every domain in the registry answers both questions, and an unstamped one
    answers them the same way: it cannot have measured anything wrong, so the
    only thing it may contribute is an unchecked entry.
    """
    answer = cls().readiness()
    assert answer.blockers == ()
    cls().render_status()


# ── CIDomain ──────────────────────────────────────────────────────────────


def test_ci_render_not_checked():
    assert pr_domains.CIDomain().render_status() == ["**CI**: not checked yet"]


def test_ci_render_success():
    lines = pr_domains.CIDomain(
        conclusion="success", failure_count=0, updated_at="t",
    ).render_status()
    assert "green" in lines[0]
    assert "success" in lines[0]


def test_ci_render_failure_with_kinds():
    lines = pr_domains.CIDomain(
        conclusion="failure", failure_count=3,
        failure_kinds={"test": 2, "lint": 1}, updated_at="t",
    ).render_status()
    assert "red" in lines[0]
    assert "3 failure(s)" in lines[0]
    assert any("lint: 1" in l for l in lines)
    assert any("test: 2" in l for l in lines)


def test_ci_render_with_run_number():
    lines = pr_domains.CIDomain(
        conclusion="failure", failure_count=1, last_run_number=42, updated_at="t",
    ).render_status()
    assert any("run #42" in l for l in lines)


def test_ci_readiness_unchecked():
    assert pr_domains.CIDomain().readiness().unchecked == ("CI",)


def test_ci_readiness_failing():
    ci = pr_domains.CIDomain(conclusion="failure", updated_at="t")
    assert ci.readiness().blockers == ("CI failing",)


def test_ci_readiness_green():
    ci = pr_domains.CIDomain(conclusion="success", updated_at="t")
    assert ci.readiness() == pr_domains.Readiness()


def test_ci_readiness_treats_a_non_success_conclusion_as_failing():
    """Cancelled and timed_out are not success, and neither may merge."""
    ci = pr_domains.CIDomain(conclusion="cancelled", updated_at="t")
    assert ci.readiness().blockers == ("CI failing",)


# ── ReviewSummary ─────────────────────────────────────────────────────────


def test_review_render_not_run():
    assert pr_domains.ReviewSummary().render_status() == ["**Review**: not run yet"]


def test_review_render_error():
    lines = pr_domains.ReviewSummary(
        review_type="full", verdict=pr_domains.ReviewVerdict.APPROVE.value,
        status=pr_domains.ReviewStatus.ERROR.value, updated_at="t",
    ).render_status()
    assert "[ERROR]" in lines[0]


def test_review_render_completed():
    lines = pr_domains.ReviewSummary(
        review_type="full", verdict=pr_domains.ReviewVerdict.APPROVE.value,
        status=pr_domains.ReviewStatus.COMPLETED.value, updated_at="t",
    ).render_status()
    assert "[ERROR]" not in lines[0]


def test_review_render_empty_status():
    lines = pr_domains.ReviewSummary(
        review_type="full", verdict=pr_domains.ReviewVerdict.APPROVE.value, updated_at="t",
    ).render_status()
    assert "[ERROR]" not in lines[0]


def test_review_render_disapprove():
    lines = pr_domains.ReviewSummary(
        review_type="full", verdict=pr_domains.ReviewVerdict.DISAPPROVE.value,
        updated_at="t",
    ).render_status()
    assert "[DISAPPROVED]" in lines[0]


def test_review_render_disapprove_and_error():
    lines = pr_domains.ReviewSummary(
        review_type="full", verdict=pr_domains.ReviewVerdict.DISAPPROVE.value,
        status=pr_domains.ReviewStatus.ERROR.value, updated_at="t",
    ).render_status()
    assert "[ERROR]" in lines[0]
    assert "[DISAPPROVED]" in lines[0]


def test_review_render_with_findings():
    lines = pr_domains.ReviewSummary(
        review_type="pr", finding_counts={"M": 2, "S": 1},
        verdict=pr_domains.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ).render_status()
    assert any("findings:" in l for l in lines)
    assert any("M: 2" in l for l in lines)


def test_review_render_with_cost():
    lines = pr_domains.ReviewSummary(
        review_type="pr", cost_usd=1.23, updated_at="t",
    ).render_status()
    assert any("$1.23" in l for l in lines)


def test_review_render_partial_with_failure_detail():
    lines = pr_domains.ReviewSummary(
        review_type="pr", verdict=pr_domains.ReviewVerdict.CHANGES_REQUESTED.value,
        status=pr_domains.ReviewStatus.PARTIAL.value,
        failure_detail="2/8 groups failed: quota exhausted (429), agent hit max turns (5)",
        finding_counts={"M": 3, "S": 2}, cost_usd=4.50, updated_at="t",
    ).render_status()
    assert any("PARTIAL" in line for line in lines)
    assert any("2/8 groups failed" in line for line in lines)
    assert any("recover" in line for line in lines)


def test_review_render_error_with_failure_detail():
    lines = pr_domains.ReviewSummary(
        review_type="pr", status=pr_domains.ReviewStatus.ERROR.value,
        failure_detail="all groups failed: quota exhausted (429)",
        cost_usd=2.10, updated_at="t",
    ).render_status()
    assert any("ERROR" in line for line in lines)
    assert any("all groups failed" in line for line in lines)
    assert any("recover" in line for line in lines)


def test_review_render_complete_no_recover_hint():
    lines = pr_domains.ReviewSummary(
        review_type="pr", verdict=pr_domains.ReviewVerdict.APPROVE.value,
        status=pr_domains.ReviewStatus.COMPLETED.value,
        finding_counts={}, cost_usd=3.00, updated_at="t",
    ).render_status()
    assert not any("recover" in line for line in lines)
    assert not any("PARTIAL" in line or "ERROR" in line for line in lines)


def test_review_readiness_unchecked():
    assert pr_domains.ReviewSummary().readiness().unchecked == ("review",)


def test_review_readiness_must_fix_findings():
    rev = pr_domains.ReviewSummary(finding_counts={"M": 2, "S": 1}, updated_at="t")
    assert rev.readiness().blockers == ("must-fix findings",)


def test_review_readiness_ignores_non_blocking_findings():
    rev = pr_domains.ReviewSummary(finding_counts={"S": 3, "N": 1}, updated_at="t")
    assert rev.readiness() == pr_domains.Readiness()


@pytest.mark.parametrize("status", [
    pr_domains.ReviewStatus.PARTIAL.value, pr_domains.ReviewStatus.ERROR.value,
])
def test_review_readiness_incomplete_run(status):
    """A run that did not finish has not cleared the PR, whatever it found."""
    rev = pr_domains.ReviewSummary(status=status, updated_at="t")
    assert rev.readiness().blockers == ("review incomplete",)


def test_review_readiness_reports_findings_and_incompleteness_together():
    rev = pr_domains.ReviewSummary(
        finding_counts={"M": 1}, status=pr_domains.ReviewStatus.PARTIAL.value,
        updated_at="t",
    )
    assert rev.readiness().blockers == ("must-fix findings", "review incomplete")


# ── CommentsSummary ───────────────────────────────────────────────────────


def test_comments_render_not_checked():
    assert pr_domains.CommentsSummary().render_status() == ["**Comments**: not checked yet"]


def test_comments_render_with_threads():
    lines = pr_domains.CommentsSummary(
        total_threads=5, by_state={"new": 2, "resolved": 3}, updated_at="t",
    ).render_status()
    assert "5 thread(s)" in lines[0]
    assert any("new: 2" in l for l in lines)
    assert any("resolved: 3" in l for l in lines)


def test_comments_render_with_blocking_reviewers():
    lines = pr_domains.CommentsSummary(
        total_threads=1, blocking_reviewers=["alice", "bob"], updated_at="t",
    ).render_status()
    assert any("blocking: alice, bob" in l for l in lines)


def test_comments_readiness_unchecked():
    assert pr_domains.CommentsSummary().readiness().unchecked == ("comments",)


def test_comments_readiness_blocking_reviewers():
    c = pr_domains.CommentsSummary(blocking_reviewers=["alice"], updated_at="t")
    assert c.readiness().blockers == ("blocking reviewers",)


def test_comments_readiness_clean():
    c = pr_domains.CommentsSummary(total_threads=3, updated_at="t")
    assert c.readiness() == pr_domains.Readiness()


# ── TriageSummary ─────────────────────────────────────────────────────────


def test_triage_render_not_run():
    assert pr_domains.TriageSummary().render_status() == ["**Triage**: not run yet"]


def test_triage_render_with_data():
    result = pr_domains.TriageSummary(
        total=5, actionable=2, valid=1, questions=1, updated_at="2024-01-01T00:00:00Z",
    ).render_status()
    assert len(result) == 1
    assert "5 threads" in result[0]
    assert "2 actionable" in result[0]
    assert "1 valid" in result[0]
    assert "1 questions" in result[0]


# ── RebaseSummary ─────────────────────────────────────────────────────────


def test_rebase_render_not_run():
    assert pr_domains.RebaseSummary().render_status() == ["**Rebase**: not run yet"]


def test_rebase_render_completed_with_conflicts():
    result = pr_domains.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at=_REBASE_RUN,
    ).render_status()
    assert len(result) >= 1
    assert "2 file(s)" in result[0]
    assert "3 commit(s)" in result[0]
    assert "force-pushed" in result[0]


def test_rebase_render_clean():
    result = pr_domains.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=5,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at=_REBASE_RUN,
    ).render_status()
    assert len(result) >= 1
    assert "clean" in result[0].lower()


def test_rebase_render_not_pushed():
    result = pr_domains.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["a.py"],
        force_pushed=False, updated_at=_REBASE_RUN,
    ).render_status()
    assert "force-pushed" not in result[0]


def test_rebase_render_conflicts():
    result = pr_domains.RebaseSummary(
        status="conflicts", updated_at=_REBASE_RUN,
    ).render_status()
    assert len(result) == 1
    assert "conflicts" in result[0].lower()
    assert "pr rebase --fix" in result[0]


def test_rebase_render_stale_files():
    result = pr_domains.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["pnpm-lock.yaml"],
        files_stale=["pnpm-lock.yaml"],
        force_pushed=True, updated_at=_REBASE_RUN,
    ).render_status()
    assert len(result) == 2
    assert "regeneration failed" in result[1]
    assert "pnpm-lock.yaml" in result[1]


def test_rebase_render_no_stale_line_when_clean():
    r = pr_domains.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=1,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at=_REBASE_RUN,
    )
    assert len(r.render_status()) == 1


def test_rebase_render_aborted():
    result = pr_domains.RebaseSummary(
        status="aborted", updated_at=_REBASE_RUN,
    ).render_status()
    assert result == ["**Rebase**: aborted"]


def test_rebase_render_already_landed():
    """A refusal is not a completed rebase — the dashboard has to say which."""
    result = pr_domains.RebaseSummary(
        status=pr_domains.RebaseStatus.ALREADY_LANDED.value,
        updated_at=_REBASE_RUN,
    ).render_status()
    assert len(result) == 1
    assert "already landed" in result[0]
    assert "pr rebase --force" in result[0]


def test_rebase_render_unrelated_history():
    r = pr_domains.RebaseSummary(
        status=pr_domains.RebaseStatus.UNRELATED_HISTORY.value,
        updated_at=_REBASE_RUN,
    )
    assert "shares no history" in r.render_status()[0]


def test_rebase_render_conflicts_over_budget():
    r = pr_domains.RebaseSummary(
        status=pr_domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value,
        updated_at=_REBASE_RUN,
    )
    assert "too many conflicts" in r.render_status()[0]


def test_every_refusal_status_renders_as_a_refusal():
    """A refusal status the table forgets renders as a completed rebase.

    `render_status` falls through to the success branch for any status it does
    not name, so a new refusal added to `pr rebase` without a row here would
    report a clean rebase that never ran.
    """
    refusals = {
        pr_domains.RebaseStatus.ALREADY_LANDED,
        pr_domains.RebaseStatus.UNRELATED_HISTORY,
        pr_domains.RebaseStatus.CONFLICTS_OVER_BUDGET,
    }
    for status in refusals:
        r = pr_domains.RebaseSummary(
            status=status.value, updated_at=_REBASE_RUN,
        )
        assert "refused" in r.render_status()[0], status


# ── PushDomain ────────────────────────────────────────────────────────────


@patch("pr_domains.git_client.run")
def test_push_observed_up_to_date(mock_run):
    mock_run.return_value = CmdResult(0, "0\n")
    assert pr_domains.PushDomain.observed(Path("/repo"), "main", updated_at="t").ahead == 0


@patch("pr_domains.git_client.run")
def test_push_observed_counts_ahead_commits(mock_run):
    """Range direction matters — an inverted range counts the wrong side."""
    mock_run.return_value = CmdResult(0, "3\n")
    push = pr_domains.PushDomain.observed(Path("/repo"), "feat/branch", updated_at="t")
    assert push.ahead == 3
    assert mock_run.call_args.args == (
        "rev-list", "--count", "origin/feat/branch..HEAD",
    )
    assert mock_run.call_args.kwargs["cwd"] == Path("/repo")


@patch("pr_domains.git_client.run")
def test_push_observed_nonzero_returncode_is_unpushed(mock_run):
    """Branch never pushed — git rev-list exits non-zero."""
    mock_run.return_value = CmdResult(128, "", "fatal: unknown revision\n")
    push = pr_domains.PushDomain.observed(Path("/repo"), "untracked", updated_at="t")
    assert push.ahead is None


@patch("pr_domains.git_client.run")
def test_push_observed_non_digit_output_is_unpushed(mock_run):
    mock_run.return_value = CmdResult(0, "not-a-number\n")
    assert pr_domains.PushDomain.observed(Path("/repo"), "main", updated_at="t").ahead is None


@patch("pr_domains.git_client.run")
def test_push_observed_stamps_the_write_it_was_given(mock_run):
    mock_run.return_value = CmdResult(0, "0\n")
    push = pr_domains.PushDomain.observed(Path("/repo"), "main", updated_at="2026-08-01T00:00:00Z")
    assert push.updated_at == "2026-08-01T00:00:00Z"


@pytest.mark.parametrize("ahead,expected", [
    (None, "**Push**: branch not pushed to remote"),
    (0, "**Push**: up to date"),
    (4, "**Push**: 4 commit(s) not pushed"),
])
def test_push_renders_each_state(ahead, expected):
    assert pr_domains.PushDomain(ahead=ahead, updated_at="t").render_status() == [expected]


def test_push_says_nothing_until_it_is_observed():
    """An unobserved push domain is silent, not "never pushed"."""
    assert pr_domains.PushDomain().render_status() == []


def test_push_readiness_up_to_date():
    assert pr_domains.PushDomain(ahead=0, updated_at="t").readiness() == pr_domains.Readiness()


def test_push_readiness_counts_unpushed_commits():
    push = pr_domains.PushDomain(ahead=2, updated_at="t")
    assert push.readiness().blockers == ("2 unpushed commit(s)",)


def test_push_readiness_branch_never_pushed():
    push = pr_domains.PushDomain(ahead=None, updated_at="t")
    assert push.readiness().blockers == ("branch not pushed",)


def test_push_readiness_unobserved_blocks_nothing():
    assert pr_domains.PushDomain().readiness() == pr_domains.Readiness()

