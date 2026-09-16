"""Tests for `pr.attribution` — dating a row's fix against the point it answers.

`AddressingHistory` is what separates "the code does what you asked" from "the
code was made to do what you asked", and only the second is a fix. Getting it
wrong is reviewer-facing in the worst direction: the flat wording tells someone
the point they raised needed no action.

The half with no test at all was the row that has no review thread. A decomposed
comment item carries a synthetic `rb-`/`ic-` id, so every lookup among threads
misses and the run could not date one side of the comparison — which read as
"no", unconditionally, whatever commit made the point true. Covered here rather
than through a summary body six layers up, so a change to the rule fails on the
rule.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from conftest import REPO_ROOT, git_out, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from git import topology as git_topology  # noqa: E402
from pr import attribution  # noqa: E402
from pr import summary_model  # noqa: E402
from pr import summary_render  # noqa: E402
from pr.fix import FixOutcome, SettledBy  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

THE_REVIEW_COMMENT = "2025-01-01T00:00:00Z"
BEFORE_THE_REVIEW = "2020-01-01T00:00:00+0000"
AFTER_THE_REVIEW = "2030-01-01T00:00:00+0000"

# The source comment behind every decomposed item below, and the id its
# synthetic id parses back to.
SOURCE_ID = "77"
ITEM_ID = f"ic-{SOURCE_ID}-0"


def _git(wt: Path, *args: str, when: str = "") -> None:
    env = dict(os.environ)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    run_checked(["git", "-C", str(wt), *args], env=env)


def _sha(wt: Path, rev: str = "HEAD") -> str:
    return git_out(wt, "rev-parse", rev).strip()


@pytest.fixture
def branch(worktree):
    """One commit dated before the review and one after it, at separate lines.

    Line 1 was last touched long before the review and line 2 in response to
    it, so the same resolver answers "in response" for one row and "already
    true" for the other without the fixture deciding which.
    """
    hooks = worktree / ".git" / "empty-hooks"
    hooks.mkdir()
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")
    _git(worktree, "config", "commit.gpgsign", "false")
    _git(worktree, "config", "core.hooksPath", str(hooks))
    (worktree / "a.py").write_text("one\ntwo\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-qm", "base")
    _git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
    (worktree / "a.py").write_text("ONE\ntwo\n")
    _git(worktree, "commit", "-qam", "line one, long before the review",
         when=BEFORE_THE_REVIEW)
    before = _sha(worktree)
    (worktree / "a.py").write_text("ONE\nTWO\n")
    _git(worktree, "commit", "-qam", "line two, in response to the review",
         when=AFTER_THE_REVIEW)
    return SimpleNamespace(path=worktree, before=before, after=_sha(worktree))


@pytest.fixture
def on_main():
    """`origin/main` as the base every `git log -L` in these tests reads from."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(git_topology, "default_branch_cached", lambda *a, **k: "main")
        yield


def _item(line: int, **kw) -> CommentItem:
    """A decomposed comment item — no thread, and routinely no line of its own."""
    defaults = {
        "id": ITEM_ID, "file": "a.py", "line": line, "reviewer": "kgn",
        "summary": "the retry is unbounded",
    }
    defaults.update(kw)
    return CommentItem(**defaults)


def _thread(tid: str = "t1") -> ReportThread:
    return ReportThread(id=tid, comments=[
        {"databaseId": 111, "createdAt": THE_REVIEW_COMMENT},
    ])


class TestDatingAnItemAgainstItsSourceComment:
    """A row with no review thread is dated by the comment it was cut from.

    The defect: `thread_opened_at(None)` is `0.0`, so `_postdates` returned
    False for every decomposed item and no such row could ever read as a fix. A
    branch that committed the fix two hours after the reviewer wrote the comment
    told them all four of their points were moot.
    """

    def test_a_commit_after_the_source_comment_reads_as_in_response(
        self, branch, on_main,
    ):
        history = attribution.AddressingHistory(
            branch.path, {SOURCE_ID: THE_REVIEW_COMMENT})
        framing = history.framing(_item(2), None)
        assert framing.in_response
        assert framing.sha == branch.after

    def test_a_commit_before_it_keeps_the_flat_wording(self, branch, on_main):
        """An item that genuinely predates the comment is the "already" case."""
        history = attribution.AddressingHistory(
            branch.path, {SOURCE_ID: THE_REVIEW_COMMENT})
        framing = history.framing(_item(1), None)
        assert not framing.in_response
        assert framing.sha == branch.before

    def test_an_undatable_source_still_declines(self, branch, on_main):
        """No timestamp for the comment is no evidence, which is the old reading.

        Claiming credit for a fix is the assertion that needs evidence, so a run
        that cannot date the comment keeps the wording it had before.
        """
        history = attribution.AddressingHistory(branch.path, {})
        assert not history.framing(_item(2), None).in_response

    def test_a_history_built_without_timestamps_declines(self, branch, on_main):
        """The default: a caller with no comment listing claims nothing new."""
        history = attribution.AddressingHistory(branch.path)
        assert not history.framing(_item(2), None).in_response

    def test_an_entry_that_is_not_a_comment_item_is_unaffected(
        self, branch, on_main,
    ):
        """A thread id parses to no source, so there is nothing to look up."""
        history = attribution.AddressingHistory(
            branch.path, {SOURCE_ID: THE_REVIEW_COMMENT})
        entry = CommentItem(id="t1", file="a.py", line=2, reviewer="kgn")
        assert not history.framing(entry, None).in_response

    def test_the_thread_still_wins_where_there_is_one(self, branch, on_main):
        """A thread dates itself; the comment map is for the rows that cannot.

        The source timestamp here is far in the future, so reading it instead of
        the thread's own open time would flip the answer.
        """
        history = attribution.AddressingHistory(
            branch.path, {SOURCE_ID: "2099-01-01T00:00:00Z"})
        entry = CommentItem(id="t1", file="a.py", line=2, reviewer="kgn")
        assert history.framing(entry, _thread()).in_response

    def test_a_review_body_item_is_dated_the_same_way(self, branch, on_main):
        """`rb-` and `ic-` are one shape — both parse to a source comment id."""
        history = attribution.AddressingHistory(
            branch.path, {"88": THE_REVIEW_COMMENT})
        assert history.framing(_item(2, id="rb-88-1"), None).in_response


class TestTheSummaryRowReadsTheSameAnswer:
    """The row and the resolver are one answer, or the table contradicts a reply.

    `build_summary_body` is what the reviewer actually sees, and it builds its
    own history when the caller hands it none — so the timestamps have to reach
    that construction too, not only the one `summary_publish` makes.
    """

    def _body(self, branch, entries, comments):
        content = summary_model.RoundContent(
            by_outcome={FixOutcome.ALREADY_ADDRESSED: entries},
            issue_comments=comments,
            review_body_comments=[],
        )
        cp = attribution.CommitPushResult("abc1234", "pushed", "")
        return summary_render.build_summary_body(
            content, cp, "owner/repo", 42, {}, wt_path=branch.path,
        )

    def test_an_item_fixed_after_its_comment_is_counted_as_fixed(
        self, branch, on_main,
    ):
        body = self._body(
            branch, [_item(2)],
            [{"id": SOURCE_ID, "created_at": THE_REVIEW_COMMENT, "seen": True}],
        )
        assert f"Fixed in [`{branch.after}`]" in body
        assert "Already addressed" not in body
        assert "1 fixed" in body

    def test_an_item_whose_code_predates_its_comment_is_already_addressed(
        self, branch, on_main,
    ):
        body = self._body(
            branch, [_item(1)],
            [{"id": SOURCE_ID, "created_at": THE_REVIEW_COMMENT, "seen": True}],
        )
        assert "Already addressed" in body
        assert "1 already addressed" in body


class TestAPublishedFixVerdictSurvivesAReplay:
    """A row published as a fix must read the same way the next round.

    The record kept the reviewer's anchor and dropped triage's evidence
    location, so round one asked `git log -L` at the evidence line and round two
    asked at the anchor — a different line, a different commit, and a row
    published as "Fixed in <sha>" came back reworded as "Already addressed",
    which tells the reviewer their comment needed no action.

    No rebase fixture: the rebase only shifts anchors so more of them stop
    resolving. The defect reproduces over an unchanged tree, which is what makes
    the test cheap and the diagnosis sharp.
    """

    def _entry(self, **kw):
        """The reviewer anchored line 1; triage cited line 2 as its evidence.

        The two lines disagree on purpose, and each has its own commit: line 1's
        predates the review and line 2's answers it. Whichever location the
        round asks about decides the verdict, so a round trip that drops one
        changes the answer.
        """
        defaults = {
            "id": "t1", "file": "a.py", "line": 1, "reviewer": "kgn",
            "summary": "the retry is unbounded",
            "evidence_file": "a.py", "evidence_line": 2,
        }
        defaults.update(kw)
        return CommentItem(**defaults)

    def _framing(self, branch, entry):
        history = attribution.AddressingHistory(branch.path)
        return history.framing(entry, _thread())

    def test_the_replayed_round_reaches_the_same_verdict(self, branch, on_main):
        """The defect: round one cited a commit, round two cited none."""
        entry = self._entry()
        first = self._framing(branch, entry)
        replayed = CommentItem.from_outcome(
            entry.to_outcome(FixOutcome.ALREADY_ADDRESSED), "kgn")
        second = self._framing(branch, replayed)
        assert first.in_response and first.sha == branch.after
        assert (second.in_response, second.sha) == (first.in_response, first.sha)

    def test_the_evidence_location_survives_the_record(self, branch, on_main):
        replayed = CommentItem.from_outcome(self._entry().to_outcome())
        assert replayed.has_evidence()
        assert (replayed.evidence_file, replayed.evidence_line) == ("a.py", 2)

    def test_an_anchor_that_no_longer_resolves_behaves_the_same_way(
        self, branch, on_main,
    ):
        """The other side of the observed split, and it must not be the tell.

        A thread anchored past the end of the file resolves to no commit at all.
        That row reads as flat both rounds — which is wrong in its own right, and
        is what the floor in `summary_rounds` is for — but it must not differ
        between the round that published it and the round that replayed it.
        """
        entry = self._entry(line=99, evidence_file="", evidence_line=0)
        first = self._framing(branch, entry)
        second = self._framing(
            branch, CommentItem.from_outcome(
                entry.to_outcome(FixOutcome.ALREADY_ADDRESSED)))
        assert not first.cited
        assert (second.in_response, second.sha) == (first.in_response, first.sha)

    def test_an_item_that_cited_nothing_still_falls_back_to_its_anchor(
        self, branch, on_main,
    ):
        """Empty evidence is "triage cited nothing", not "look nowhere"."""
        entry = self._entry(line=2, evidence_file="", evidence_line=0)
        replayed = CommentItem.from_outcome(entry.to_outcome())
        assert self._framing(branch, replayed).sha == branch.after

    def test_the_provenance_a_floor_reads_travels_too(self, branch, on_main):
        """The floor's exemption is keyed on it, so a lost field would bury it."""
        entry = self._entry(settled_by=SettledBy.OPERATOR)
        replayed = CommentItem.from_outcome(entry.to_outcome())
        assert replayed.settled_by is SettledBy.OPERATOR


class TestReadingATimestamp:
    """`posix_seconds` is the one parser both dating surfaces go through."""

    def test_a_github_stamp_parses(self):
        assert attribution.posix_seconds("2025-01-01T00:00:00Z") > 0

    def test_an_unparseable_stamp_is_zero(self):
        assert attribution.posix_seconds("last tuesday") == 0.0

    def test_an_empty_stamp_is_zero(self):
        assert attribution.posix_seconds("") == 0.0
