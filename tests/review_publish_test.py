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
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review import publish as review_publish  # noqa: E402
from review.paths import FILENAME_POST_SESSION  # noqa: E402

# Captured before the `posts` fixture replaces `review_publish.post` with a
# recording stub, so the parameter names below still name the real function.
_POST_SIGNATURE = inspect.signature(review_publish.post)


@pytest.fixture
def review_file(tmp_path):
    d = tmp_path / "widget-42"
    d.mkdir()
    f = d / "review.md"
    f.write_text("## Must fix\n- **[M1]** boom\n")
    return f


@pytest.fixture
def posts(monkeypatch):
    """Record every post and submit instead of spawning review-post."""
    calls = []

    def _submit_pending(*a, **kw):
        calls.append(("submit", a, kw))
        return True

    monkeypatch.setattr(review_publish, "post",
                        lambda *a, **kw: calls.append(("post", a, kw)))
    monkeypatch.setattr(review_publish, "submit_pending", _submit_pending)
    return calls


def _answers(monkeypatch, *responses):
    """Answer the confirm prompts in order."""
    queue = list(responses)
    monkeypatch.setattr(review_publish.prompt, "confirm",
                        lambda *a, **kw: queue.pop(0))
    return queue


def _resolve(review_file, **overrides):
    kwargs = dict(no_post=False, auto_post=False, auto_submit=False,
                  bin_dir=Path("/bin"))
    kwargs.update(overrides)
    return review_publish.resolve("acme/widget", "42", review_file, **kwargs)


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


def test_auto_submit_rides_along_with_auto_post(review_file, posts):
    """--submit is passed to review-post rather than submitted separately."""
    result = _resolve(review_file, auto_post=True, auto_submit=True)

    bound = _POST_SIGNATURE.bind(*posts[0][1], **posts[0][2])
    assert bound.arguments["submit"] is True, "the submit flag reaches review-post"
    assert result.submitted is True


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
    assert any("$EDITOR" in h for h in result.hints)


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


def test_accepting_both_prompts_posts_and_submits(review_file, posts, monkeypatch):
    """The full interactive path: satisfied, post, submit."""
    _answers(monkeypatch, True, True, True)

    result = _resolve(review_file)

    assert [c[0] for c in posts] == ["post", "submit"]
    assert result.posted is True
    assert result.submitted is True
    assert result.post_session_log == str(review_file.parent / FILENAME_POST_SESSION)


def test_posting_without_submitting_still_reports_the_post_log(
    review_file, posts, monkeypatch,
):
    """A pending review is posted; the cost of posting it still counts."""
    _answers(monkeypatch, True, True, False)

    result = _resolve(review_file)

    assert [c[0] for c in posts] == ["post"]
    assert result.posted is True
    assert result.submitted is False
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

    assert isinstance(result, review_publish.PostResult)


@pytest.mark.parametrize(
    "content",
    [b"[]", b"{", b"\xff\xfe\x00bad"],
    ids=["non-dict", "truncated-json", "bad-encoding"],
)
def test_submit_pending_survives_an_unreadable_post_tracking_file(
    tmp_path, capsys, content,
):
    """Every way the file can be unusable falls through to "no tracking".

    This reads through `serde.load_file` rather than its own read/parse/except
    precisely so the three cases cannot drift apart: a bare `[]` from a killed
    write, truncated JSON, and a byte sequence that is not valid UTF-8 (which
    `read_text` raises `UnicodeDecodeError` for) all have to degrade the same
    way, not just the ones a hand-listed exception tuple happened to name.
    """
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / FILENAME_POST_SESSION).write_bytes(content)

    review_publish.submit_pending("owner/repo", "1", str(review_dir / "review.md"))

    assert "Could not read review_id" in capsys.readouterr().err
