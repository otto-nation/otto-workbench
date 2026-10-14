"""review.review_body_reply: answering a review body from `pr comments --reply`.

The answer cannot go where the review is — GitHub has no reply API for a review
body — so it is stored per review and published in the summary comment. These
cover the routing that keeps a thread target on its old path, and the publish:
what is stored, when, and what is left owed when the post does not happen.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
from conftest import make_ctx
import core.log
import core.publishing
import cli.review_threads
import core.run_lock
import pr.comments
import pr.context
import pr.state
import review.review_body_reply
from pr.fix import ItemOutcome
from gh.pr_data import PRData
from git.land import CommitStatus

_REVIEW = {
    "databaseId": 88, "author": {"login": "kgn"}, "state": "COMMENTED",
    "body": "Please tighten the parser.", "submittedAt": "2026-01-02T00:00:00Z",
}
_THREAD = {
    "id": "PRRT_abc", "isResolved": False, "path": "a.py", "line": 3,
    "comments": {"totalCount": 1, "nodes": [{
        "id": "c1", "databaseId": 111, "author": {"login": "kgn"}, "body": "nit",
        "createdAt": "2026-01-01T00:00:00Z",
    }]},
}


def _pr_data():
    return PRData(viewer_login="me", head_sha="abc1234", head_ref="b", base_ref="main",
                  reviews=[_REVIEW], review_threads=[_THREAD])


@pytest.fixture
def ctx(tmp_path):
    work = tmp_path / "wt"
    (work / ".git").mkdir(parents=True)
    return make_ctx(repo="owner/repo", pr_number=1, worktree_root=work,
                    head_sha="abc1234", target_dir=tmp_path / "target")


@pytest.fixture
def reply_body():
    return "Tightened — see https://github.com/owner/repo/blob/abc/p.py#L3"


class TestRouting:
    """A thread wins; a review body is recognised in each of its spellings."""

    def _route(self, ctx, target):
        with patch.object(review.review_body_reply, "fetch_pr_data", return_value=_pr_data()):
            return review.review_body_reply.route_reply(ctx, target)

    def test_a_comment_inside_a_thread_is_still_a_thread(self, ctx):
        route = self._route(ctx, "111")
        assert route.thread
        assert route.source == ""

    @pytest.mark.parametrize("target", ["88", "rb-88", "rb-88-2"])
    def test_a_review_body_is_named_by_its_source_key(self, ctx, target):
        assert self._route(ctx, target).source == "rb-88"

    def test_a_target_naming_nothing_lists_the_review_bodies(self, ctx):
        route = self._route(ctx, "404")
        assert (route.thread, route.source) == (False, "")
        assert route.review_body_ids == ("88",)

    def test_a_mistyped_review_id_is_refused_and_nothing_is_stored(
        self, ctx, tmp_path, capsys,
    ):
        """A stored reply publishes permanently, and nothing retracts one."""
        (tmp_path / "f").write_text("Done.")
        with patch.object(pr.context, "resolve", return_value=ctx), \
                patch.object(review.review_body_reply, "fetch_pr_data",
                             return_value=_pr_data()), \
                patch.object(core.run_lock, "claim_for_process"), \
                patch.object(pr.comments, "post_issue_comment", return_value="u") as post:
            code = cli.review_threads.main(
                ["--reply", "rb-404", "--body-file", str(tmp_path / "f"), "--post"])
        assert code == 1
        post.assert_not_called()
        assert pr.state.load_state(ctx.target_dir) is None
        assert "review bodies on PR #1: rb-88" in capsys.readouterr().err

    def test_a_triaged_review_outside_the_fetch_window_is_accepted(self, ctx):
        """The snapshot holds an item cut from it, so the review is real."""
        state = pr.state.load_or_init(
            target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
            pr_number=ctx.pr_number, head_sha=ctx.head_sha,
            worktree_root=str(ctx.require_worktree()))
        state.fix.fix.items.append(ItemOutcome(id="rb-404-1", summary="Old point"))
        pr.state.save_state(ctx.target_dir, state)
        assert self._route(ctx, "rb-404").source == "rb-404"


def _run(ctx, reply_body, *, post_url="https://github.com/owner/repo/pull/1#issuecomment-5"):
    route = review.review_body_reply.ReplyRoute(pr_data=_pr_data(), source="rb-88")
    with patch.object(pr.comments, "post_issue_comment", return_value=post_url) as post:
        code = review.review_body_reply.run_review_body_reply(ctx, route, reply_body)
    return code, post


def _seed(ctx, **fix):
    """A state file as an earlier round left it, with `fix` fields set on it."""
    state = pr.state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=ctx.head_sha,
        worktree_root=str(ctx.require_worktree()),
    )
    for name, value in fix.items():
        target = state.fix.fix if name.startswith("commit_") else state.fix
        setattr(target, name, value)
    pr.state.save_state(ctx.target_dir, state)


class TestPublishing:

    def test_the_reply_is_stored_and_published_in_the_summary(self, ctx, reply_body):
        with core.publishing.run(post=True):
            code, post = _run(ctx, reply_body)
        assert code == 0
        posted = post.call_args[0][2]
        assert "<!-- pr-comments:review-reply k=88 " in posted
        assert "**@kgn** · [review](https://github.com/owner/repo/pull/1#pullrequestreview-88)" \
            in posted
        state = pr.state.load_state(ctx.target_dir)
        assert state.fix.source_replies["rb-88"].startswith("Tightened")
        assert state.fix.summary_deferred is False
        assert state.fix.summary_url.endswith("#issuecomment-5")

    def test_a_reply_citing_no_line_of_code_is_warned_about(self, ctx):
        with core.publishing.run(post=True), patch.object(core.log, "warn") as warn:
            _run(ctx, "Tightened it.")
        assert any("cites no permalink" in c[0][0] for c in warn.call_args_list)

    def test_a_cited_reply_draws_no_warning(self, ctx, reply_body):
        with core.publishing.run(post=True), patch.object(core.log, "warn") as warn:
            _run(ctx, reply_body)
        assert not any("cites no permalink" in c[0][0] for c in warn.call_args_list)

    def test_a_post_that_fails_leaves_the_reply_owed(self, ctx, reply_body):
        """`--finish --post` then publishes it — the scope still finds it unpublished."""
        with core.publishing.run(post=True):
            code, _post = _run(ctx, reply_body, post_url=None)
        assert code == 1
        state = pr.state.load_state(ctx.target_dir)
        assert "rb-88" in state.fix.source_replies
        assert state.fix.summary_deferred is True

    def test_a_draft_stores_nothing_and_prints_the_summary(self, ctx, reply_body):
        drafts = []
        route = review.review_body_reply.ReplyRoute(pr_data=_pr_data(), source="rb-88")
        with core.publishing.run(post=False), \
                patch.object(core.publishing, "draft",
                             side_effect=lambda action, body="": drafts.append(body)), \
                patch("gh.client.api") as api:
            code = review.review_body_reply.run_review_body_reply(ctx, route, reply_body)
        assert code == 0
        api.assert_not_called()
        assert any("<!-- pr-comments:review-reply k=88 " in body for body in drafts)
        assert pr.state.load_state(ctx.target_dir) is None

    def test_reading_the_pr_writes_no_thread_state(self, ctx, reply_body):
        """The comments ledger is `--triage`'s to advance, not a reply's."""
        with core.publishing.run(post=True):
            _run(ctx, reply_body)
        assert not pr.comments.threads_state_path(ctx.target_dir).exists()


class TestOwedSummary:
    """The post retires the debt the reply created, never debt `--finish` holds."""

    @pytest.mark.parametrize("owed", [True, False])
    def test_a_published_reply_leaves_the_summary_debt_as_it_found_it(
            self, ctx, reply_body, owed):
        """Held back for `--finish`, it stays owed — that re-render writes the issue link."""
        _seed(ctx, summary_deferred=owed)
        with core.publishing.run(post=True):
            code, post = _run(ctx, reply_body)
        assert code == 0
        post.assert_called_once()
        state = pr.state.load_state(ctx.target_dir)
        assert state.fix.summary_deferred is owed
        assert state.fix.summary_url.endswith("#issuecomment-5")

    def test_storing_a_reply_stamps_the_fix_domain(self, ctx, reply_body):
        _seed(ctx, updated_at="2000-01-01T00:00:00Z")
        with core.publishing.run(post=True):
            _run(ctx, reply_body)
        assert pr.state.load_state(ctx.target_dir).fix.updated_at > "2000-01-01T00:00:00Z"


class TestDraftOutput:
    """One headline per draft, and a pending push says why there is no body."""

    def _draft(self, ctx, reply_body):
        drafts, infos = [], []
        route = review.review_body_reply.ReplyRoute(pr_data=_pr_data(), source="rb-88")
        with core.publishing.run(post=False), \
                patch.object(core.publishing, "draft",
                             side_effect=lambda action, body="": drafts.append(body)), \
                patch.object(core.log, "info", side_effect=infos.append):
            code = review.review_body_reply.run_review_body_reply(ctx, route, reply_body)
        assert code == 0
        return drafts, infos

    def test_a_draft_prints_one_headline(self, ctx, reply_body):
        drafts, _infos = self._draft(ctx, reply_body)
        assert len(drafts) == 1
        assert "k=88 " in drafts[0]

    def test_a_pending_push_says_no_body_could_be_rendered(self, ctx, reply_body):
        _seed(ctx, commit_sha="abc1234", commit_status=CommitStatus.PUSH_HELD)
        with patch("git.push.holds", return_value=False):
            drafts, infos = self._draft(ctx, reply_body)
        assert drafts == []
        assert any("No draft body" in line and "push is still pending" in line
                   for line in infos)
