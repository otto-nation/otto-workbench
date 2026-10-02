"""Helpers shared by the suites split out of the former review_threads_test.py.

Only what two or more of those suites use lives here; a helper one suite uses
moved with it. `_no_published_summary` is autouse and every one of them imports
it, so each suite still starts from a PR with no summary comment, as the single
file did.
"""

import atexit
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx
import fix.comments
import core.proc
from pr.comments_state import ThreadState
from git.land import CommitStatus
import pr.comments
from pr.triage_round import TriagedRound
import pr.published_record
import pr.summary_model
import pr.summary_render
from pr.comments_fix import FixSummary
from pr.fix import FixOutcome, FixRecord
from pr.state import PRIdentity, PRState
from pr.thread_models import ClassificationResult, CommentItem, PRReport, ReplyOutcome, ReportThread


def _fix(
    items=(), *, commit_sha="", commit_status=None, head_sha="", **kwargs,
) -> FixSummary:
    """A comment fix pass carrying these outcomes, as the domain stores them.

    The record's envelope fields stay keywords here rather than a nested
    `FixRecord` literal — a test that names a commit is making a point about the
    commit, not about which of the two objects holds it. `commit_status` takes
    the string a state file holds and coerces it, so a typo fails here instead
    of reaching a renderer as an unrecognised status.
    """
    return FixSummary(
        fix=FixRecord(
            items=list(items), commit_sha=commit_sha,
            commit_status=CommitStatus(commit_status) if commit_status else None,
            head_sha=head_sha,
        ),
        **kwargs,
    )


@pytest.fixture(scope="session")
def content():
    """Build a `RoundContent` from the buckets a test names, and no others.

    Keyed by the outcome's own spelling, so a bucket a test does not mention is
    absent from the mapping rather than present and empty — matching how the
    round reaches the renderers in production. A helper that wrote every key
    in, mentioned or not, would hide that distinction from these tests.
    """
    def _make(**buckets):
        comments = {
            k: list(buckets.pop(k, ()))
            for k in ("issue_comments", "review_body_comments")
        }
        return pr.summary_model.RoundContent(
            by_outcome={
                FixOutcome(name): list(entries)
                for name, entries in buckets.items()
            },
            **comments,
        )
    return _make


def _lookup_returns(*comments):
    """Patch the marker lookup to report `comments`, oldest first.

    Both the autouse default and the per-test override go through this one
    patch, so a test entering `_published(...)` inside the fixture's patch is
    plain `patch.object` nesting: the inner patch wins for its block and
    restores the fixture's on exit.

    A `MarkerComment` with no id is the "PR has no summary yet" stand-in rather
    than a comment, so it contributes the lookup's own outcome and no history.
    """
    import pr.comments
    newest = comments[-1]
    history = pr.comments.MarkerHistory(
        found=newest.found,
        comments=tuple(c for c in comments if c.comment_id),
        newest_other_at=newest.newest_other_at,
    )
    return patch.object(pr.comments, "find_marker_comments", return_value=history)


@pytest.fixture(autouse=True)
def _no_published_summary():
    """Start every test from a PR with no summary comment yet.

    Every summary upsert reads the published comment first, so without this the
    suite would shell out to `gh api`. Tests covering the carry-forward stub it
    with a body of their own.
    """
    import pr.comments
    with _lookup_returns(pr.comments.MarkerComment(found=True)):
        yield


def _published(body: str):
    """Stub a prior summary comment with the given body."""
    import pr.comments
    return _lookup_returns(pr.comments.MarkerComment(
        True, 11, body, url="https://github.com/owner/repo/pull/1#issuecomment-11"))


# ── Helpers ──────────────────────────────────────────────────────────────────

# Stand-in SHAs for the attribution tests. Per-round attribution is only
# testable when each round has a distinguishable one, and an assertion reads as
# a claim about the round rather than about seven digits.
_ROUND_1_SHA = "1111111"


_PASS_SHA = "9999999"


def _fake_ctx(tmp_path, **overrides):
    """A stand-in for `ResolvedContext` in the fix-pass drivers below.

    One helper rather than the same literal at eight call sites: the fields a
    fix pass reads off its context grow, and a `SimpleNamespace` answers a
    field it was never given with `AttributeError` rather than a default. Eight
    copies means eight failures every time one is added, in tests that are not
    about the new field.

    `host` empty is public GitHub, which is what these drivers assert against.
    """
    fields = {
        "repo": "owner/repo", "branch": "b", "pr_number": 1,
        "head_sha": "aaa1111", "target_dir": tmp_path, "host": "",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _git_ran(returncode, stdout="", stderr=""):
    """What a stubbed `git.client.run` hands back.

    `out`, `ok`, `lines` and `head_sha` are all `run` underneath, so patching
    the one call covers every read the script makes.
    """
    return core.proc.CmdResult(returncode, stdout, stderr)


def _answering_the_owner(mock_run, sha="abc1234", touched=("f.go",)):
    """Wrap a `git.client.run` stub so the owner's own reads are answered.

    The commit scope is a dirty-set snapshot taken on either side of the agent,
    and a catch-all stub answers both readings identically — an empty
    difference, so the pass commits nothing and every assertion about the commit
    fails for a reason the test never set up. ``touched`` is what the agent is
    taken to have changed: absent from the first reading and present in every
    reading after it.

    Answered by that shape rather than by a list of one reply per read. The
    engine reads the worktree once per batch as well as once per pass, so a
    fixed-length list encodes how many invocations the pass makes — and when it
    runs short, the exhausted iterator's default reads as a worktree where the
    agent changed nothing, failing the commit for a reason no test wrote.

    Every push here goes through `push.push`, which finishes by asking the
    remote what it holds. A stub's catch-all answers that with the empty string
    — a branch the remote does not have — so every push would read as lost
    whatever the test was setting up, and the owner would retry it. Passing the
    SHA the stub commits makes the push land; passing a different one is how a
    test asks for the lost path.

    The answer echoes back the refname the owner asked for. It compares the
    refname it reads against the one it queried — a fixed one here would be
    discarded as somebody else's branch, and every push would read as lost for
    a reason that has nothing to do with what the test set up.
    """
    baseline = iter(("",))
    after = "\n".join(touched)

    def run(*cmd, **kwargs):
        if cmd[:1] == ("ls-remote",):
            return _git_ran(0, stdout=f"{sha}\t{cmd[-1]}\n" if sha else "")
        if cmd[:2] == ("diff", "HEAD"):
            return _git_ran(0, stdout=next(baseline, after))
        if cmd[:2] == ("ls-files", "--others"):
            return _git_ran(0, stdout="")
        return mock_run(*cmd, **kwargs)
    return run


def _tick_every_fix(wt_path):
    """An `invoke_fix` stub that answers every entry `fixed`.

    `fix.engine` rewrites the checklist immediately before each invocation, so
    an agent that answers anything has to answer it from inside the call —
    a file ticked beforehand is overwritten before the agent ever sees it.
    """
    tracking = Path(wt_path) / "pr-comments" / "fix-tracking.md"

    def invoke(_invocation):
        tracking.write_text(tracking.read_text().replace("- [ ] fixed", "- [x] fixed"))
        return 0
    return invoke


def _triaged_round(*, fixable=(), fixable_items=(), needs_human=(), dismissed=(),
                   already_addressed=(), replies=None, has_unaccounted=False):
    """A `TriagedRound` from the buckets a test names, with the rest empty.

    The thread side takes everything except `fixable_items`, which is what the
    round's own properties then merge — a test naming `dismissed` is making a
    point about a dismissal, not about which side it arrived on.
    """
    return TriagedRound(
        threads=ClassificationResult(
            fixable=list(fixable), needs_human=list(needs_human),
            dismissed=list(dismissed), already_addressed=list(already_addressed),
        ),
        items=ClassificationResult(fixable=list(fixable_items)),
        replies=replies or ReplyOutcome(),
        has_unaccounted=has_unaccounted,
    )


def _fix_adapter(wt_path, **overrides):
    """A CommentFixAdapter over an otherwise empty pass.

    Every bucket defaults to empty so a test names only the one it is about.
    """
    report = overrides.pop("report", None) or PRReport(repo="owner/repo", pr_number=1)
    ctx = overrides.pop("ctx", None) or make_ctx(
        repo="owner/repo", pr_number=1, worktree_root=wt_path, target_dir=wt_path,
    )
    round_ = overrides.pop("round_", None) or _triaged_round(**overrides)
    return fix.comments.CommentFixAdapter(report, ctx, wt_path, round_)


# ── _render_deferred_summary ───────────────────────────────────────────────


# A worktree root that exists but holds no repo. Existing is the part that
# matters: the git client passes the root as `cwd`, so a made-up path fails in
# Python before git is reached, where `git -C` used to just exit non-zero. Not
# being a repo is what these tests want — every git read degrades to its
# default, which is the state each of them was written against.
_STATE_WORKTREE = tempfile.mkdtemp(prefix="review-threads-state-")
atexit.register(shutil.rmtree, _STATE_WORKTREE, ignore_errors=True)


def _make_state(fix=None):
    """Build a minimal PRState with the given FixSummary."""
    return PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1,
            head_sha="abc1234", worktree_root=_STATE_WORKTREE,
        ),
        fix=fix or _fix(),
    )


# ── reply upsert ─────────────────────────────────────────────────────────


def _standing_reply_thread(tid="t1", body="Applied: old take", **kw):
    """A thread whose last comment is ours and unanswered — the editable case."""
    kw.setdefault("state", ThreadState.ADDRESSED)
    kw.setdefault("my_login", "me")
    return ReportThread(id=tid, comments=[
        {"databaseId": 111, "body": "reviewer's point", "author": {"login": "kgn"}},
        {"databaseId": 222, "body": body, "author": {"login": "me"}},
    ], **kw)


# ── rows the published comment has and local state does not ────────────────


ROUND_ONE_ROW = (
    "| [drop the guard](https://github.com/owner/repo/pull/1#discussion_r111) "
    "| @kgn | [`old.go:4`](https://github.com/owner/repo/blob/aaaaaaa/old.go#L4) "
    "| Fixed in [`9f2e1a0`](https://github.com/owner/repo/commit/9f2e1a0) |"
)


def _unmarked(rows) -> list[str]:
    """Rows with the marker a re-emitted legacy row is stamped with taken out.

    These cases are about which rows are kept, not about the stamp, which
    `published_record_test.py` covers on its own.
    """
    return [pr.published_record.unmarked(row) for row in rows]


def _published_summary(*rows: str) -> str:
    """A prior summary comment carrying the given rendered rows."""
    return "\n".join([
        pr.summary_render.SUMMARY_MARKER, "## Review Comments Addressed", "",
        "**1 fixed**", "",
        pr.summary_model.TABLE_HEADER, pr.summary_model.TABLE_DIVIDER,
        *rows, "",
    ])


# ── rows a human rewrote, that local state can account for ─────────────────


_GENERATED_ACTION_CELL = "Fixed in [`9f2e1a0`](https://github.com/owner/repo/commit/9f2e1a0)"


_HAND_WRITTEN_ACTION_CELL = (
    "Superseded — @kgn reproduced it independently and the next review round "
    "accepted the root cause"
)


HAND_EDITED_ROW = ROUND_ONE_ROW.replace(
    _GENERATED_ACTION_CELL, _HAND_WRITTEN_ACTION_CELL)


# ── items triage cut out of one top-level comment ──────────────────────────


# Three points raised in one issue comment, so every row triage renders for
# them links back to the one permalink that comment has.
_SIBLING_ITEMS = [
    CommentItem(id="ic-900-0", summary="drop the guard", reviewer="kgn",
                file="old.go", line=4),
    CommentItem(id="ic-900-1", summary="name the timeout", reviewer="kgn",
                file="net.go", line=12),
    CommentItem(id="ic-900-2", summary="log the retry", reviewer="kgn",
                file="net.go", line=31),
]


# ── addressed in response, or genuinely already addressed ──────────────────


_THE_REVIEW_COMMENT = "2025-01-01T00:00:00Z"


_BEFORE_THE_REVIEW = "2020-01-01T00:00:00+0000"


_AFTER_THE_REVIEW = "2030-01-01T00:00:00+0000"


# ── comment items settle through their source comment ─────────────────────


def _fetches(comments):
    """Stub the PR's issue-comment listing with `comments`."""
    return patch("pr.comments.fetch_issue_comments", return_value=comments)


def _our_reply(anchor, prefix="Applied:", user="me"):
    return {
        "user": user,
        "body": f"{prefix} drop the retry\n\n"
                f"See https://github.com/owner/repo/pull/42{anchor}.",
    }
