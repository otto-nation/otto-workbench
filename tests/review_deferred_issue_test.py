"""Tests for `review.deferred_issue` — the paths its move out of the binary exposed.

The cluster arrived with 50 tests already, all driving it through
`test_review_threads.py`, and those stay where they are. What is here is what
those 50 never reached, found by tracing the module rather than the surface:

- `update_deferred_issue` had **no** test of any kind, including the branch
  that deliberately reports FILED on a failed update
- the whole `existing_issue_id` path through `create_or_update_deferred_issue`
  had never run — every prior test passes `existing_issue_id=""`, so the
  `url or existing_issue_url` fallback had never been evaluated
- `_no_team_key` was reached only transitively, and neither of its two trail
  records was asserted
- `validate_track` was skipped entirely on an empty snapshot, which is #1319
- the tracking issue's link reaches the summary comment only because
  `finalize_deferred` runs first and writes it into the same in-memory state —
  an ordering nothing tested and nothing on the writer's side documented

The last two are why a move-only commit would not have been safe. Neither is
visible from the surface the existing tests drive; both became reachable
contract questions the moment these functions were public.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT, make_ctx

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from config import workbench_config  # noqa: E402
from core import markdown  # noqa: E402
from pr import summary_publish  # noqa: E402
from pr import thread_replies  # noqa: E402
from pr.comments_fix import FixSummary  # noqa: E402
from pr.fix import FixOutcome, FixRecord, ItemOutcome  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402
from pr.thread_models import CommentItem, PRReport, ReportThread  # noqa: E402
from review import deferred_issue  # noqa: E402
from review import issue as review_issue  # noqa: E402
from review.issue import CreatedIssue, IssueDelivery, IssueResult  # noqa: E402


def _identity(worktree="/wt") -> PRIdentity:
    return PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                      head_sha="abc1234", worktree_root=str(worktree))


def _state(*ids, issue_id="", issue_url="", worktree="/wt") -> PRState:
    """A state whose fix pass deferred every id given."""
    return PRState(
        identity=_identity(worktree),
        fix=FixSummary(
            fix=FixRecord(items=[
                ItemOutcome(id=i, summary=f"s{i}", file="a.py", line=1,
                            outcome=FixOutcome.DEFERRED, reason="agent could not auto-fix")
                for i in ids
            ]),
            deferred_issue_id=issue_id,
            deferred_issue_url=issue_url,
        ),
    )


def _ctx(worktree):
    return make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                    target_dir=worktree / "target")


def _entry(tid="t1", **kw):
    kw.setdefault("summary", "do thing")
    kw.setdefault("file", "a.go")
    kw.setdefault("line", 1)
    return CommentItem(id=tid, **kw)


class TestUpdatingAnIssueThatAlreadyExists:
    """`update_deferred_issue`, which had no direct test at all.

    The cycle files one issue and refreshes it each round, so on every round
    after the first this is the path taken — and the one nothing covered.
    """

    def test_a_successful_update_carries_the_refreshed_url(self):
        with patch.object(review_issue, "update_issue", return_value=True), \
                patch.object(review_issue, "get_issue_url",
                             return_value="https://x/ENG-1"):
            result = deferred_issue.update_deferred_issue(
                "linear", "ENG-1", "body", "owner/repo", None)
        assert result.delivery is IssueDelivery.FILED
        assert result.issue == CreatedIssue(id="ENG-1", url="https://x/ENG-1")

    def test_a_failed_update_still_reports_filed(self):
        """Deliberate, and the reason is worth pinning.

        The issue exists; the refresh did not land. Reporting UNDELIVERED would
        put a closeout debt on `pr status` demanding an issue that is already
        there, and the operator would have nothing to do about it. Stale beats
        missing, and `owed` stays False.
        """
        with patch.object(review_issue, "update_issue", return_value=False):
            result = deferred_issue.update_deferred_issue(
                "linear", "ENG-1", "body", "owner/repo", None)
        assert result.delivery is IssueDelivery.FILED
        assert result.owed is False
        assert result.issue.id == "ENG-1"

    def test_a_failed_update_does_not_invent_a_url(self):
        """`get_issue_url` is not reached, so the caller's fallback decides."""
        with patch.object(review_issue, "update_issue", return_value=False), \
                patch.object(review_issue, "get_issue_url") as url:
            result = deferred_issue.update_deferred_issue(
                "linear", "ENG-1", "body", "owner/repo", None)
        url.assert_not_called()
        assert result.issue.url == ""


class TestTheSecondRoundUpdatesRatherThanRefiling:
    """The `existing_issue_id` path, which no prior test had ever executed."""

    def _run(self, worktree, *, existing_id, existing_url="", update_ok=True):
        with patch.object(review_issue, "load_issue_provider") as load, \
                patch.object(review_issue, "ensure_issue_provider") as ensure, \
                patch.object(review_issue, "update_issue", return_value=update_ok), \
                patch.object(review_issue, "get_issue_url",
                             return_value="https://x/ENG-1" if update_ok else ""), \
                patch.object(review_issue, "create_issue") as create:
            info = review_issue.IssueProviderInfo(name="linear", options={"team": "ENG"})
            load.return_value = ensure.return_value = info
            result = deferred_issue.create_or_update_deferred_issue(
                [_entry()], "owner/repo", 42, {}, _ctx(worktree),
                existing_id, None, existing_url,
            )
        return result, create

    def test_an_existing_issue_is_updated_not_created_again(self, worktree):
        result, create = self._run(worktree, existing_id="ENG-1")
        create.assert_not_called()
        assert result.issue.id == "ENG-1"

    def test_the_refreshed_url_wins_over_the_stored_one(self, worktree):
        result, _ = self._run(worktree, existing_id="ENG-1",
                              existing_url="https://stale/ENG-1")
        assert result.issue.url == "https://x/ENG-1"

    def test_the_stored_url_survives_an_update_that_returned_none(self, worktree):
        """The fallback at the heart of this path, never once evaluated before.

        A failed update yields no url. Without the fallback the state's url is
        overwritten with "", and every deferral reply already posted points at
        a link the summary can no longer render.
        """
        result, _ = self._run(worktree, existing_id="ENG-1",
                              existing_url="https://kept/ENG-1", update_ok=False)
        assert result.issue.url == "https://kept/ENG-1"


class TestATrackerWithNoTeamKey:
    """`_no_team_key` — reached only transitively before, its trail unasserted."""

    def _call(self, *, publishing_open):
        records = []

        class _Trail:
            def info(self, event, msg, **kw):
                records.append(("info", event, msg))

            def error(self, event, msg, **kw):
                records.append(("error", event, msg))

        result = deferred_issue._no_team_key(
            "linear", _Trail(), publishing_open=publishing_open)
        return result, records

    def test_an_open_gate_owes_the_issue(self):
        result, records = self._call(publishing_open=True)
        assert result.delivery is IssueDelivery.UNDELIVERED
        assert result.owed is True
        assert records == [("error", "deferred_issue", "no team key")]

    def test_a_draft_run_owes_nothing(self):
        """A draft run has failed at nothing it would otherwise have attempted."""
        result, records = self._call(publishing_open=False)
        assert result.delivery is IssueDelivery.SKIPPED
        assert result.owed is False
        assert records == [("info", "deferred_issue", "skipped — no team key")]

    @pytest.mark.parametrize("publishing_open", [True, False])
    def test_the_message_names_the_key_and_the_command(self, capsys, publishing_open):
        """#1318: the key comes from config, so the remediation is one command.

        Named in both wordings, not only the erroring one: a draft run is the
        run most likely to be the first to notice, and telling it to go look
        something up is what makes the message unactionable.
        """
        self._call(publishing_open=publishing_open)
        err = capsys.readouterr().err
        assert workbench_config.ISSUE_TEAM_KEY in err
        assert f"otto-workbench config set {workbench_config.ISSUE_TEAM_KEY}" in err


class TestTrackValidation:
    def test_an_unknown_id_exits(self):
        with pytest.raises(SystemExit):
            deferred_issue.validate_track(_state("t1"), {"nope"})

    def test_track_all_validates_nothing(self):
        deferred_issue.validate_track(_state("t1"), deferred_issue.TRACK_ALL)

    def test_a_non_deferred_id_is_unknown(self):
        """Membership is of the deferred set, not of the record."""
        state = _state("t1")
        state.fix.fix.items.append(
            ItemOutcome(id="t2", outcome=FixOutcome.FIXED))
        with pytest.raises(SystemExit):
            deferred_issue.validate_track(state, {"t2"})

    def test_an_empty_snapshot_rejects_an_id_that_cannot_exist(self, worktree):
        """#1319: the one case where "filed nothing" reads as agreement.

        `finalize_deferred` used to return before `validate_track` when the
        snapshot held no items, so a typo'd --track was neither filed nor
        reported and the run exited 0 in silence. An id that names no deferred
        thread is now reported whether or not the snapshot has items.
        """
        state = PRState(identity=_identity())
        with patch.object(deferred_issue, "create_or_update_deferred_issue") as create:
            with pytest.raises(SystemExit):
                deferred_issue.finalize_deferred(
                    state, _ctx(worktree), {}, track={"nope"})
        create.assert_not_called()

    def test_an_empty_snapshot_with_no_track_stays_silent(self, worktree, capsys):
        """The companion case #1319 insists the fix keeps apart.

        A snapshot with no items and no --track is an ordinary no-op: there is
        no unknown id, so validating one more time must not turn a quiet run
        into a noisy one.
        """
        state = PRState(identity=_identity())
        with patch.object(deferred_issue, "create_or_update_deferred_issue") as create:
            deferred_issue.finalize_deferred(state, _ctx(worktree), {})
        create.assert_not_called()
        assert capsys.readouterr().err == ""


class TestTheIssueLinkReachesTheSummary:
    """The [A22] adjacency: two surfaces, one in-memory hand-off.

    `finalize_deferred` writes the issue id and url into `state.fix`, and
    `summary_publish.render_deferred_summary` reads them back out of the same
    object to render the link in each deferred row. The two are separate
    modules called two lines apart, so nothing but call order connects them —
    and nothing failed when the writer moved to another package.
    """

    def _finalize(self, worktree, *, filed_id, filed_url):
        state = _state("t1")
        state.fix.summary_deferred = True
        result = IssueResult(IssueDelivery.FILED,
                             CreatedIssue(id=filed_id, url=filed_url))
        with patch.object(deferred_issue, "create_or_update_deferred_issue",
                          return_value=result), \
                patch.object(thread_replies, "post_deferred_replies"):
            deferred_issue.finalize_deferred(
                state, _ctx(worktree), {}, track=deferred_issue.TRACK_ALL)
        return state

    def test_the_filed_issue_reaches_the_state_the_summary_reads(self, worktree):
        state = self._finalize(worktree, filed_id="ENG-1", filed_url="https://x/ENG-1")
        assert state.fix.deferred_issue_id == "ENG-1"
        assert state.fix.deferred_issue_url == "https://x/ENG-1"
        assert state.fix.deferred_issue_pending is False

    def test_the_summary_renders_the_link_the_writer_stored(self, worktree):
        """End to end across the seam, in the order `--finish` runs them.

        The writer is in `review.deferred_issue` and the reader is in
        `pr.summary_publish`; what connects them is that both hold the same
        `state` object and one runs first. Drive the real pair, and assert the
        id reaches the published body — a mock in the middle would assert the
        seam by assuming it.
        """
        state = self._finalize(worktree, filed_id="ENG-1", filed_url="https://x/ENG-1")
        with patch("pr.comments.post_issue_comment", return_value="https://url") as post, \
                patch("pr.comments.find_marker_comment", return_value=None), \
                patch("core.publishing.enabled", return_value=True):
            summary_publish.render_deferred_summary(
                state, PRReport(), "owner/repo", 42, {})
        body = post.call_args[0][2]
        assert "ENG-1" in body
        assert "https://x/ENG-1" in body

    def test_an_unfiled_issue_leaves_the_summary_with_a_bare_deferral(self, worktree):
        """The same seam, the other way: no id written, no link rendered.

        A bare "Deferred" is what a row reads when nothing was filed, which is
        why `deferred_issue_pending` exists to say so somewhere the operator
        looks — the row alone cannot be told from one nobody asked to track.
        """
        state = self._finalize(worktree, filed_id="", filed_url="")
        with patch("pr.comments.post_issue_comment", return_value="https://url") as post, \
                patch("pr.comments.find_marker_comment", return_value=None), \
                patch("core.publishing.enabled", return_value=True):
            summary_publish.render_deferred_summary(
                state, PRReport(), "owner/repo", 42, {})
        body = post.call_args[0][2]
        assert "ENG-1" not in body
        assert "Deferred" in body

    def test_an_undelivered_issue_leaves_the_debt_on_the_state(self, worktree):
        state = _state("t1")
        with patch.object(deferred_issue, "create_or_update_deferred_issue",
                          return_value=IssueResult(IssueDelivery.UNDELIVERED)), \
                patch.object(thread_replies, "post_deferred_replies") as reply:
            deferred_issue.finalize_deferred(
                state, _ctx(worktree), {}, track=deferred_issue.TRACK_ALL)
        assert state.fix.deferred_issue_pending is True
        reply.assert_not_called()


class TestReportingRunsAfterFiling:
    """Order is the contract, and it was held only by two adjacent lines."""

    def test_a_typod_id_is_refused_before_the_unfiled_list_prints(self, worktree):
        """Reversed, the operator reads a list of unfiled threads and only then
        learns the id they passed was never valid — so the list they were given
        described a selection that never happened."""
        state = _state("t1")
        printed = []
        with patch.object(deferred_issue.log, "info", side_effect=printed.append), \
                pytest.raises(SystemExit):
            deferred_issue.finalize_deferred(
                state, _ctx(worktree), {}, track={"nope"})
        assert printed == []


class TestTheTrackingIssueBody:
    """Only what the move changed. The rest stays in `test_review_threads.py`."""

    def test_the_table_is_three_columns_wide(self):
        body = deferred_issue.build_deferred_issue_body(
            [_entry()], "owner/repo", 42, {})
        header = next(line for line in body.splitlines() if "Thread" in line)
        assert markdown.row_cells(header) == ["Thread", "File", "Reason"]

    def test_the_divider_matches_the_header(self):
        """One column count, from `markdown.table_divider`. The two were spelled
        apart and the divider's dash counts did not match its own header."""
        body = deferred_issue.build_deferred_issue_body(
            [_entry()], "owner/repo", 42, {})
        lines = body.splitlines()
        header = next(i for i, line in enumerate(lines) if "Thread" in line)
        assert lines[header + 1] == markdown.table_divider(3)

    def test_the_thread_cell_is_the_shared_one(self):
        """The same builder the summary row uses — see `permalinks.thread_cell`.

        Both tables escape the label inside the link rather than around it, and
        only this one had a test for the pipe case before they were merged.
        """
        entry = _entry(summary="use a | b")
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 12345}])}
        body = deferred_issue.build_deferred_issue_body(
            [entry], "owner/repo", 42, threads)
        row = next(line for line in body.splitlines() if "use a" in line)
        assert len(markdown.row_cells(row)) == 3
        assert "[use a \\| b](" in row
        assert "#discussion_r12345" in row

    def test_an_entry_with_no_summary_renders_the_placeholder(self):
        """An empty cell reads as a table bug; a dash reads as nothing to say.

        Pinned on the shared builder rather than on either table, because the
        merge made this one decision for both — and a mutation emptying the
        placeholder passed every test either table had before this.
        """
        body = deferred_issue.build_deferred_issue_body(
            [_entry(summary="")], "owner/repo", 42, {})
        row = next(line for line in body.splitlines() if "a.go" in line)
        assert markdown.row_cells(row)[0] == "—"


class TestDeferredOutcomes:
    """One filter, three readers — validate, file, report."""

    def test_only_deferred_outcomes_are_returned(self):
        state = _state("t1", "t2")
        state.fix.fix.items.append(ItemOutcome(id="t3", outcome=FixOutcome.FIXED))
        assert [o.id for o in deferred_issue.deferred_outcomes(state)] == ["t1", "t2"]

    def test_an_empty_record_yields_nothing(self):
        state = PRState(identity=_identity())
        assert deferred_issue.deferred_outcomes(state) == []


class TestTrackAllSelectsEverything:
    def test_every_id_is_a_member(self):
        assert "anything" in deferred_issue.TRACK_ALL

    def test_it_reports_itself_as_truthy(self):
        """Inherited from frozenset this would be False, and the sentinel that
        selects every id reporting itself as empty is the opposite of true."""
        assert bool(deferred_issue.TRACK_ALL) is True

    def test_an_ordinary_set_is_unaffected(self):
        assert "t1" not in frozenset()
