"""review.deferred_issue: building, choosing and delivering the deferral issue."""

import contextlib
import sys
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _STATE_WORKTREE, _fix, _no_published_summary  # noqa: E402
from conftest import make_ctx
import pr.state
import core.log
import core.markdown
from git.land import CommitStatus
import pr.thread_replies
from pr.fix import FixOutcome, ItemOutcome
from pr.state import PRIdentity, PRState
from pr.thread_models import CommentItem, ReportThread
import review.deferred_issue
from review.issue import CreatedIssue, IssueDelivery, IssueResult


def _filed(issue_id: str, url: str) -> IssueResult:
    """What create_issue answers once a tracker accepted the write."""
    return IssueResult(IssueDelivery.FILED, CreatedIssue(id=issue_id, url=url))


# ── deferred_issue.build_deferred_issue_body ──────────────────────────────


class TestBuildDeferredIssueBody:

    def test_basic_body(self):
        deferred = [
            CommentItem(id="t1", file="src/foo.go", line=10,
                            summary="fix it", reason="agent could not auto-fix"),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 12345}]),
        }
        body = review.deferred_issue.build_deferred_issue_body(deferred, "owner/repo", 42, threads_by_id)
        assert "PR #42" in body
        assert "src/foo.go:10" in body
        assert "fix it" in body
        assert "agent could not auto-fix" in body
        assert "#discussion_r12345" in body

    def test_no_permalink(self):
        deferred = [
            CommentItem(id="t1", file="a.go", line=1,
                            summary="do thing", reason="r"),
        ]
        body = review.deferred_issue.build_deferred_issue_body(deferred, "owner/repo", 1, {})
        assert "do thing" in body
        assert "a.go:1" in body

    def test_a_missing_reason_renders_a_placeholder(self):
        """An empty cell would read as a table bug; a dash reads as "unstated"."""
        deferred = [CommentItem(id="t1", file="a.go", line=1, summary="do thing")]
        body = review.deferred_issue.build_deferred_issue_body(deferred, "owner/repo", 1, {})
        assert "—" in body

    def test_prose_cells_keep_the_row_three_columns_wide(self):
        """A pipe in prose would otherwise shift every later cell of the row.

        The summary and the reason are free text written per round. One literal
        pipe splits the row into more cells than the table has columns, so the
        reason lands under File and the tracking issue reads as a table bug.
        """
        deferred = [
            CommentItem(id="t1", file="a.go", line=1,
                        summary="use a | b", reason="see x | y"),
        ]
        body = review.deferred_issue.build_deferred_issue_body(deferred, "owner/repo", 1, {})
        row = next(line for line in body.splitlines() if "use a" in line)
        assert core.markdown.row_cells(row) == ["use a \\| b", "`a.go:1`", "see x \\| y"]

    def test_a_piped_summary_survives_inside_its_permalink_label(self):
        """The escape goes on the label, not around the link, as in summary_row."""
        deferred = [
            CommentItem(id="t1", file="a.go", line=1,
                        summary="use a | b", reason="r"),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 12345}]),
        }
        body = review.deferred_issue.build_deferred_issue_body(deferred, "owner/repo", 1, threads_by_id)
        row = next(line for line in body.splitlines() if "use a" in line)
        assert len(core.markdown.row_cells(row)) == 3
        assert "[use a \\| b](" in row


# ── deferred_issue.finalize_deferred ──────────────────────────────────────


class TestFinalizeDeferredCarriesTheReason:
    """The reason is the only column separating "agent gave up" from a decision."""

    def _state_with_deferred(self, worktree):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=_fix(items=[
                ItemOutcome(
                    id="t1", file="a.go", line=7,
                    summary="rename the guard",
                    outcome=FixOutcome.DEFERRED,
                    reason="agent could not auto-fix",
                ),
            ], reviewers={"t1": "kgn"}),
        )
        pr.state.save_state(worktree, state)
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                       target_dir=worktree / "target")
        return state, ctx

    def _run(self, state, ctx):
        captured = []
        with patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create, \
                patch.object(pr.thread_replies, "post_deferred_replies"):
            create.side_effect = lambda deferred, *a, **kw: (
                captured.extend(deferred) or _filed("I_1", "u")
            )
            review.deferred_issue.finalize_deferred(state, ctx, {}, track={"t1"})
        return captured

    def test_reason_survives_into_the_tracking_issue(self, worktree):
        state, ctx = self._state_with_deferred(worktree)
        captured = self._run(state, ctx)
        assert [e.reason for e in captured] == ["agent could not auto-fix"]

    def test_the_rest_of_the_outcome_survives_too(self, worktree):
        state, ctx = self._state_with_deferred(worktree)
        entry = self._run(state, ctx)[0]
        assert (entry.id, entry.file, entry.line) == ("t1", "a.go", 7)
        assert (entry.reviewer, entry.summary) == ("kgn", "rename the guard")

    def test_the_caller_owns_the_save(self, worktree):
        """Saving its own read would drop whatever the caller already wrote."""
        state, ctx = self._state_with_deferred(worktree)
        state.fix.fix.commit_status = CommitStatus.PUSHED
        self._run(state, ctx)
        assert state.fix.deferred_issue_id == "I_1"
        on_disk = pr.state.load_state(worktree)
        assert on_disk.fix.fix.commit_status is None
        assert on_disk.fix.deferred_issue_id == ""


class TestDeferralRequiresAChoice:
    """Deferral is a decision. An agent running out of turns is not one."""

    def _state(self, worktree, ids):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=_fix(items=[
                ItemOutcome(
                    id=i, file="a.go", line=1,
                    summary=f"item {i}", outcome=FixOutcome.DEFERRED,
                    reason="agent could not auto-fix",
                )
                for i in ids
            ], reviewers={i: "kgn" for i in ids}),
        )
        pr.state.save_state(worktree, state)
        return state

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                        target_dir=worktree / "target")

    def _run(self, state, ctx, track):
        captured = []
        with patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create, \
                patch.object(pr.thread_replies, "post_deferred_replies") as reply:
            create.side_effect = lambda deferred, *a, **kw: (
                captured.extend(deferred) or _filed("I_1", "u")
            )
            review.deferred_issue.finalize_deferred(state, ctx, {}, track=track)
        return captured, create, reply

    def test_no_selection_files_nothing(self, worktree):
        state = self._state(worktree, ["t1", "t2"])
        captured, create, reply = self._run(
            state, self._ctx(worktree), track=frozenset())
        assert captured == []
        create.assert_not_called()
        reply.assert_not_called()

    def test_default_is_no_selection(self, worktree):
        """Omitting track entirely must not fall back to filing everything."""
        state = self._state(worktree, ["t1", "t2"])
        with patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create, \
                patch.object(pr.thread_replies, "post_deferred_replies"):
            review.deferred_issue.finalize_deferred(state, self._ctx(worktree), {})
        create.assert_not_called()

    def test_only_selected_threads_are_filed(self, worktree):
        state = self._state(worktree, ["t1", "t2", "t3"])
        captured, _, _ = self._run(
            state, self._ctx(worktree), track={"t2"})
        assert [e.id for e in captured] == ["t2"]

    def test_track_all_files_everything(self, worktree):
        state = self._state(worktree, ["t1", "t2"])
        captured, _, _ = self._run(
            state, self._ctx(worktree), track=review.deferred_issue.TRACK_ALL)
        assert [e.id for e in captured] == ["t1", "t2"]

    def test_unknown_id_is_an_error_not_a_silent_skip(self, worktree):
        state = self._state(worktree, ["t1"])
        with patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create:
            assert not review.deferred_issue.finalize_deferred(
                state, self._ctx(worktree), {}, track={"t9"})
        create.assert_not_called()

    def test_a_non_deferred_id_is_also_an_error(self, worktree):
        """Naming a thread the pass already fixed is a mistake worth surfacing."""
        state = self._state(worktree, ["t1"])
        state.fix.fix.items.append(ItemOutcome(id="t2", outcome=FixOutcome.FIXED))
        with patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create:
            assert not review.deferred_issue.finalize_deferred(
                state, self._ctx(worktree), {}, track={"t2"})
        create.assert_not_called()


class TestUnfiledDeferralsAreNamed:
    """The report has to name exactly the threads nobody asked to file."""

    def _report(self, ids, track):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="abc1234", worktree_root=_STATE_WORKTREE),
            fix=_fix(items=[
                ItemOutcome(id=i, outcome=FixOutcome.DEFERRED) for i in ids
            ]),
        )
        with patch.object(core.log, "info") as info:
            review.deferred_issue.report_unfiled_deferrals(state, track)
        return " ".join(str(c) for c in info.call_args_list)

    def test_no_selection_names_every_deferral(self):
        msg = self._report(["t1", "t2"], frozenset())
        assert "t1" in msg and "t2" in msg

    def test_partial_selection_names_only_the_rest(self):
        """A non-empty selection is not a reason to stop reporting the others."""
        msg = self._report(["t1", "t2", "t3"], frozenset({"t2"}))
        assert "t1" in msg and "t3" in msg
        assert "t2" not in msg

    def test_track_all_leaves_nothing_unfiled(self):
        assert self._report(["t1", "t2"], review.deferred_issue.TRACK_ALL) == ""

    def test_nothing_deferred_says_nothing(self):
        assert self._report([], frozenset()) == ""

    def test_the_sentinel_is_not_an_empty_set(self):
        """It selects everything; code that asks `if track:` must hear yes."""
        assert bool(review.deferred_issue.TRACK_ALL) is True


class TestDeferredIssueProvider:
    """#795: a GitHub repo was skipped for a Linear-shaped reason.

    ``ensure_issue_provider`` only runs once publishing is enabled — a draft
    run files nothing, so asking which tracker to use has no consequence.
    Every case here that expects the resolved-provider path to run therefore
    opens the gate with ``publishing_on``; the one exception is the gate test
    itself, which relies on the default closed gate.
    """

    def _deferred(self):
        return [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]

    def _create(self, **overrides):
        kwargs = dict(
            deferred=self._deferred(), repo="owner/repo", pr_number=1,
            threads_by_id={}, ctx=make_ctx(), existing_issue_id="", trail=None,
        )
        kwargs.update(overrides)
        return review.deferred_issue.create_or_update_deferred_issue(**kwargs)

    def test_stops_when_no_tracker_is_configured(self, publishing_on):
        """An unset provider must report, not quietly file nothing."""
        import review.issue
        with patch.object(
            review.issue, "ensure_issue_provider",
            return_value=review.issue.IssueProviderInfo(),
        ), patch.object(review.issue, "create_issue") as created:
            result = self._create()
        assert result.issue.id == ""
        assert result.owed is True
        created.assert_not_called()

    def test_github_needs_no_team_key(self, publishing_on):
        """gh issue create is addressed by repo; a branch with no ABC-123 is fine."""
        import review.issue
        info = review.issue.IssueProviderInfo(name="github", options={})
        with patch.object(review.issue, "ensure_issue_provider", return_value=info), \
             patch.object(
                 review.issue, "create_issue",
                 return_value=_filed("#42", "https://gh/42"),
             ) as created:
            result = self._create()
        assert result.issue.id == "#42"
        created.assert_called_once()

    def test_linear_prefers_the_configured_team(self, publishing_on):
        """issues.team is published config; it should be read."""
        import review.issue
        info = review.issue.IssueProviderInfo(name="linear", options={"team": "ENG"})
        with patch.object(review.issue, "ensure_issue_provider", return_value=info), \
             patch.object(
                 review.issue, "create_issue",
                 return_value=_filed("ENG-9", "https://linear/ENG-9"),
             ) as created:
            self._create()
        created.assert_called_once_with(
            "linear", "ENG", ANY, ANY, parent_id=None, repo="owner/repo", opts={"team": "ENG"},
        )

    def test_a_branch_derived_id_no_longer_supplies_the_team(self, publishing_on):
        """#1318: the team comes from config alone, never from the branch.

        Splitting the key out of the parent issue id made filing depend on what
        the run was invoked against rather than on how the repo is configured:
        a branch carrying a parent id filed, the same repo on a branch without
        one reported no team key. The id is still read — Linear takes it as
        `--parent` — but it no longer answers for the team.
        """
        import review.issue
        info = review.issue.IssueProviderInfo(name="linear", options={})
        with patch.object(review.issue, "ensure_issue_provider", return_value=info), \
             patch.object(review.issue, "create_issue") as created:
            result = self._create(ctx=make_ctx(branch="isaac/ENG-1/x"))
        created.assert_not_called()
        assert result.owed is True

    def test_linear_still_skips_with_no_team_anywhere(self, publishing_on):
        """Skipped, but owed: nothing was filed and the deferrals have no home."""
        import review.issue
        info = review.issue.IssueProviderInfo(name="linear", options={})
        with patch.object(review.issue, "ensure_issue_provider", return_value=info), \
             patch.object(review.issue, "create_issue") as created:
            result = self._create()
        assert result.issue.id == ""
        assert result.owed is True
        created.assert_not_called()

    def test_a_draft_run_does_not_ask_which_tracker(self):
        """create_issue files nothing while publishing is off, so asking is pointless."""
        import core.publishing
        import review.issue
        with patch.object(core.publishing, "enabled", return_value=False), \
             patch.object(review.issue, "ensure_issue_provider") as asked, \
             patch.object(
                 review.issue, "load_issue_provider",
                 return_value=review.issue.IssueProviderInfo(),
             ) as loaded:
            result = self._create()
        asked.assert_not_called()
        loaded.assert_called_once_with("/wt")
        assert result.issue == CreatedIssue()
        assert result.owed is False

    def test_unresolved_provider_reaches_the_trail_as_an_error(self, publishing_on):
        """Deleting the trail.error call would leave the suite green without this."""
        import review.issue
        trail = MagicMock()
        with patch.object(
            review.issue, "ensure_issue_provider",
            return_value=review.issue.IssueProviderInfo(),
        ), patch.object(review.issue, "create_issue"):
            self._create(trail=trail)
        trail.error.assert_called_once_with("deferred_issue", "no issue tracker configured")
        trail.info.assert_not_called()

    def test_unresolved_provider_in_draft_mode_reaches_the_trail_as_info(self):
        """The unresolved-path event fires here too, but as info — and only here.

        A resolved provider whose creation genuinely fails still reaches
        ``trail.error("deferred_issue", "creation failed")`` in
        ``_create_deferred_issue`` whether or not the gate is open — this
        asserts the unresolved path never does while the gate is shut.
        """
        import review.issue
        trail = MagicMock()
        with patch.object(
            review.issue, "load_issue_provider",
            return_value=review.issue.IssueProviderInfo(),
        ), patch.object(review.issue, "create_issue"):
            self._create(trail=trail)
        trail.info.assert_called_once_with(
            "deferred_issue", "skipped — no issue tracker configured",
        )
        trail.error.assert_not_called()


class TestDeferredIssueDraftIsNotAFailure:
    """#804: a draft run and a refused tracker both used to arrive as ``None``.

    The gate declining a write is the gate working, so it must not reach the
    closeout `pr status` reads — while a creation that genuinely failed must,
    gate open or shut.
    """

    def _create(self, delivery, trail):
        import review.issue
        info = review.issue.IssueProviderInfo(name="github", options={})
        with patch.object(review.issue, "load_issue_provider", return_value=info), \
             patch.object(
                 review.issue, "create_issue",
                 return_value=IssueResult(delivery),
             ):
            return review.deferred_issue.create_or_update_deferred_issue(
                deferred=[CommentItem(id="t1", summary="fix regex")],
                repo="owner/repo", pr_number=1, threads_by_id={},
                ctx=make_ctx(), existing_issue_id="", trail=trail,
            )

    def test_a_declined_write_is_reported_as_deferral(self, capsys):
        trail = MagicMock()
        self._create(IssueDelivery.SKIPPED, trail)
        trail.info.assert_called_once_with("deferred_issue", "skipped — publishing off")
        trail.error.assert_not_called()
        assert "Failed to create" not in capsys.readouterr().err

    def test_a_failed_creation_is_an_error_even_while_the_gate_is_shut(self, capsys):
        """Reading the gate again instead of the return value would lose this."""
        trail = MagicMock()
        self._create(IssueDelivery.UNDELIVERED, trail)
        trail.error.assert_called_once_with("deferred_issue", "creation failed")
        trail.info.assert_not_called()
        assert "Failed to create deferred tracking issue" in capsys.readouterr().err


class TestUndeliveredDeferredIssueReachesTheState:
    """#805: threads were filed against a tracking issue that never existed.

    The only record was a trail event, and nobody reads the trail to decide
    whether a PR is safe to merge — so `pr status` said ready while the
    deferred comments had no home.
    """

    def _finalize(self, worktree, provider, create=None):
        import review.issue
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="abc1234", worktree_root=str(worktree)),
            fix=_fix(items=[
                ItemOutcome(id="t1", file="a.go", line=7,
                            summary="rename the guard", outcome=FixOutcome.DEFERRED),
            ], reviewers={"t1": "kgn"}),
        )
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                       target_dir=worktree / "target")
        creation = patch.object(review.issue, "create_issue", return_value=create) \
            if create is not None else contextlib.nullcontext()
        with patch.object(review.issue, "ensure_issue_provider", return_value=provider), \
                patch.object(review.issue, "load_issue_provider", return_value=provider), \
                patch.object(pr.thread_replies, "post_deferred_replies"), creation:
            review.deferred_issue.finalize_deferred(state, ctx, {}, track={"t1"})
        return state.fix

    def _provider(self, name):
        import review.issue
        return review.issue.IssueProviderInfo(name=name, options={})

    def test_a_creation_failure_is_recorded(self, worktree, publishing_on):
        fix = self._finalize(
            worktree, self._provider("github"),
            create=IssueResult(IssueDelivery.UNDELIVERED),
        )
        assert fix.deferred_issue_pending is True

    def test_a_provider_that_cannot_create_issues_is_recorded(
        self, worktree, publishing_on,
    ):
        fix = self._finalize(worktree, self._provider("jira"))
        assert fix.deferred_issue_pending is True

    def test_no_tracker_configured_is_recorded(self, worktree, publishing_on):
        fix = self._finalize(worktree, self._provider(""))
        assert fix.deferred_issue_pending is True

    def test_a_tracker_with_no_team_key_is_recorded(self, worktree, publishing_on):
        """A branch with no ABC-123 and no configured team files nothing either."""
        fix = self._finalize(worktree, self._provider("linear"))
        assert fix.deferred_issue_pending is True
        assert fix.closeout_debt().owed is True

    def test_a_draft_run_with_no_team_key_owes_nothing(self, worktree):
        """Nothing was attempted, so the missing key cost the run nothing."""
        fix = self._finalize(worktree, self._provider("linear"))
        assert fix.deferred_issue_pending is False

    def test_a_filed_issue_owes_nothing(self, worktree, publishing_on):
        fix = self._finalize(
            worktree, self._provider("github"), create=_filed("#42", "https://gh/42"),
        )
        assert fix.deferred_issue_pending is False
        assert fix.deferred_issue_id == "#42"

    def test_a_draft_run_owes_nothing(self, worktree):
        """The gate declining the write is not a tracking issue gone missing."""
        fix = self._finalize(worktree, self._provider("github"))
        assert fix.deferred_issue_pending is False
