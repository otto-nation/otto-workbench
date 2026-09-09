"""Tests for rebase.refusals — the preflight, and what a refusal guarantees."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import landed as branch_landed
from git import client as git_client
from pr.domains import RebaseStatus
from rebase import inspect as rebase_inspect
from rebase import refusals
from rebase import types as rebase_types

_TARGET = "origin/main"


def _ctx(branch="isaac/feat/x"):
    ctx = mock.MagicMock()
    ctx.branch = branch
    return ctx


class TestAsRefusal:
    """`Landed` carries no branch name, so naming it is what this adds."""

    def test_it_names_the_branch_the_evidence_is_about(self):
        landed = branch_landed.Landed(
            signal=branch_landed.LandedSignal.PR_MERGED,
            detail="PR #7 merged", commits_ahead=0, pr_number=7,
        )
        report = refusals.as_refusal(landed, "isaac/feat/x")
        assert report.branch == "isaac/feat/x"
        assert report.signal == branch_landed.LandedSignal.PR_MERGED.value
        assert report.detail == "PR #7 merged"
        assert report.pr_number == 7

    def test_no_evidence_is_no_refusal(self):
        assert refusals.as_refusal(None, "isaac/feat/x") is None


class TestUnrelatedHistoryCheck:
    """Exact rather than heuristic — git either finds a merge base or it does not."""

    def test_a_shared_history_is_not_refused(self):
        with mock.patch.object(rebase_inspect, "shares_history", return_value=True):
            assert refusals.unrelated_history_check(
                "/fake", _ctx(), target_ref=_TARGET) is None

    def test_no_merge_base_is_refused_against_the_resolved_ref(self):
        with mock.patch.object(rebase_inspect, "shares_history", return_value=False):
            report = refusals.unrelated_history_check(
                "/fake", _ctx(), target_ref="origin/release/1.2")

        assert report.status == RebaseStatus.UNRELATED_HISTORY.value
        assert report.signal == rebase_types.RefusalSignal.NO_MERGE_BASE.value
        assert "origin/release/1.2" in report.detail


class TestRefuse:
    """Every refusal exits on the shared code and records what it saw."""

    @staticmethod
    def _report(status):
        return rebase_types.RefusalReport(
            branch="isaac/feat/x", signal="merged_pr", detail="PR #7 merged",
            status=status,
        )

    @pytest.mark.parametrize("status", [
        RebaseStatus.ALREADY_LANDED.value,
        RebaseStatus.UNRELATED_HISTORY.value,
        RebaseStatus.CONFLICTS_OVER_BUDGET.value,
    ])
    def test_every_status_has_a_hint_naming_the_resolved_ref(self, status, capsys):
        """A hint keyed by status cannot be paired with another refusal's text."""
        ctx = _ctx()
        with mock.patch.object(rebase_types.RebaseOutcome, "save"):
            rc = refusals.refuse(
                ctx, self._report(status), target_ref="origin/release/1.2")

        assert rc == refusals.REFUSAL_EXIT
        assert "origin/release/1.2" in capsys.readouterr().err

    def test_it_records_the_refusal_against_the_resolved_ref(self):
        """The recorded base is this run's ref, not whatever the repo calls trunk."""
        ctx = _ctx()
        recorded = []
        with mock.patch.object(
            rebase_types.RebaseOutcome, "save", autospec=True,
            side_effect=lambda self, c: recorded.append((self, c)),
        ):
            refusals.refuse(
                ctx, self._report(RebaseStatus.ALREADY_LANDED.value),
                target_ref="origin/release/1.2")

        (outcome, saved_ctx), = recorded
        assert saved_ctx is ctx
        assert outcome.target_base == "origin/release/1.2"
        assert outcome.status is RebaseStatus.ALREADY_LANDED


class TestRefuseOverBudget:
    """The only refusal raised mid-rebase — so it has a worktree to restore."""

    def test_it_aborts_before_refusing(self):
        commands = []

        def fake_run(*args, **kwargs):
            commands.append(args)
            return mock.Mock(ok=True, returncode=0, stdout="", stderr="")

        with mock.patch.object(git_client, "run", side_effect=fake_run), \
             mock.patch.object(rebase_types.RebaseOutcome, "save"):
            rc = refusals.refuse_over_budget(
                "/fake", _ctx(), 40, target_ref=_TARGET)

        assert rc == refusals.REFUSAL_EXIT
        assert ("rebase", "--abort") in commands

    def test_the_detail_names_the_spread_and_the_budget(self, capsys):
        with mock.patch.object(git_client, "run",
                               return_value=mock.Mock(ok=True, returncode=0)), \
             mock.patch.object(rebase_types.RebaseOutcome, "save"):
            refusals.refuse_over_budget("/fake", _ctx(), 40, target_ref=_TARGET)

        err = capsys.readouterr().err
        assert "40" in err and str(refusals.CONFLICT_FILE_BUDGET) in err
