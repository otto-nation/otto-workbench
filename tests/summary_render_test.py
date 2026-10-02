"""pr.summary_render: the summary body, its framing and folded rows."""

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _AFTER_THE_REVIEW, _BEFORE_THE_REVIEW, _THE_REVIEW_COMMENT, _no_published_summary,
    _unmarked, content,
)
from conftest import git_out, run_checked
import git.topology
from git.land import CommitStatus
import pr.thread_replies
import pr.attribution
import pr.summary_model
import pr.summary_render
import pr.summary_scope
from pr.summary_model import ActionCell
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import CommentItem, ReportThread


# ── _build_summary_body ─────────────────────────────────────────────────────


class TestBuildSummaryBody:
    """Test summary body renders correct status per CommitPushResult."""

    def _fixed_entry(self, **overrides):
        defaults = {"summary": "fix regex", "file": "parsers.py", "line": 10}
        defaults.update(overrides)
        return CommentItem(**defaults)

    def test_pushed_shows_commit_link(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[self._fixed_entry(commit_sha="abc1234")]),
            cp, "owner/repo", 1, {},
        )
        assert "/commit/abc1234" in body
        assert "push failed" not in body

    def test_no_changes_shows_an_unattributed_fix(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[self._fixed_entry()]), cp, "owner/repo", 1, {},
        )
        assert ActionCell.UNATTRIBUTED in body
        assert "no commit needed" not in body

    def test_commit_failed_shows_precommit_hint(self, content):
        cp = pr.attribution.CommitPushResult(None, "commit_failed", "hook error")
        body = pr.summary_render.build_summary_body(
            content(fixed=[self._fixed_entry()]), cp, "owner/repo", 1, {},
        )
        assert "commit failed" in body

    def test_push_failed_names_the_local_commit(self, content):
        """The row says the work is committed but unpublished, and links nothing.

        A SHA the remote does not have would 404 for whoever clicks it, so the
        cell states the situation rather than citing it.
        """
        cp = pr.attribution.CommitPushResult("abc1234", "push_failed", "rejected")
        body = pr.summary_render.build_summary_body(
            content(fixed=[self._fixed_entry(commit_sha="abc1234")]),
            cp, "owner/repo", 1, {},
        )
        assert "committed locally (push failed)" in body
        assert "/commit/abc1234" not in body

    def test_needs_human_rows(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(needs_human=[
                CommentItem(summary="question", file="a.py", line=1, reason="contested"),
            ]),
            cp, "owner/repo", 1, {},
        )
        assert pr.summary_model.HumanReason.CONTESTED.prose in body

    def test_a_declined_entry_reaches_the_table_beside_needs_human(self, content):
        """The coarsening `RoundContent.needs_a_person` owns, seen from the table.

        `DECLINED` is a bucket of its own in the state file and shares the
        reviewer-facing one with `NEEDS_HUMAN`; a renderer reading only the
        latter would drop the entries the agent argued against.
        """
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(
                needs_human=[CommentItem(
                    id="t1", summary="question", file="a.py", line=1,
                    reason="contested",
                )],
                declined=[CommentItem(
                    id="t2", summary="premise is wrong", file="b.py", line=2,
                )],
            ),
            cp, "owner/repo", 1, {},
        )
        assert "question" in body
        assert "premise is wrong" in body
        assert "2 need discussion" in body

    def test_empty_returns_no_table(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(content(), cp, "owner/repo", 1, {})
        assert "Thread" not in body

    def test_thread_permalink_in_summary(self, content):
        """Fixed entries with matching thread data render as links."""
        tid = "PRRT_abc123"
        entry = self._fixed_entry(id=tid)
        threads_by_id = {
            tid: ReportThread(id=tid, comments=[{"databaseId": 999}]),
        }
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 42, threads_by_id,
        )
        assert "#discussion_r999" in body
        assert "[fix regex]" in body

    def test_comment_item_issue_comment_permalink(self, content):
        """Comment items from issue comments link to #issuecomment-{source_id}."""
        entry = CommentItem(
            id="ic-77777-0", summary="add tests", file="foo.py", line=5,
            source_id="77777", source_type="issue_comment",
        )
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 42, {},
        )
        assert "#issuecomment-77777" in body
        assert "[add tests]" in body

    def test_comment_item_review_body_permalink(self, content):
        """Comment items from review bodies link to #pullrequestreview-{source_id}."""
        entry = CommentItem(
            id="rb-88888-1", summary="refactor needed", file="bar.py", line=3,
            source_id="88888", source_type="review_body",
        )
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 42, {},
        )
        assert "#pullrequestreview-88888" in body
        assert "[refactor needed]" in body

    def test_a_replayed_comment_item_keeps_its_permalink(self, content):
        """An entry rebuilt from a recorded outcome still parses its source id.

        The replay path `--finish` takes: what state holds is an `ItemOutcome`,
        and every renderer downstream reads a `CommentItem`, so the synthetic id
        has to survive `from_outcome` intact for the permalink to resolve.
        """
        entry = CommentItem.from_outcome(ItemOutcome(
            id="ic-99999-0", summary="fix typo", file="readme.md", line=1,
            outcome=FixOutcome.FIXED,
        ))
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 42, {},
        )
        assert "#issuecomment-99999" in body
        assert "[fix typo]" in body

    def test_reviewer_column_rendered(self, content):
        """Table rows include the reviewer as @mention."""
        entry = self._fixed_entry(reviewer="alice")
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 1, {},
        )
        assert "| Reviewer |" in body
        assert "@alice" in body

    def test_reviewer_column_missing_shows_dash(self, content):
        """Entries without a reviewer show a dash."""
        entry = self._fixed_entry(reviewer="")
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[entry]), cp, "owner/repo", 1, {},
        )
        assert "| — |" in body

    def test_unseen_issue_comments_render_discussion_section(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        issue_comments = [
            {"user": "alice", "body": "Can we add tests?", "seen": False},
        ]
        body = pr.summary_render.build_summary_body(
            content(issue_comments=issue_comments), cp, "owner/repo", 1, {},
        )
        assert "### Discussion Comments" in body
        assert "@alice" in body
        assert "Can we add tests?" in body

    def test_seen_issue_comments_not_rendered(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        issue_comments = [
            {"user": "alice", "body": "Old comment", "seen": True},
        ]
        body = pr.summary_render.build_summary_body(
            content(issue_comments=issue_comments), cp, "owner/repo", 1, {},
        )
        assert "Discussion Comments" not in body

    def test_unseen_review_body_comments_render_review_level_section(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        review_body_comments = [
            {"user": "bob", "state": "CHANGES_REQUESTED", "body": "Needs refactor", "seen": False},
        ]
        body = pr.summary_render.build_summary_body(
            content(review_body_comments=review_body_comments),
            cp, "owner/repo", 1, {},
        )
        assert "### Review-Level Comments" in body
        assert "@bob" in body
        assert "(CHANGES_REQUESTED)" in body
        assert "Needs refactor" in body

    def test_seen_review_body_comments_not_rendered(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        review_body_comments = [
            {"user": "bob", "state": "APPROVED", "body": "Looks good", "seen": True},
        ]
        body = pr.summary_render.build_summary_body(
            content(review_body_comments=review_body_comments),
            cp, "owner/repo", 1, {},
        )
        assert "Review-Level Comments" not in body

    def test_deferred_with_issue_link(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        deferred = [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]
        body = pr.summary_render.build_summary_body(
            content(deferred=deferred), cp, "owner/repo", 1, {},
            deferred_issue_id="ENG-456",
            deferred_issue_url="https://linear.app/team/issue/ENG-456",
        )
        assert "ENG-456" in body
        assert "Deferred →" in body
        assert "linear.app" in body

    def test_deferred_without_issue(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        deferred = [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]
        body = pr.summary_render.build_summary_body(
            content(deferred=deferred), cp, "owner/repo", 1, {},
        )
        assert "Deferred" in body
        assert "→" not in body


class TestEveryVerdictReachesTheTable:
    """A bucket with no branch in the renderer is a row nobody ever reads.

    That is the shape the settled-elsewhere defect had before it had a bucket:
    the outcome existed, the record held it, and the summary the reviewer read
    accounted for it under somebody else's heading.
    """

    # The CI pass's word for an item it refuses to look at on sight. The comment
    # pass has no such items — every thread triage keeps is attempted — so the
    # comment summary has no wording for it and no bucket to put it in.
    _NOT_THE_COMMENT_PASS = frozenset({FixOutcome.SKIPPED})

    def test_each_verdict_renders_exactly_one_row_and_is_counted_once(
        self, content,
    ):
        """The row and the tally are asserted together, over the whole enum.

        They are the pairing that can disagree: a verdict can reach the table
        under a heading and still be added to `**N fixed**`, or be counted and
        never printed. Sweeping the enum is what makes the next member fail
        here rather than land silently in somebody else's bucket.
        """
        verdicts = [o for o in FixOutcome if o not in self._NOT_THE_COMMENT_PASS]
        buckets = {
            o.value: [CommentItem(id=f"t{n}", file="a.py", line=n,
                                  reviewer="kgn", summary=f"point {n}")]
            for n, o in enumerate(verdicts, start=1)
        }
        body = pr.summary_render.build_summary_body(
            content(**buckets), pr.attribution.CommitPushResult("abc1234", "pushed", ""),
            "owner/repo", 42, {},
        )
        assert [f"point {n}" in body for n in range(1, len(verdicts) + 1)] == (
            [True] * len(verdicts)
        )
        counts = next(ln for ln in body.split("\n") if "fixed**" in ln)
        assert sum(int(n) for n in re.findall(r"\d+", counts)) == len(verdicts)


# ── _summarize_comment_body ─────────────────────────────────────────────────


class TestSummarizeCommentBody:
    def test_plain_text(self):
        assert pr.summary_render.summarize_comment_body("Hello world") == "Hello world"

    def test_markdown_header_stripped(self):
        assert pr.summary_render.summarize_comment_body("## Section Title") == "Section Title"

    def test_single_line_html_comment_skipped(self):
        body = "<!-- metadata -->\nActual content"
        assert pr.summary_render.summarize_comment_body(body) == "Actual content"

    def test_multiline_html_comment_skipped(self):
        body = "<!-- head_sha: abc\ndate: 2026-07-13\n-->\nActual content"
        assert pr.summary_render.summarize_comment_body(body) == "Actual content"

    def test_empty_body(self):
        assert pr.summary_render.summarize_comment_body("") == "(empty)"

    def test_only_html_comments_returns_empty(self):
        body = "<!-- comment -->\n<!-- another -->"
        assert pr.summary_render.summarize_comment_body(body) == "(empty)"

    def test_truncates_long_line(self):
        long = "x" * 200
        result = pr.summary_render.summarize_comment_body(long, max_len=120)
        assert len(result) == 120
        assert result.endswith("…")


class TestAddressedInResponseFraming:
    """A reviewer we acted for must not be told their comment needed no action.

    Triage reads current HEAD, which holds the fixes an earlier round of the
    same cycle landed, so a thread the pass fixed comes back `already_addressed`
    on the next run. The verdict answers "does the code do this now?" correctly;
    the flat "Already addressed" answers "was your comment moot?" wrongly.
    """

    @staticmethod
    def _git(wt, *args, when=""):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        run_checked(["git", "-C", str(wt), *args], env=env)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def branch(self, worktree):
        """A branch with one commit dated before the review and one after it."""
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "a.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        (worktree / "a.py").write_text("ONE\ntwo\n")
        self._git(worktree, "commit", "-qam", "line one, long before the review",
                  when=_BEFORE_THE_REVIEW)
        before = self._sha(worktree, "HEAD")
        (worktree / "a.py").write_text("ONE\nTWO\n")
        self._git(worktree, "commit", "-qam", "line two, in response to the review",
                  when=_AFTER_THE_REVIEW)
        return SimpleNamespace(path=worktree, before=before,
                               after=self._sha(worktree, "HEAD"))

    @staticmethod
    def _thread(tid, database_id):
        return ReportThread(id=tid, comments=[
            {"databaseId": database_id, "createdAt": _THE_REVIEW_COMMENT},
        ])

    def _reply_body(self, entry, thread, wt_path, **kwargs):
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.post_already_addressed_replies(
                [entry], {entry.id: thread}, "owner/repo", 42, wt_path, **kwargs,
            )
        return post.call_args[0][3]

    def test_a_commit_after_the_review_reads_as_addressed_in_response(self, branch):
        body = self._reply_body(
            CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert body.startswith("Applied: rename it")
        assert "Already addressed" not in body
        assert f"Fixed in [`{branch.after[:7]}`]" in body

    def test_code_predating_the_comment_stays_already_addressed(self, branch):
        """The genuine case: the reviewer's point was true before they made it."""
        body = self._reply_body(
            CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert body.startswith("Already addressed: use the helper")
        assert "Applied:" not in body
        assert f"Addressed in [`{branch.before[:7]}`]" in body

    def test_an_undated_thread_keeps_the_pre_existing_reading(self, branch):
        """Claiming credit is the assertion that needs evidence, not the absence."""
        body = self._reply_body(
            CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            ReportThread(id="t2", comments=[{"databaseId": 222}]), branch.path,
        )
        assert body.startswith("Already addressed: rename it")

    def test_a_fix_the_resolver_cannot_cite_still_reads_as_a_fix(self, branch):
        """#827's caller: a FIXED entry whose commit a hook rejected lands here.

        The pass acted on the thread — that is what put the entry in `fixed` —
        so the reply says so even though no commit can be named for it. The one
        commit the branch offers for that line predates the comment and cannot
        be what carried a fix made after it, so nothing is cited.
        """
        entry = CommentItem(id="t1", summary="use the helper", file="a.py", line=1)
        cp = pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t1": self._thread("t1", 111)}, "owner/repo", 42,
                cp, branch.path,
            )
        body = post.call_args[0][3]
        assert body.startswith("Applied: use the helper")
        assert "Already addressed" not in body
        assert branch.before[:7] not in body

    def _summary(self, content, entry, thread, wt_path):
        cp = pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            return pr.summary_render.build_summary_body(
                content(already_addressed=[entry]),
                cp, "owner/repo", 42, {entry.id: thread}, wt_path=wt_path,
            )

    def test_the_summary_row_reports_a_responsive_fix_as_fixed(self, content, branch):
        body = self._summary(
            content, CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert f"Fixed in [`{branch.after}`]" in body
        assert "Already addressed" not in body
        assert "**1 fixed**" in body

    def test_the_summary_row_keeps_already_addressed_for_older_code(
        self, content, branch,
    ):
        body = self._summary(
            content,
            CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert "Already addressed" in body
        assert "1 already addressed" in body
        assert "fixed" not in body


class TestDuplicateFindingRendersOnce:
    """One review point that arrived twice is still one row in the table."""

    def _thread(self, **kw):
        defaults = {"id": "t1", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "drop the retry"}
        defaults.update(kw)
        return CommentItem(**defaults)

    def _item(self, **kw):
        defaults = {"id": "ic-77-0", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "also drop the retry", "reason": "contested"}
        defaults.update(kw)
        return CommentItem(**defaults)

    def _body(self, content, fixed, needs_human, threads_by_id=None):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        return pr.summary_render.build_summary_body(
            content(fixed=fixed, needs_human=needs_human),
            cp, "owner/repo", 42,
            threads_by_id if threads_by_id is not None else self._threads(),
        )

    def _threads(self):
        return {"t1": ReportThread(id="t1", file="a.go", line=7, reviewer="kgn",
                                   comments=[{"databaseId": 5}])}

    def test_the_item_folds_into_the_thread_it_restates(self, content):
        body = self._body(content, [self._thread()], [self._item()])
        assert len(pr.summary_scope.table_rows(body)) == 1
        assert "#issuecomment-77" not in body
        assert "#discussion_r5" in body

    def test_the_counts_line_never_promises_a_row_it_folded(self, content):
        body = self._body(content, [self._thread()], [self._item()])
        assert "need discussion" not in body
        assert "1 fixed" in body

    def test_another_line_is_another_finding(self, content):
        body = self._body(content, [self._thread()], [self._item(line=9)])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_an_item_naming_no_line_falls_back_to_its_text(self, content):
        """No line is "cannot answer", and the text signal answers instead.

        These two summaries are shorter than a containment match is allowed to
        be, so nothing folds — but the reason is the length gate rather than the
        missing line. The class below covers the case where the text is long
        enough to carry the match.
        """
        body = self._body(content, [self._thread()], [self._item(line=0)])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_another_reviewers_point_is_another_finding(self, content):
        body = self._body(content, [self._thread()], [self._item(reviewer="amp")])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_two_real_threads_are_never_folded_together(self, content):
        threads = self._threads()
        threads["t2"] = ReportThread(id="t2", file="a.go", line=7, reviewer="kgn",
                                     comments=[{"databaseId": 6}])
        body = self._body(
            content, [self._thread()],
            [self._thread(id="t2", summary="and rename it")],
            threads,
        )
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_an_item_with_no_thread_to_fold_into_still_renders(self, content):
        body = self._body(content, [], [self._item()], {})
        rows = pr.summary_scope.table_rows(body)
        assert len(rows) == 1
        assert "#issuecomment-77" in body

    def test_the_folded_locations_are_where_the_folded_ids_were(self, content):
        """The carry-forward step reads locations; the render reads ids.

        Both come off the same buckets, so a location this reports must be one
        of the entries the fold removed — otherwise a published row would be
        dropped for a fold that never happened.
        """
        round_content = content(fixed=[self._thread()], needs_human=[self._item()])
        threads = self._threads()
        assert pr.summary_model.folded_item_ids(round_content, threads) == {"ic-77-0"}
        assert pr.summary_model.folded_locations(round_content, threads) == frozenset({"kgn|a.go:7"})

    def test_an_unfolded_round_reports_no_locations(self, content):
        round_content = content(needs_human=[self._item()])
        assert pr.summary_model.folded_locations(round_content, {}) == frozenset()

    def test_a_declined_item_folds_into_the_thread_it_restates(self, content):
        """`folded_item_ids` reads every bucket, so the fold is not `needs_human`'s.

        The duplicate detector walks the whole mapping rather than a list of
        bucket names, which is what keeps an outcome added later from being
        folded only once someone remembers to add it.
        """
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[self._thread()], declined=[self._item()]),
            cp, "owner/repo", 42, self._threads(),
        )
        assert len(pr.summary_scope.table_rows(body)) == 1
        assert "#issuecomment-77" not in body


class TestALineLessItemFoldsOnItsText:
    """The fold's other end: an item with no line had no key to fold on at all.

    Triage is asked for a line only "if referenced in the item", and a prose
    paragraph references none — so `finding_location` returned "" on exactly the
    copy the fold exists to remove, and `thread_covered_locations` stripped it
    from the covered set. One reviewer's five inline threads plus a review-body
    paragraph restating four of them published four duplicate rows from the
    weaker surface and none for the fifth.

    The second signal is exact containment of normalised text, not similarity:
    a false fold hides a reviewer's finding entirely, a missed one only prints a
    duplicate row.
    """

    POINT = "the retry loop is unbounded and will spin forever"

    def _thread_entry(self, **kw):
        defaults = {"id": "t1", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": self.POINT}
        defaults.update(kw)
        return CommentItem(**defaults)

    def _item(self, **kw):
        defaults = {"id": "ic-77-0", "file": "a.go", "line": 0, "reviewer": "kgn",
                    "summary": "the retry loop is unbounded", "reason": "contested"}
        defaults.update(kw)
        return CommentItem(**defaults)

    def _threads(self, body=""):
        return {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            comments=[{"databaseId": 5, "body": body or self.POINT}],
        )}

    def _body(self, content, fixed, needs_human, threads_by_id=None):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        return pr.summary_render.build_summary_body(
            content(fixed=fixed, needs_human=needs_human), cp, "owner/repo", 42,
            self._threads() if threads_by_id is None else threads_by_id,
        )

    def test_the_line_less_item_folds_into_the_thread_it_restates(self, content):
        body = self._body(content, [self._thread_entry()], [self._item()])
        assert len(pr.summary_scope.table_rows(body)) == 1
        assert "#issuecomment-77" not in body
        assert "#discussion_r5" in body

    def test_the_counts_line_does_not_promise_the_folded_row(self, content):
        body = self._body(content, [self._thread_entry()], [self._item()])
        assert "need discussion" not in body
        assert "1 fixed" in body

    def test_an_unrelated_item_is_not_folded(self, content):
        """The negative case the asymmetry is about: a false fold hides a finding."""
        body = self._body(
            content, [self._thread_entry()],
            [self._item(summary="this variable name is misleading to the reader")],
        )
        assert len(pr.summary_scope.table_rows(body)) == 2
        assert "#issuecomment-77" in body

    def test_another_reviewers_restatement_is_another_finding(self, content):
        """Two people writing about one point are writing about two things."""
        body = self._body(
            content, [self._thread_entry()], [self._item(reviewer="amp")])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_a_restatement_of_another_file_is_another_finding(self, content):
        body = self._body(
            content, [self._thread_entry()], [self._item(file="b.go")])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_a_short_restatement_is_left_alone(self, content):
        """Below the length gate, containment is coincidence rather than evidence."""
        body = self._body(
            content, [self._thread_entry(summary="drop it")],
            [self._item(summary="drop it")],
        )
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_the_reviewers_own_comment_body_can_carry_the_match(self, content):
        """Triage summarises a thread; the item is likelier to quote the comment."""
        body = self._body(
            content, [self._thread_entry(summary="unbounded retry")],
            [self._item()], self._threads(body=self.POINT),
        )
        assert len(pr.summary_scope.table_rows(body)) == 1

    def test_an_item_that_does_have_a_line_still_folds_on_location(
        self, content,
    ):
        """The precise signal is asked first, and unrelated text does not undo it."""
        body = self._body(
            content, [self._thread_entry()],
            [self._item(line=7, summary="a completely different point entirely")],
        )
        assert len(pr.summary_scope.table_rows(body)) == 1

    def test_an_item_with_a_line_elsewhere_does_not_fall_back_to_text(
        self, content,
    ):
        """A line that answers "no" is an answer; only "" falls through."""
        body = self._body(
            content, [self._thread_entry()], [self._item(line=9)])
        assert len(pr.summary_scope.table_rows(body)) == 2

    def test_the_folded_text_is_reported_for_the_carry_forward_step(
        self, content,
    ):
        round_content = content(
            fixed=[self._thread_entry()], needs_human=[self._item()])
        threads = self._threads()
        assert pr.summary_model.folded_item_ids(round_content, threads) == {"ic-77-0"}
        # The thread's location, which is the one the fold kept. The published
        # item row carries no line, so `summary_model.location_from_cells` reads "" off it and
        # this set can never recognise it — hence the second one.
        assert pr.summary_model.folded_locations(round_content, threads) == frozenset(
            {"kgn|a.go:7"})
        assert pr.summary_model.folded_restatements(round_content, threads) == frozenset(
            {"the retry loop is unbounded"})

    def test_an_item_folded_on_location_is_not_reported_as_text(self, content):
        """`folded_locations` already names it; naming it twice widens the fold."""
        round_content = content(
            fixed=[self._thread_entry()], needs_human=[self._item(line=7)])
        assert pr.summary_model.folded_restatements(
            round_content, self._threads()) == frozenset()

    def test_the_published_row_of_a_text_fold_is_not_carried_back(self):
        """Otherwise the duplicate returns verbatim every round, for good."""
        thread_row = (
            "| [the retry loop is unbounded and will spin forever]"
            "(https://github.com/o/r/pull/1#discussion_r5) | @kgn | "
            "[`a.go:7`](https://github.com/o/r/blob/abc/a.go#L7) | Fixed |")
        item_row = (
            "| [the retry loop is unbounded](https://github.com/o/r/pull/1"
            "#issuecomment-77) | @kgn | `a.go` | contested |")
        published = f"{thread_row}\n{item_row}"
        assert pr.summary_scope.carried_over_rows(
            published, thread_row,
            folded_texts=frozenset({"the retry loop is unbounded"}),
        ) == []

    def test_an_unfolded_item_row_with_no_line_is_still_carried(self):
        thread_row = (
            "| [the retry loop is unbounded and will spin forever]"
            "(https://github.com/o/r/pull/1#discussion_r5) | @kgn | "
            "[`a.go:7`](https://github.com/o/r/blob/abc/a.go#L7) | Fixed |")
        other = (
            "| [the logging here is far too chatty](https://github.com/o/r/pull/1"
            "#issuecomment-77) | @kgn | `a.go` | contested |")
        published = f"{thread_row}\n{other}"
        assert _unmarked(pr.summary_scope.carried_over_rows(
            published, thread_row,
            folded_texts=frozenset({"the retry loop is unbounded"}),
        )) == [other]
