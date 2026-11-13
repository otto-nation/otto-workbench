"""The four ways a PR review ends, and what each reports back.

`resolve` is the whole posting decision. The property worth pinning is not
which prompt is asked but that *every* path returns — a review that ran is a
review to record, whether or not it reached GitHub, and an exit here would
leave the domain unwritten and `pr status` reporting "not checked: review" for
a review that did run.

The second property is `post_session_log`: empty unless something was actually
posted. It is what the summary aggregates posting cost from, so a path that
reported one without posting would inflate the figure, and one that posted
without reporting would drop it.
"""

import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from conftest import FAKE_REPO  # noqa: E402
from core.proc import CmdResult  # noqa: E402
from core.serde import load_file, to_dict  # noqa: E402
from gh.pr_data import PRData  # noqa: E402
from pr.state import PostedAs, PostEvent, PostTracking  # noqa: E402
import review.publish  # noqa: E402
from review.paths import FILENAME_POST_SESSION  # noqa: E402
from review.posting import ReviewEvent  # noqa: E402
import core.prompt
import core.publishing

# Captured before the `posts` fixture replaces `review.publish.post` with a
# recording stub, so the parameter names below still name the real function.
_POST_SIGNATURE = inspect.signature(review.publish.post)


@pytest.fixture
def review_file(tmp_path):
    d = tmp_path / "widget-42"
    d.mkdir()
    f = d / "review.md"
    f.write_text("## Must fix\n- **[M1]** boom\n")
    return f


@pytest.fixture
def posts(monkeypatch):
    """Record every post and publish instead of spawning review-post."""
    calls = []

    def _publish_draft(*a, **kw):
        calls.append(("publish", a, kw))
        return review.publish.DraftPublish.PUBLISHED

    monkeypatch.setattr(review.publish, "post",
                        lambda *a, **kw: calls.append(("post", a, kw)))
    monkeypatch.setattr(review.publish, "publish_draft", _publish_draft)
    return calls


def _answers(monkeypatch, *responses):
    """Answer the confirm prompts in order."""
    queue = list(responses)
    monkeypatch.setattr(core.prompt, "confirm",
                        lambda *a, **kw: queue.pop(0))
    return queue


def _resolve(review_file, **overrides):
    kwargs = dict(no_post=False, auto_post=False,
                  bin_dir=Path("/bin"))
    kwargs.update(overrides)
    return review.publish.resolve(FAKE_REPO, "42", review_file, **kwargs)


def test_no_post_skips_github_and_says_how_to_post_later(review_file, posts):
    """--no-post is an unattended run: no prompts, no post, a hint on the way out."""
    result = _resolve(review_file, no_post=True)

    assert posts == []
    assert result.posted is False
    assert result.post_session_log == "", "nothing was posted, so there is no post log"
    assert any("pr review --post" in h for h in result.hints)


def test_auto_post_posts_without_asking(review_file, posts):
    """--post is the other unattended run, and it reports a post log."""
    result = _resolve(review_file, auto_post=True)

    assert [c[0] for c in posts] == ["post"]
    assert result.posted is True
    assert result.post_session_log == str(review_file.parent / FILENAME_POST_SESSION)
    assert result.hints == (), "an unattended post has nothing to advise"


def test_auto_post_posts_a_draft(review_file, posts):
    """A full-run auto-post is always a draft; --submit is gone."""
    result = _resolve(review_file, auto_post=True)

    bound = _POST_SIGNATURE.bind(*posts[0][1], **posts[0][2])
    assert "submit" not in bound.arguments
    assert result.published is False


def test_auto_post_passes_branch_to_review_post(review_file, posts):
    """The branch running the review reaches review-post as its own arg.

    review-post turns this into --expect-ref, which is what tells two
    concurrent runs against the same PR apart. A caller that swallowed the
    branch here would leave the automatic post path unprotected even though
    `pr review --post`'s own call site (ai/bin/pr) passes it.
    """
    result = _resolve(review_file, auto_post=True, branch="alice/feat/thing")

    bound = _POST_SIGNATURE.bind(*posts[0][1], **posts[0][2])
    assert bound.arguments["branch"] == "alice/feat/thing"
    assert result.posted is True


def test_interactive_post_passes_branch_to_review_post(review_file, posts, monkeypatch):
    """The branch also reaches review-post on the confirm-then-post path."""
    _answers(monkeypatch, True, True, False)

    _resolve(review_file, branch="alice/feat/thing")

    bound = _POST_SIGNATURE.bind(*posts[0][1], **posts[0][2])
    assert bound.arguments["branch"] == "alice/feat/thing"


def test_an_unsatisfying_review_is_not_posted_but_is_still_reported(
    review_file, posts, monkeypatch,
):
    """Declining at the first prompt returns; it does not exit.

    The hints tell the operator to edit and post it themselves, which only
    makes sense if the run continues far enough to record what happened.
    """
    _answers(monkeypatch, False)

    result = _resolve(review_file)

    assert posts == []
    assert result.posted is False
    assert result.post_session_log == ""
    hint = next(h for h in result.hints if "$EDITOR" in h)
    assert hint.endswith("review.json")


def test_declining_the_post_prompt_returns_the_same_way(review_file, posts, monkeypatch):
    """Satisfied with the review, but not posting it now."""
    _answers(monkeypatch, True, False)

    result = _resolve(review_file)

    assert posts == []
    assert result.posted is False
    assert result.post_session_log == "", (
        "nothing was posted, so there is no posting cost to aggregate"
    )
    assert any("pr review --post" in h for h in result.hints)


@pytest.mark.parametrize("kwargs,answers", [
    ({"no_post": True}, ()),
    ({}, (False,)),
    ({}, (True, False)),
])
def test_a_path_that_does_not_post_reports_no_post_log(
    review_file, posts, monkeypatch, kwargs, answers,
):
    """The field's only job, asserted on every path that declines.

    `print_summary` aggregates posting cost from this log, so a declining path
    that named one anyway would report the cost of a post that never happened.
    A mutation returning `posted_log` here survived the rest of this file.
    """
    if answers:
        _answers(monkeypatch, *answers)

    result = _resolve(review_file, **kwargs)

    assert posts == []
    assert result.post_session_log == ""


def test_accepting_both_prompts_posts_and_publishes_as_comment(
    review_file, posts, monkeypatch,
):
    """The full interactive path: satisfied, post, publish as a comment."""
    _answers(monkeypatch, True, True, True)

    result = _resolve(review_file)

    assert [c[0] for c in posts] == ["post", "publish"]
    assert posts[1][1][3] is review.publish.ReviewEvent.COMMENT
    assert result.posted is True
    assert result.published is True
    assert result.post_session_log == str(review_file.parent / FILENAME_POST_SESSION)


def test_posting_without_submitting_still_reports_the_post_log(
    review_file, posts, monkeypatch,
):
    """A pending review is posted; the cost of posting it still counts."""
    _answers(monkeypatch, True, True, False)

    result = _resolve(review_file)

    assert [c[0] for c in posts] == ["post"]
    assert result.posted is True
    assert result.published is False
    assert result.post_session_log == str(review_file.parent / FILENAME_POST_SESSION)


@pytest.mark.parametrize("kwargs,answers", [
    ({"no_post": True}, ()),
    ({"auto_post": True}, ()),
    ({}, (False,)),
    ({}, (True, False)),
    ({}, (True, True, False)),
    ({}, (True, True, True)),
])
def test_every_path_returns_rather_than_exiting(
    review_file, posts, monkeypatch, kwargs, answers,
):
    """The property the four endings share, asserted across all six routes.

    A review that ran is a review to record. An exit on any of these paths
    would skip the domain write that follows, so `pr fix` would later skip the
    review pass for findings that exist.
    """
    if answers:
        _answers(monkeypatch, *answers)

    result = _resolve(review_file, **kwargs)

    assert isinstance(result, review.publish.PostResult)


def _write_tracking(review_dir, **fields):
    data = dict(
        posted_as=PostedAs.REVIEW.value,
        status=PostEvent.PENDING.value,
        review_id=7,
        submitted=False,
        head_sha_at_post="aaa1111bbb2222",
        commit_id="aaa1111bbb2222",
    )
    data.update(fields)
    tracking = PostTracking(**data)
    (review_dir / FILENAME_POST_SESSION).write_text(
        json.dumps(to_dict(tracking)) + "\n")
    return tracking


def _pending_pr_data(head_sha="aaa1111bbb2222", review_id=7):
    reviews = []
    if review_id is not None:
        reviews = [{"state": "PENDING", "databaseId": review_id}]
    return PRData(
        viewer_login="bot", head_sha=head_sha,
        head_ref="feat", base_ref="main", reviews=reviews,
    )


def _publish(review_dir, monkeypatch, *, event=ReviewEvent.COMMENT,
             pr_data=None, api=None):
    monkeypatch.setattr(
        review.publish, "fetch_pr_data",
        lambda *a, **k: pr_data or _pending_pr_data(),
    )
    calls = []

    def _api(endpoint, **kw):
        calls.append((endpoint, kw))
        if api is not None:
            return api(endpoint, **kw)
        return CmdResult(0, "{}")

    monkeypatch.setattr(review.publish.gh.client, "api", _api)
    outcome = review.publish.publish_draft(
        "owner/repo", "1", str(review_dir / "review.md"), event,
    )
    return outcome, calls


@pytest.mark.parametrize(
    "content",
    [b"[]", b"{", b"\xff\xfe\x00bad"],
    ids=["non-dict", "truncated-json", "bad-encoding"],
)
def test_unreadable_tracking_is_no_draft_and_calls_no_api(
    tmp_path, monkeypatch, content,
):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / FILENAME_POST_SESSION).write_bytes(content)
    called = []
    monkeypatch.setattr(
        review.publish, "fetch_pr_data",
        lambda *a, **k: called.append("fetch") or _pending_pr_data(),
    )
    monkeypatch.setattr(
        review.publish.gh.client, "api",
        lambda *a, **k: called.append("api") or CmdResult(0, "{}"),
    )

    outcome = review.publish.publish_draft(
        "owner/repo", "1", str(review_dir / "review.md"), ReviewEvent.COMMENT,
    )

    assert outcome is review.publish.DraftPublish.NO_DRAFT
    assert called == []


def test_missing_tracking_is_no_draft(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    called = []
    monkeypatch.setattr(
        review.publish.gh.client, "api",
        lambda *a, **k: called.append("api") or CmdResult(0, "{}"),
    )

    outcome = review.publish.publish_draft(
        "owner/repo", "1", str(review_dir / "review.md"), ReviewEvent.COMMENT,
    )

    assert outcome is review.publish.DraftPublish.NO_DRAFT
    assert called == []


def test_already_published_tracking_is_no_draft(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(
        review_dir, status=PostEvent.COMMENT.value, submitted=True,
    )
    called = []
    monkeypatch.setattr(
        review.publish, "fetch_pr_data",
        lambda *a, **k: called.append("fetch") or _pending_pr_data(),
    )

    outcome = review.publish.publish_draft(
        "owner/repo", "1", str(review_dir / "review.md"), ReviewEvent.COMMENT,
    )

    assert outcome is review.publish.DraftPublish.NO_DRAFT
    assert called == []


def test_publish_draft_records_approve(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)

    outcome, calls = _publish(
        review_dir, monkeypatch, event=ReviewEvent.APPROVE,
    )

    assert outcome is review.publish.DraftPublish.PUBLISHED
    assert outcome.ok
    endpoint, kw = calls[0]
    assert endpoint.endswith("/reviews/7/events")
    assert json.loads(kw["input_text"]) == {"event": "APPROVE"}
    assert kw["retry"] is False
    loaded = load_file(PostTracking, review_dir / FILENAME_POST_SESSION)
    assert loaded.status == "approve"
    assert loaded.submitted is True


def test_fetch_failure_is_failed(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)
    monkeypatch.setattr(
        review.publish, "fetch_pr_data",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit(1)),
    )
    called = []
    monkeypatch.setattr(
        review.publish.gh.client, "api",
        lambda *a, **k: called.append("api") or CmdResult(0, "{}"),
    )

    outcome = review.publish.publish_draft(
        "owner/repo", "1", str(review_dir / "review.md"), ReviewEvent.COMMENT,
    )

    assert outcome is review.publish.DraftPublish.FAILED
    assert called == []


def test_events_post_failure_is_failed(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)

    outcome, _ = _publish(
        review_dir, monkeypatch,
        api=lambda *a, **k: CmdResult(1, "", "gh: Unprocessable (HTTP 422)"),
    )

    assert outcome is review.publish.DraftPublish.FAILED
    loaded = load_file(PostTracking, review_dir / FILENAME_POST_SESSION)
    assert loaded.status == PostEvent.PENDING.value
    assert loaded.submitted is False


def test_mismatched_pending_id_is_not_pending(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)

    outcome, calls = _publish(
        review_dir, monkeypatch,
        pr_data=_pending_pr_data(review_id=99),
        api=lambda *a, **k: CmdResult(0, json.dumps({"state": "PENDING"})),
    )

    assert outcome is review.publish.DraftPublish.NOT_PENDING
    assert calls[0][0].endswith("/reviews/7")
    loaded = load_file(PostTracking, review_dir / FILENAME_POST_SESSION)
    assert loaded.status == PostEvent.PENDING.value


def test_not_pending_records_a_submitted_github_state(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)

    outcome, _ = _publish(
        review_dir, monkeypatch,
        pr_data=_pending_pr_data(review_id=None),
        api=lambda *a, **k: CmdResult(0, json.dumps({"state": "APPROVED"})),
    )

    assert outcome is review.publish.DraftPublish.NOT_PENDING
    loaded = load_file(PostTracking, review_dir / FILENAME_POST_SESSION)
    assert loaded.status == "approve"
    assert loaded.submitted is True


def test_not_pending_404_leaves_the_record(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir)

    outcome, _ = _publish(
        review_dir, monkeypatch,
        pr_data=_pending_pr_data(review_id=None),
        api=lambda *a, **k: CmdResult(
            1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)",
        ),
    )

    assert outcome is review.publish.DraftPublish.NOT_PENDING
    loaded = load_file(PostTracking, review_dir / FILENAME_POST_SESSION)
    assert loaded.status == PostEvent.PENDING.value
    assert loaded.submitted is False


def test_head_moved_since_the_draft_was_posted(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(review_dir, review_sha="")

    outcome, calls = _publish(
        review_dir, monkeypatch,
        pr_data=_pending_pr_data(head_sha="ccc3333ddd4444"),
    )

    assert outcome is review.publish.DraftPublish.HEAD_MOVED
    assert calls == []


def test_approve_on_review_sha_drift_is_head_moved(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    _write_tracking(
        review_dir,
        head_sha_at_post="ccc3333ddd4444",
        commit_id="ccc3333ddd4444",
        review_sha="aaa1111bbb2222",
    )

    outcome, calls = _publish(
        review_dir, monkeypatch, event=ReviewEvent.APPROVE,
        pr_data=_pending_pr_data(head_sha="ccc3333ddd4444"),
    )

    assert outcome is review.publish.DraftPublish.HEAD_MOVED
    assert calls == []


# ── the argv post() actually builds ─────────────────────────────────────────
#
# The tests above stub `post` out, so they hold what `resolve` hands it and
# nothing about what it does with that. These drive the real `post` and assert
# on the argv, which is where `branch` becomes the flag review-post reads.


def _post_argv(monkeypatch, tmp_path, **kw):
    """Run the real `post` and return the argv it hands review-post."""
    seen = {}

    def _call(handler, argv, **_):
        seen["handler"] = handler
        seen["argv"] = list(argv)
        return 0

    monkeypatch.setattr(core.publishing, "call_entry_point", _call)
    review.publish.post("42", str(tmp_path / "review.md"),
                        bin_dir=tmp_path, **kw)
    assert seen["handler"] == "cli.review_post:main"
    return seen["argv"]


def test_post_turns_a_branch_into_expect_ref(monkeypatch, tmp_path):
    """The flag review-post reads to refuse another run's review.

    Without this the guard is inert on the automatic path: review-post has
    nothing to compare the sidecar against and publishes whatever the
    PR-keyed lookup found.
    """
    argv = _post_argv(monkeypatch, tmp_path, branch="alice/feat/thing")

    assert "--expect-ref" in argv
    assert argv[argv.index("--expect-ref") + 1] == "alice/feat/thing"


def test_post_omits_expect_ref_when_no_branch_is_known(monkeypatch, tmp_path):
    """A caller that cannot name its branch has nothing to claim, and still posts."""
    assert "--expect-ref" not in _post_argv(monkeypatch, tmp_path)
