"""pr.summary_publish: posting, deferring and re-rendering the summary."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import (  # noqa: E402
    _answering_the_owner, _fake_ctx, _fix, _git_ran, _make_state, _no_published_summary,
    _tick_every_fix, content,
)
from conftest import make_ctx
import fix.comment_checklist
import fix.comments
import pr.state
from pr.comments_state import ThreadState
import git.client
import git.push
import git.topology
import pr.attribution
import pr.thread_context
import pr.fix_state
import pr.summary_publish
import pr.summary_render
from pr.summary_model import ActionCell
from pr.fix import FixOutcome, ItemOutcome, RECONCILED_REASON, SettledBy
from pr.state import PRIdentity, PRState
from pr.thread_models import CommentItem, PRReport, ReportThread, TriageResult
import review.closeout
import review.deferred_issue
import agent.backend


class TestPostOrDeferSummary:
    def _fixed_entry(self, **overrides):
        defaults = {"summary": "fix regex", "file": "parsers.py", "line": 10}
        defaults.update(overrides)
        return CommentItem(**defaults)

    def test_posts_when_pushed_no_deferred(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        with patch("pr.comments.post_issue_comment", return_value="https://url") as mock:
            url = pr.summary_publish.post_or_defer_summary(
                content(fixed=[self._fixed_entry()]), cp, "owner/repo", 1, {},
            )
        assert url == "https://url"
        mock.assert_called_once()

    def test_defers_when_needs_human(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        url = pr.summary_publish.post_or_defer_summary(
            content(
                fixed=[self._fixed_entry()],
                needs_human=[self._fixed_entry(summary="question")],
            ),
            cp, "owner/repo", 1, {},
        )
        assert url is None

    def test_defers_when_the_only_open_entry_was_declined(self, content):
        """`DECLINED` holds the summary back exactly as `NEEDS_HUMAN` does.

        Both mean a person still owes an answer, so the pass that reads only
        one of them posts a summary over a round that is not finished.
        """
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        url = pr.summary_publish.post_or_defer_summary(
            content(
                fixed=[self._fixed_entry()],
                declined=[self._fixed_entry(summary="premise is wrong")],
            ),
            cp, "owner/repo", 1, {},
        )
        assert url is None

    def test_defers_when_push_failed(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "push_failed", "rejected")
        with patch("pr.comments.post_issue_comment") as mock:
            url = pr.summary_publish.post_or_defer_summary(
                content(fixed=[self._fixed_entry()]), cp, "owner/repo", 1, {},
            )
        assert url is None
        mock.assert_not_called()


class TestRenderDeferredSummary:
    def test_not_deferred_is_noop(self):
        state = _make_state(_fix(summary_deferred=False))
        report = PRReport()
        with patch("pr.comments.post_issue_comment") as mock_post:
            pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()

    def test_renders_with_issue_link(self):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, outcome=FixOutcome.DEFERRED),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            summary_deferred=True,
            deferred_issue_id="ENG-456",
            deferred_issue_url="https://linear.app/team/issue/ENG-456",
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        assert fix.summary_url == "https://github.com/comment/1"
        assert fix.summary_deferred is False
        body = mock_post.call_args[0][2]
        assert "Deferred →" in body
        assert "[ENG-456]" in body
        assert "linear.app" in body

    def test_renders_without_issue_link(self):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, outcome=FixOutcome.DEFERRED),
            ],
            commit_status="no_changes",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "Deferred" in body
        assert "→" not in body

    def test_reports_needs_human_as_open(self):
        """The one condition that routes here is a needs_human thread."""
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="auto fix", file="a.py", line=1, outcome=FixOutcome.FIXED),
                ItemOutcome(id="t2", summary="premise disputed", file="b.py", line=2,
                              outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
                ItemOutcome(id="t3", summary="complex", file="c.py", line=3, outcome=FixOutcome.DEFERRED),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            summary_deferred=True,
            deferred_issue_id="ENG-789",
            deferred_issue_url="https://linear.app/issue/ENG-789",
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "auto fix" in body
        assert "complex" in body
        assert "premise disputed" in body
        assert "1 need discussion" in body

    def test_needs_human_settled_by_hand_renders_as_fixed(self, worktree):
        """--finish reconciles first, so the row credits the hand fix."""
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", summary_deferred=True,
                           commit_status="no_changes", items=[
                               ItemOutcome(id="t1", summary="premise disputed",
                                             file="b.py", line=2,
                                             outcome=FixOutcome.NEEDS_HUMAN,
                                             reason="contested"),
                           ]),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.RESOLVED, is_resolved=True,
            comments=[{"body": "x"}],
        )])
        with patch.object(git.client, "head_sha", return_value="aaaaaaa"), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as mock_post:
            review.closeout.finish_deferred_work(ctx, report, track=review.deferred_issue.TRACK_ALL)
        body = mock_post.call_args[0][2]
        assert "premise disputed" in body
        assert "Addressed outside the fix pass" in body
        assert "need discussion" not in body

    def test_reconstructs_commit_link(self):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="pushed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "def5678" in body

    def test_skips_when_push_failed_and_still_unpushed(self):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment") as mock_post:
            with patch.object(git.push, "holds", return_value=False):
                pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

    def test_posts_when_push_failed_but_now_pushed(self, publishing_on):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr.comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            with patch.object(git.push, "holds", return_value=True):
                pr.summary_publish.render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_called_once()
        assert fix.summary_deferred is False
        assert fix.fix.commit_status == "pushed"
        body = mock_post.call_args[0][2]
        assert "def5678" in body
        assert "push failed" not in body

    def test_held_commit_keeps_the_summary_deferred(self, publishing_on):
        """The commit link would 404 — same hazard as a failed push."""
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_held",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr.comments.post_issue_comment") as mock_post:
            with patch.object(git.push, "holds", return_value=False):
                pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

    def test_draft_run_leaves_the_deferred_queue_intact(self):
        """Retiring push_failed without publishing would strand the replies."""
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True):
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert fix.fix.commit_status == "push_failed"
        assert fix.summary_deferred is True


class TestSummaryUsesPerThreadCommit:
    """A thread's row names the commit that fixed it, not the last pass's."""

    def _post(self, *threads, commit_sha="", commit_status="no_changes"):
        fix = _fix(
            commit_sha=commit_sha, commit_status=commit_status,
            summary_deferred=True, items=list(threads),
        )
        with patch("pr.comments.post_issue_comment", return_value="u") as post:
            pr.summary_publish.render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_row_links_the_thread_own_commit(self):
        body = self._post(ItemOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            outcome=FixOutcome.FIXED, commit_sha="deadbee",
        ))
        assert "deadbee" in body
        assert "no commit needed" not in body

    def test_row_without_a_sha_claims_no_commit(self):
        body = self._post(ItemOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            outcome=FixOutcome.FIXED,
        ))
        assert ActionCell.UNATTRIBUTED in body

    def test_each_round_keeps_its_own_attribution(self):
        """The failure: one pass's envelope SHA relabelled every round."""
        body = self._post(
            ItemOutcome(id="t1", summary="round one", file="a.py", line=1,
                          outcome=FixOutcome.FIXED, commit_sha="1111111"),
            ItemOutcome(id="t2", summary="round two", file="b.py", line=2,
                          outcome=FixOutcome.FIXED, commit_sha="2222222"),
        )
        assert "1111111" in body
        assert "2222222" in body

    def test_a_reconciled_thread_claims_no_commit(self):
        """It was fixed by hand — crediting the pass's commit would be a lie.

        The file cell still permalinks at the pass's SHA; that is a location
        anchor, not a claim about who fixed it. The status cell is the claim.
        """
        body = self._post(
            ItemOutcome(id="t1", summary="fixed by hand", file="a.py", line=1,
                          outcome=FixOutcome.FIXED,
                          settled_by=SettledBy.RECONCILIATION,
                          reason=RECONCILED_REASON),
            commit_sha="def5678", commit_status="pushed",
        )
        assert "Fixed in" not in body
        assert "Addressed outside the fix pass" in body

    def test_a_thread_settled_on_the_forge_is_a_row_but_not_a_fix(self):
        """The bug this member exists for: resolution counted as work done.

        The row is shown — the thread is no longer owed and the reader should
        see that — but it is reported under its own word, and the fixed tally
        never sees it.
        """
        body = self._post(
            ItemOutcome(id="t1", summary="deferred by the reviewer", file="a.py", line=1,
                          outcome=FixOutcome.SETTLED_ELSEWHERE,
                          settled_by=SettledBy.RECONCILIATION,
                          reason=RECONCILED_REASON),
            commit_sha="def5678", commit_status="pushed",
        )
        assert ActionCell.RECONCILED in body
        assert "fixed**" not in body
        assert "1 settled elsewhere" in body

    def test_a_thread_with_no_sha_does_not_borrow_the_pass(self):
        """The pass committed; this row is not in that commit, so it says so.

        The row's file cell still permalinks at the pass's SHA — a location
        anchor pins the tree the reviewer should read, which is a different
        claim from "this commit fixed your thread". Only the status cell makes
        that claim, and it has nothing to make it with.
        """
        body = self._post(
            ItemOutcome(id="t1", summary="fix it", file="a.py", line=1,
                          outcome=FixOutcome.FIXED),
            commit_sha="def5678", commit_status="pushed",
        )
        assert ActionCell.UNATTRIBUTED in body
        assert "Fixed in" not in body
        assert "/blob/def5678/a.py" in body


class TestFailedCommitIsNotReportedAsNoCommit:
    """A hook-rejected commit published as "no commit needed".

    Two independent defects, one visible claim: recovery overwrote the known
    failure on its way out of the fix pass, and the renderer then read the
    status cell straight off a snapshot it never checked against the worktree.
    """

    @staticmethod
    def _item(tid, verification="valid"):
        return CommentItem(
            id=tid, file="f.go", line=10, reviewer="kgn", summary=f"{tid} summary",
            classification="actionable_suggestion", verification=verification,
            complexity="low", state=ThreadState.NEW,
        )

    def _fix_pass(self, tmp_path):
        """Drive a fix pass whose commit is rejected and whose HEAD never moves."""
        threads = [self._item("t1"), self._item("t2")]
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[
                ReportThread(id=t.id, file=t.file, line=t.line,
                             comments=[{"databaseId": 100 + n}])
                for n, t in enumerate(threads)
            ],
        )
        ctx = _fake_ctx(tmp_path)

        pushes = []
        commits = []

        def mock_run(*cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
                return _git_ran(1, stderr="pre-commit hook failed\n")
            return _git_ran(0, stdout="aaa1111\n")

        with patch.object(agent.backend, "invoke_fix",
                          side_effect=_tick_every_fix(tmp_path)), \
             patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist") as persist, \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(mock_run, sha="aaa1111")), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.post_issue_comment", return_value="u"), \
             patch("pr.comments.resolve_thread", return_value=True):
            result = fix.comments.run_pass(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )
        return SimpleNamespace(
            result=result, persisted=persist.call_args[0][0],
            pushes=pushes, commits=commits,
        )

    def test_the_failure_survives_recovery(self, tmp_path, publishing_on):
        """The persisted status is what --finish reads on the next run."""
        run = self._fix_pass(tmp_path)
        assert run.result.commit_status == "commit_failed"
        assert run.persisted.fix.commit_status == "commit_failed"
        assert run.persisted.fix.commit_sha == ""

    def test_a_rejected_commit_pushes_nothing(self, tmp_path, publishing_on):
        """There is no commit to publish, so no push may be attempted.

        The status cell is only half the claim: pushing a branch whose commit
        the hook rejected would put the *previous* head in front of a reviewer
        as though it carried this round's fixes.
        """
        run = self._fix_pass(tmp_path)
        assert run.commits
        assert run.pushes == []

    def test_a_hand_commit_credits_no_row_on_its_own(self):
        """Reconciliation is one yes/no about the branch, not per-row evidence.

        "HEAD moved past the snapshot" says work landed outside the pass. It
        does not say which row any of it carries, and the count of commits
        cannot make it say so: with one commit the record still holds rows an
        earlier round settled, whose fix that commit demonstrably does not
        contain. Naming HEAD anyway credits it for every row — a link that
        opens a diff the reviewer's thread is not in.

        Nothing here can be resolved per row either: the summary is rendered
        from state with no worktree to read a line history out of.
        """
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(git.client, "head_sha", return_value="ccc3333"), \
             patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_issue_comment", return_value="u") as post:
            pr.summary_publish.render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert ActionCell.UNATTRIBUTED in body
        assert "Fixed in" not in body
        assert "no commit needed" not in body
        # Where to look stays knowable even when who landed it does not: the
        # file cell pins the tree that holds the work.
        assert "/blob/ccc3333/f.go" in body

    def test_an_unpushed_hand_commit_claims_nothing(self):
        """A SHA a reviewer cannot open is not worth naming."""
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(git.client, "head_sha", return_value="bbb2222"), \
             patch.object(git.push, "holds", return_value=False), \
             patch("pr.comments.post_issue_comment", return_value="u") as post:
            pr.summary_publish.render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert ActionCell.RECONCILED in body
        assert "bbb2222" not in body

    def test_a_still_unmoved_head_keeps_the_failure(self):
        """Nothing was committed by anyone — the cell must not invent a commit."""
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(git.client, "head_sha", return_value="aaa1111"), \
             patch("pr.comments.post_issue_comment", return_value="u") as post:
            pr.summary_publish.render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert "commit failed" in body

    def test_the_contradiction_is_reported(self, capsys):
        """N fixes and no commit is caught, not rendered quietly."""
        cp = pr.attribution.CommitPushResult(None, "commit_failed", "hook")
        pr.summary_publish._warn_unattributed_fixes(
            [CommentItem(id="t1", summary="fix it", file="a.py", line=1)], cp,
        )
        assert "no commit to attribute" in capsys.readouterr().err


class TestTheWarningCountsTheRowsThatReachTheReader:
    """The warned number and the table it describes are one answer.

    The warning ran against the list before the renderer folded it and before
    the renderer settled which cell each row gets, so it counted rows nobody
    would see and rows that render a perfectly good claim. On the report this
    came from it said 10 over a table carrying 6.
    """

    @staticmethod
    def _outcome(tid, file, line, settled_by=SettledBy.PASS):
        return ItemOutcome(
            id=tid, file=file, line=line, summary=f"{tid} summary",
            outcome=FixOutcome.FIXED, settled_by=settled_by,
        )

    def _publish(self, threads):
        """Render a summary whose pass committed nothing and whose HEAD stood still."""
        by_id = {
            t.id: ReportThread(id=t.id, file=t.file, line=t.line,
                               comments=[{"databaseId": 100 + n}])
            for n, t in enumerate(threads) if not t.id.startswith("ic-")
        }
        fix = _fix(
            items=threads, commit_status="no_changes", head_sha="aaa1111",
            reviewers={t.id: "kgn" for t in threads},
            summary_deferred=True, has_comment_items=True,
        )
        with patch.object(git.client, "head_sha", return_value="aaa1111"), \
             patch("pr.comments.post_issue_comment", return_value="u") as post:
            pr.summary_publish.render_deferred_summary(
                _make_state(fix), PRReport(), "owner/repo", 1, by_id,
            )
        return post.call_args[0][2]

    def _threads(self):
        return [
            # Two rows nothing on the branch accounts for — what the warning is for.
            self._outcome("t1", "f.go", 10),
            self._outcome("t2", "g.go", 20),
            # The comment item restating t1: same reviewer, same file:line, and
            # no thread of its own, so the renderer folds it into t1's row.
            self._outcome("ic-500-1", "f.go", 10),
            # Settled outside the pass. Uncitable, but the cell says where the
            # fix went, so it is no contradiction to report.
            self._outcome("t3", "h.go", 30, settled_by=SettledBy.RECONCILIATION),
        ]

    def test_the_count_equals_the_rows_rendered_without_a_claim(self, capsys):
        body = self._publish(self._threads())
        warned = int(re.search(
            r"(\d+) fixed row\(s\) have no commit", capsys.readouterr().err,
        ).group(1))
        assert warned == body.count(ActionCell.UNATTRIBUTED)

    def test_the_folded_row_is_neither_counted_nor_rendered(self, capsys):
        body = self._publish(self._threads())
        assert "ic-500-1 summary" not in body
        assert "2 fixed row(s) have no commit" in capsys.readouterr().err

    def test_a_row_settled_outside_the_pass_is_not_a_contradiction(self, capsys):
        body = self._publish(self._threads())
        err = capsys.readouterr().err
        # Three rows carry no commit link; only two of them claim nothing. The
        # third says where its fix went, which is why "uncited" is the wrong
        # test and the rendered cell is the right one.
        assert body.count(ActionCell.RECONCILED) == 1
        assert body.count(ActionCell.UNATTRIBUTED) == 2
        assert "2 fixed row(s) have no commit" in err

    def test_a_table_with_nothing_to_report_stays_quiet(self, capsys):
        """Every row folded or settled leaves no contradiction to warn about."""
        body = self._publish([
            self._outcome("t3", "h.go", 30, settled_by=SettledBy.RECONCILIATION),
        ])
        assert ActionCell.RECONCILED in body
        assert "no commit to attribute" not in capsys.readouterr().err


class TestSummaryStillOwed:
    """Whether the round has a fix summary the PR has not been told about."""

    def _owed(self, content, commit_status="pushed", has_unaccounted=False, **kw):
        return pr.summary_publish.summary_still_owed(content(**kw), commit_status, has_unaccounted)

    def test_nothing_to_say(self, content, publishing_on):
        assert self._owed(content) is False

    def test_open_discussion_defers(self, content, publishing_on):
        assert self._owed(content, needs_human=["t1"]) is True

    def test_a_declined_entry_defers(self, content, publishing_on):
        """`DECLINED` is an open question too — see `needs_a_person`."""
        assert self._owed(content, declined=["t1"]) is True

    def test_unpushed_commit_defers(self, content, publishing_on):
        assert self._owed(
            content, commit_status="push_failed", fixed=["t1"],
        ) is True

    def test_held_commit_defers(self, content, publishing_on):
        """A held push leaves the same gap as a failed one: no remote commit."""
        assert self._owed(
            content, commit_status="push_held", fixed=["t1"],
        ) is True

    def test_a_round_with_rows_owes_them(self, content, publishing_on):
        """Owed is about the table, not about whether the post went out.

        The caller settles that half with `summary_url is None`, so a post the
        API refused leaves the summary owed instead of closing the round out.
        """
        assert self._owed(content, fixed=["t1"]) is True

    def test_draft_leaves_the_summary_owed(self, content):
        assert self._owed(content, fixed=["t1"]) is True

    def test_draft_with_nothing_to_say_owes_nothing(self, content):
        assert self._owed(content) is False

    def test_an_already_addressed_only_round_owes_its_table(self, content):
        """The round the bucket test missed: no fix, no dismissal, a full table.

        Every thread settled before this pass reached it, so the draft renders
        rows for them and records outcomes for none of the two buckets the old
        clause named.
        """
        assert self._owed(content, already_addressed=["t1"]) is True

    def test_an_unread_issue_comment_owes_a_table_on_its_own(self, content):
        """The summary reports unseen comments, so one is a row to render."""
        assert self._owed(content, issue_comments=[{"seen": False}]) is True
        assert self._owed(content, review_body_comments=[{"seen": False}]) is True

    def test_comments_the_round_already_saw_owe_nothing(self, content):
        assert self._owed(content, issue_comments=[{"seen": True}]) is False


class TestAlreadyAddressedInSummary:
    def test_rendered_as_addressed_not_dismissed(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        entry = CommentItem(
            id="t1", summary="drop the guard", file="f.go", line=10, reviewer="kgn",
        )
        body = pr.summary_render.build_summary_body(
            content(already_addressed=[entry]), cp, "owner/repo", 1, {},
        )
        assert "1 already addressed" in body
        assert "Already addressed" in body
        assert "inapplicable" not in body

    def test_deferred_summary_renders_already_addressed(self):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="drop the guard", file="f.go", line=10,
                              outcome=FixOutcome.ALREADY_ADDRESSED),
                ItemOutcome(id="t2", summary="complex", file="c.go", line=3,
                              outcome=FixOutcome.DEFERRED),
            ],
            commit_status="no_changes",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr.comments.post_issue_comment", return_value="https://url") as mock_post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "drop the guard" in body
        assert "Already addressed" in body
