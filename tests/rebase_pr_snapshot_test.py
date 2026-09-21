"""Tests for rebase.pr_snapshot — one read of the PR per rebase."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import latch_graphql  # noqa: E402
from gh import budget as gh_budget
from gh import client as gh_client
from pr.domains import RebaseStatus
from rebase import pr_snapshot as rebase_pr_snapshot
from rebase import refusals
from rebase import target as rebase_target
from rebase.types import RefusalSignal


def _ctx(pr_number=42, branch="isaac/feat/x", repo="acme/widget"):
    return SimpleNamespace(pr_number=pr_number, branch=branch, repo=repo)


def _answers(data):
    return mock.patch.object(gh_client, "pr_view", return_value=data)


class TestFetch:
    def test_it_reads_every_field_the_run_needs_in_one_call(self):
        with _answers({
            "state": "OPEN", "number": 42, "url": "https://gh/42",
            "baseRefName": "release/2", "isDraft": False,
            "reviewDecision": "APPROVED",
        }) as view:
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())

        view.assert_called_once()
        assert set(rebase_pr_snapshot.FIELDS) == set(view.call_args[0][1:])
        assert snapshot.base_ref == "release/2"
        assert snapshot.review_decision == "APPROVED"
        assert snapshot.answered

    def test_it_asks_by_number_when_one_is_resolved(self):
        with _answers({"state": "OPEN"}) as view:
            rebase_pr_snapshot.fetch("/wt", _ctx(pr_number=42))
        assert view.call_args[0][0] == "42"

    def test_it_asks_by_branch_when_no_number_is_resolved(self):
        with _answers({"state": "OPEN"}) as view:
            rebase_pr_snapshot.fetch("/wt", _ctx(pr_number=None))
        assert view.call_args[0][0] == "isaac/feat/x"

    def test_a_tracker_that_cannot_answer_is_not_an_answer(self):
        # gh absent, unauthenticated, rate-limited, or no PR: all the same, and
        # none of them may read as a state that stops a rebase.
        with _answers({}):
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())

        assert not snapshot.answered
        assert not snapshot.merged
        assert not snapshot.open_and_ready

    def test_it_asks_nothing_when_there_is_nothing_to_ask_about(self):
        with _answers({"state": "OPEN"}) as view:
            snapshot = rebase_pr_snapshot.fetch(
                "/wt", _ctx(pr_number=None, branch=""),
            )
        view.assert_not_called()
        assert not snapshot.answered


class TestOpenAndReady:
    """Which PRs a force-push notice is owed to."""

    def test_a_ready_open_pr_is_one_someone_may_be_reading(self):
        assert rebase_pr_snapshot.PRSnapshot(
            state="OPEN", number=1, is_draft=False,
        ).open_and_ready

    def test_a_draft_is_the_author_s_own_workspace(self):
        assert not rebase_pr_snapshot.PRSnapshot(
            state="OPEN", number=1, is_draft=True,
        ).open_and_ready

    def test_a_merged_pr_is_not_awaiting_review(self):
        assert not rebase_pr_snapshot.PRSnapshot(
            state="MERGED", number=1,
        ).open_and_ready

    def test_a_closed_unmerged_pr_is_not_awaiting_review(self):
        assert not rebase_pr_snapshot.PRSnapshot(
            state="CLOSED", number=1,
        ).open_and_ready


class TestItReplacesTheSecondCall:
    """The two reads this collapsed, each still answering the same way."""

    def test_the_base_comes_from_the_snapshot_without_a_second_read(self):
        with mock.patch.object(gh_client, "pr_view") as view:
            base = rebase_target.pr_base_branch(
                "/wt", _ctx(),
                rebase_pr_snapshot.PRSnapshot(state="OPEN", base_ref="main"),
            )
        view.assert_not_called()
        assert base == "main"

    def test_a_merged_pr_still_refuses_from_the_snapshot(self):
        report = refusals.tracker_landed_check(
            "/wt", _ctx(),
            rebase_pr_snapshot.PRSnapshot(
                state="MERGED", number=42, url="https://gh/42",
            ),
        )
        assert report is not None
        assert "42" in report.detail

    def test_an_open_pr_is_no_refusal(self):
        assert refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(state="OPEN", number=42),
        ) is None

    def test_an_unanswered_snapshot_is_no_refusal(self):
        # A machine with no gh, no auth or no network cannot answer this at any
        # point, and refusing on it would mean `pr rebase` never runs there.
        assert refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(),
        ) is None

    def test_the_wording_matches_the_path_that_reads_it_itself(self):
        """Both paths phrase one finding, so neither drifts from the other."""
        from_snapshot = refusals.tracker_landed_check(
            "/wt", _ctx(),
            rebase_pr_snapshot.PRSnapshot(
                state="MERGED", number=42, url="https://gh/42",
            ),
        )
        with _answers({"state": "MERGED", "number": 42, "url": "https://gh/42"}):
            from_its_own_read = refusals.tracker_landed_check("/wt", _ctx())

        assert from_snapshot.detail == from_its_own_read.detail
        assert from_snapshot.signal == from_its_own_read.signal


class TestARefusedRead:
    """The one unanswered read that stops a rebase rather than letting it run.

    Every other silence is a machine that cannot answer the question. This one
    is a machine that did not ask, about a PR that is knowable — and the answer
    it skipped is the only signal that survives a squash merge.
    """

    def test_the_snapshot_records_that_the_call_was_declined(self):
        with latch_graphql(), _answers({}):
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())

        assert snapshot.refused
        assert not snapshot.answered

    def test_an_ordinary_failure_is_not_a_refusal(self):
        """No gh, no auth, no network, no PR — none of them latch."""
        with _answers({}):
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())

        assert not snapshot.refused
        assert not snapshot.answered

    def test_a_successful_read_is_never_marked_refused(self):
        """A latch armed by some earlier call must not taint an answer we got."""
        with latch_graphql(), _answers({"state": "OPEN", "number": 42}):
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())

        assert not snapshot.refused
        assert snapshot.answered

    def test_it_refuses_the_rebase(self):
        report = refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(refused=True),
        )

        assert report is not None
        assert report.signal == RefusalSignal.TRACKER_REFUSED.value
        assert report.status == RebaseStatus.TRACKER_UNREAD.value

    def test_it_does_not_claim_the_branch_landed(self):
        """The status ALREADY_LANDED asserts the work is in the base. Nothing
        here established that — the check is refusing because it could not."""
        report = refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(refused=True),
        )

        assert report.status != RebaseStatus.ALREADY_LANDED.value
        assert "not asked" in report.detail

    def test_the_hint_explains_what_proceeding_would_cost(self):
        report = refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(refused=True),
        )
        hint = refusals.REFUSAL_HINTS[report.status].format(ref="origin/main")

        assert "squash merge" in hint
        assert "force-push" in hint

    def test_an_open_pr_is_not_refused_because_some_other_call_latched(self):
        """The false refusal this design exists to avoid, in its likeliest form.

        The latch is process-wide and armed by whichever call met the quota
        first. Keying the refusal on the latch alone — rather than on this
        read having come back empty — would refuse a rebase whose PR we
        successfully read and found open.
        """
        with latch_graphql(), _answers({"state": "OPEN", "number": 42}):
            assert refusals.tracker_landed_check("/wt", _ctx()) is None

    def test_the_snapshotless_path_refuses_a_read_of_its_own_that_was_declined(
        self,
    ):
        """The path `pr ci --fix` takes, and the hole this closes.

        It has no snapshot, so `by_tracker` draws the distinction from its own
        read instead — not from the latch as seen from `tracker_landed_check`,
        which is what the test above forbids. `pr ci --fix` resolves its PR
        context through GraphQL before reaching here, so it is the caller most
        likely to meet an armed latch, and it force-pushes.
        """
        with latch_graphql(), _answers({}):
            report = refusals.tracker_landed_check("/wt", _ctx())

        assert report is not None
        assert report.status == RebaseStatus.TRACKER_UNREAD.value

    def test_the_snapshotless_path_still_proceeds_when_gh_merely_failed(self):
        """No latch, no refusal — a machine with no gh still rebases.

        The best-effort contract survives for every silence that is not a
        budget refusal, which is the whole population of machines without gh,
        without auth, or offline.
        """
        with _answers({}):
            assert refusals.tracker_landed_check("/wt", _ctx()) is None

    def test_the_remedy_survives_the_latch_expiring_before_the_report_builds(
        self,
    ):
        """The remedy comes from the snapshot, not a fresh read of the latch.

        `fetch` and the refusal it feeds are two separate calls, and the
        latch's own window can pass between them. Re-querying at report time
        would silently lose the reset time to a latch that already expired —
        this pins that the remedy travels with the snapshot instead.
        """
        with latch_graphql(), _answers({}):
            snapshot = rebase_pr_snapshot.fetch("/wt", _ctx())
        assert "refills at" in snapshot.remedy

        # The latch expires before the refusal report is built.
        gh_budget.reset_for_tests()
        assert gh_budget.latched(gh_budget.Resource.GRAPHQL) is None

        report = refusals.tracker_landed_check("/wt", _ctx(), snapshot)

        assert "refills at" in report.detail

    def test_the_generic_hint_is_the_fallback_for_a_snapshot_with_no_remedy(
        self,
    ):
        """A snapshot built with `refused=True` alone, as tests upstream do,
        still gets a usable remedy rather than an empty one."""
        report = refusals.tracker_landed_check(
            "/wt", _ctx(), rebase_pr_snapshot.PRSnapshot(refused=True),
        )

        assert gh_budget.BUDGET_EXHAUSTED_HINT in report.detail


class TestNameTheOpenPR:
    """The notice, which both force-push sites call."""

    def test_it_names_a_ready_pr(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(rebase_pr_snapshot.PRSnapshot(
            state="OPEN", number=1358, url="https://gh/1358",
        ))
        err = capsys.readouterr().err
        assert "https://gh/1358" in err
        assert "ready for review" in err

    def test_it_falls_back_to_the_number_when_there_is_no_url(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(
            rebase_pr_snapshot.PRSnapshot(state="OPEN", number=1358),
        )
        assert "#1358" in capsys.readouterr().err

    def test_it_says_nothing_about_a_draft(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(rebase_pr_snapshot.PRSnapshot(
            state="OPEN", number=1, is_draft=True,
        ))
        assert capsys.readouterr().err == ""

    def test_it_says_nothing_about_a_closed_pr(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(
            rebase_pr_snapshot.PRSnapshot(state="CLOSED", number=1),
        )
        assert capsys.readouterr().err == ""

    def test_it_says_nothing_when_github_could_not_be_asked(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(rebase_pr_snapshot.PRSnapshot())
        assert capsys.readouterr().err == ""

    def test_it_tolerates_having_no_snapshot_at_all(self, capsys):
        rebase_pr_snapshot.name_the_open_pr(None)
        assert capsys.readouterr().err == ""
