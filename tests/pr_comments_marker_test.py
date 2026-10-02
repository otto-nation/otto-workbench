"""Tests for pr.comments writes: the marker-comment upsert and the PATCH endpoints."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.comments
from core.proc import CmdResult
import core.log
import gh.client


REPO = "owner/repo"


# ── post_issue_comment upsert ──────────────────────────────────────────────


MARKER = "<!-- pr-comments:summary -->"


def _posted(url: str = "u") -> CmdResult:
    """What `_gh_post` answers a successful write with."""
    return CmdResult(returncode=0, stdout=json.dumps({"html_url": url}))


def test_post_issue_comment_posts_new_without_marker():
    with patch.object(pr.comments, "_gh_post", return_value=_posted()) as post, \
         patch.object(pr.comments, "find_marker_comment",
                      autospec=True) as find:
        url = pr.comments.post_issue_comment(REPO, 1, "body")
    assert url == "u"
    post.assert_called_once()
    find.assert_not_called()


def _pages(*pages):
    """Decoded gh api --paginate --slurp output: an outer array of pages."""
    return list(pages)


def _listing(value):
    return patch.object(gh.client, "api_json", return_value=value)


def test_post_issue_comment_edits_existing_marked_comment():
    """A second round must update the first round's summary, not append to it."""
    listing = _pages([
        {"id": 10, "body": "unrelated"},
        {"id": 11, "body": f"{MARKER}\nround one"},
    ])
    with _listing(listing), \
         patch.object(pr.comments, "_patch_issue_comment", return_value="u2") as patch_fn, \
         patch.object(pr.comments, "_gh_post") as post:
        url = pr.comments.post_issue_comment(REPO, 1, "round two", marker=MARKER)
    assert url == "u2"
    patch_fn.assert_called_once_with(REPO, 11, "round two")
    post.assert_not_called()


def test_post_issue_comment_posts_new_when_marker_absent():
    listing = _pages([{"id": 10, "body": "unrelated"}])
    with _listing(listing), \
         patch.object(pr.comments, "_gh_post", return_value=_posted()) as post:
        url = pr.comments.post_issue_comment(REPO, 1, "body", marker=MARKER)
    assert url == "u"
    post.assert_called_once()


def _found(comment_id, body):
    return pr.comments.MarkerComment(True, comment_id, body)


def test_find_marker_comment_prefers_latest():
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nold"},
        {"id": 11, "body": f"{MARKER}\nnew"},
    ])
    with _listing(listing):
        found = pr.comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(11, f"{MARKER}\nnew")


def test_find_marker_comment_spans_pages():
    """The marker comment is posted first, so on a busy PR it is not on page one."""
    listing = _pages(
        [{"id": 10, "body": f"{MARKER}\nround one"}],
        [{"id": 11, "body": "unrelated"}],
    )
    with _listing(listing):
        found = pr.comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(10, f"{MARKER}\nround one")


def test_find_marker_comment_carries_the_timeline():
    """The upsert has to know whether anyone spoke below the summary."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": "not so fast", "created_at": "2026-01-02T00:00:00Z"},
    ])
    with _listing(listing):
        found = pr.comments.find_marker_comment(REPO, 1, MARKER)
    assert found.created_at == "2026-01-01T00:00:00Z"
    assert found.newest_other_at == "2026-01-02T00:00:00Z"


def test_find_marker_comment_does_not_read_an_older_summary_as_an_answer():
    """A superseded summary is ours; reading it as a reply reposts forever."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": f"{MARKER}\nround two", "created_at": "2026-01-03T00:00:00Z"},
    ])
    with _listing(listing):
        found = pr.comments.find_marker_comment(REPO, 1, MARKER)
    assert found.comment_id == 11
    assert found.newest_other_at == ""


def test_find_marker_comment_accepts_flat_listing():
    """A single unslurped page must still be readable."""
    listing = [{"id": 12, "body": f"{MARKER}\nonly"}]
    with _listing(listing):
        found = pr.comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(12, f"{MARKER}\nonly")


@pytest.mark.parametrize("payload", [
    None,
    {"message": "Not Found"},
])
def test_find_marker_comment_reports_lookup_failure(payload):
    """A failed listing must be distinguishable from an empty one.

    A caller reconciling against the published body reads an empty `body` as
    "the comment said nothing", so `found` has to carry the difference.
    """
    with _listing(payload):
        assert pr.comments.find_marker_comment(REPO, 1, MARKER) == \
            pr.comments.MarkerComment(found=False)


def test_find_marker_comment_reports_empty_listing():
    with _listing([]):
        assert pr.comments.find_marker_comment(REPO, 1, MARKER) == \
            pr.comments.MarkerComment(found=True)


def test_find_marker_comments_keeps_every_round_oldest_first():
    """A caller spreading its record across comments has to link the earlier ones."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one"},
        {"id": 11, "body": "unrelated"},
        {"id": 12, "body": f"{MARKER}\nround two"},
    ])
    with _listing(listing):
        history = pr.comments.find_marker_comments(REPO, 1, MARKER)
    assert [c.comment_id for c in history.comments] == [10, 12]
    assert history.bodies == [f"{MARKER}\nround one", f"{MARKER}\nround two"]
    assert history.newest.comment_id == 12


def test_find_marker_comments_carries_each_comments_url():
    """The footer chain links comments, so the listing's link is the only source."""
    listing = _pages([
        {"id": 10, "body": MARKER, "html_url": "https://gh/pull/1#issuecomment-10"},
    ])
    with _listing(listing):
        history = pr.comments.find_marker_comments(REPO, 1, MARKER)
    assert history.comments[0].url == "https://gh/pull/1#issuecomment-10"


def test_find_marker_comments_dates_the_body_as_well_as_the_comment():
    """A summary edited in place holds rows for surfaces newer than its post
    time, so a caller asking what the record covers needs the edit time too."""
    listing = _pages([
        {"id": 10, "body": MARKER,
         "created_at": "2026-01-02T00:00:00Z",
         "updated_at": "2026-01-06T00:00:00Z"},
    ])
    with _listing(listing):
        history = pr.comments.find_marker_comments(REPO, 1, MARKER)
    assert history.comments[0].created_at == "2026-01-02T00:00:00Z"
    assert history.comments[0].updated_at == "2026-01-06T00:00:00Z"


def test_find_marker_comments_reports_an_unread_listing_as_no_history():
    """`found` distinguishes it from a PR that genuinely has no summary yet."""
    with _listing(None):
        history = pr.comments.find_marker_comments(REPO, 1, MARKER)
    assert history == pr.comments.MarkerHistory(found=False)
    assert history.newest == pr.comments.MarkerComment(found=False)


def test_marker_history_newest_stands_in_for_an_unmarked_pr():
    """The upsert target of a PR with no summary is an empty comment, not None."""
    history = pr.comments.MarkerHistory(found=True, newest_other_at="2026-01-02T00:00:00Z")
    assert history.newest == pr.comments.MarkerComment(
        found=True, newest_other_at="2026-01-02T00:00:00Z")
    assert history.bodies == []


def test_post_issue_comment_reuses_a_supplied_lookup():
    """A caller that already read the comment must not pay for the listing twice."""
    with patch.object(gh.client, "api_json") as listing, \
         patch.object(pr.comments, "_patch_issue_comment", return_value="u2") as patch_fn:
        url = pr.comments.post_issue_comment(
            REPO, 1, "round two", marker=MARKER,
            existing=_found(11, f"{MARKER}\nround one"),
        )
    assert url == "u2"
    listing.assert_not_called()
    patch_fn.assert_called_once_with(REPO, 11, "round two")


def test_post_issue_comment_logs_when_lookup_fails():
    """Falling back to a new comment on lookup failure must not be silent."""
    with _listing(None), \
         patch.object(pr.comments, "_gh_post", return_value=_posted()), \
         patch.object(core.log, "error") as err:
        url = pr.comments.post_issue_comment(REPO, 1, "body", marker=MARKER)
    assert url == "u"
    err.assert_called_once()


def test_patch_issue_comment_uses_patch_method():
    with patch.object(pr.comments, "_gh_post", return_value=_posted()) as post:
        assert pr.comments._patch_issue_comment(REPO, 11, "body") == "u"
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/issues/comments/11"


def test_patch_thread_reply_uses_patch_method():
    with patch.object(pr.comments, "_gh_post", return_value=CmdResult(0)) as post:
        assert pr.comments.patch_thread_reply(REPO, 99, "body") is True
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/pulls/comments/99"


def test_patch_thread_reply_reports_failure():
    with patch.object(pr.comments, "_gh_post", return_value=CmdResult(1)):
        assert pr.comments.patch_thread_reply(REPO, 99, "body") is False


def test_update_pr_body_patches_the_pull_endpoint():
    with patch.object(pr.comments, "_gh_post", return_value=CmdResult(0)) as post:
        assert pr.comments.update_pr_body(REPO, 7, "new body") is True
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/pulls/7"
    assert post.call_args[0][1] == "new body"


def test_update_pr_body_reports_failure():
    with patch.object(pr.comments, "_gh_post", return_value=CmdResult(1)):
        assert pr.comments.update_pr_body(REPO, 7, "new body") is False
