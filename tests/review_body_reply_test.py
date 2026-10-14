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
import core.publishing
import pr.comments
import pr.state
import review.review_body_reply
from gh.pr_data import PRData

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
def body_file(tmp_path):
    path = tmp_path / "reply.md"
    path.write_text("Tightened — see https://github.com/owner/repo/blob/abc/p.py#L3")
    return str(path)


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


def _run(ctx, body_file, *, post_url="https://github.com/owner/repo/pull/1#issuecomment-5"):
    route = review.review_body_reply.ReplyRoute(pr_data=_pr_data(), source="rb-88")
    with patch.object(pr.comments, "post_issue_comment", return_value=post_url) as post:
        code = review.review_body_reply.run_review_body_reply(ctx, route, body_file)
    return code, post


class TestPublishing:

    def test_the_reply_is_stored_and_published_in_the_summary(self, ctx, body_file):
        with core.publishing.run(post=True):
            code, post = _run(ctx, body_file)
        assert code == 0
        posted = post.call_args[0][2]
        assert "<!-- pr-comments:review-reply k=88 " in posted
        assert "**@kgn** · [review](https://github.com/owner/repo/pull/1#pullrequestreview-88)" \
            in posted
        state = pr.state.load_state(ctx.target_dir)
        assert state.fix.source_replies["rb-88"].startswith("Tightened")
        assert state.fix.summary_deferred is False
        assert state.fix.summary_url.endswith("#issuecomment-5")

    def test_a_post_that_fails_leaves_the_reply_owed(self, ctx, body_file):
        """`--finish --post` then publishes it — the scope still finds it unpublished."""
        with core.publishing.run(post=True):
            code, _post = _run(ctx, body_file, post_url=None)
        assert code == 1
        state = pr.state.load_state(ctx.target_dir)
        assert "rb-88" in state.fix.source_replies
        assert state.fix.summary_deferred is True

    def test_a_draft_stores_nothing_and_prints_the_summary(self, ctx, body_file):
        drafts = []
        route = review.review_body_reply.ReplyRoute(pr_data=_pr_data(), source="rb-88")
        with core.publishing.run(post=False), \
                patch.object(core.publishing, "draft",
                             side_effect=lambda action, body="": drafts.append(body)), \
                patch("gh.client.api") as api:
            code = review.review_body_reply.run_review_body_reply(ctx, route, body_file)
        assert code == 0
        api.assert_not_called()
        assert any("<!-- pr-comments:review-reply k=88 " in body for body in drafts)
        assert pr.state.load_state(ctx.target_dir) is None

    def test_a_missing_body_file_stores_nothing(self, ctx, tmp_path):
        with core.publishing.run(post=True):
            code, post = _run(ctx, str(tmp_path / "nope.md"))
        assert code == 1
        post.assert_not_called()
        assert pr.state.load_state(ctx.target_dir) is None

    def test_reading_the_pr_writes_no_thread_state(self, ctx, body_file):
        """The comments ledger is `--triage`'s to advance, not a reply's."""
        with core.publishing.run(post=True):
            _run(ctx, body_file)
        assert not pr.comments.threads_state_path(ctx.target_dir).exists()
