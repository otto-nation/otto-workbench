"""Tests for `pr.permalinks` — the links a reply points at, and what backs them.

`git-operations.md` § Replying to Review Comments requires every reply to cite a
SHA-pinned permalink. Two things have to hold for that to mean anything: the URL
has to name a commit rather than a branch, and the line it highlights has to be
a line that exists. Both are tested here.

`anchored_line`'s drift check against a real two-commit branch lives in
`test_review_threads.py::TestLineAnchorsAreTreeScoped`, which builds the two
trees it needs; what is added here is the paths that need no repository.
"""

import sys
from pathlib import Path

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr import permalinks  # noqa: E402
from pr import settlement  # noqa: E402
from pr import summary_model  # noqa: E402
from pr import triage  # noqa: E402
from pr.fix import ItemOutcome  # noqa: E402
from pr.thread_models import (  # noqa: E402
    THREAD_ANCHOR, CommentItem, CommentSourceKind, ReportThread,
)

_KINDS = [k for k in CommentSourceKind if k is not CommentSourceKind.UNSET]

_REPO = "owner/repo"
_SHA = "abc1234"


class TestBlobPermalink:
    def test_a_line_is_anchored(self):
        assert permalinks.blob_permalink(_REPO, _SHA, "a/b.py", 7) == (
            f"https://github.com/{_REPO}/blob/{_SHA}/a/b.py#L7")

    def test_no_line_gives_a_whole_file_link(self):
        assert permalinks.blob_permalink(_REPO, _SHA, "a/b.py") == (
            f"https://github.com/{_REPO}/blob/{_SHA}/a/b.py")

    def test_the_url_names_a_commit_and_not_a_branch(self):
        """The requirement the whole module exists for."""
        url = permalinks.blob_permalink(_REPO, _SHA, "a/b.py", 7)
        assert f"/blob/{_SHA}/" in url
        assert "/blob/main/" not in url


class TestCommitPermalink:
    def test_it_names_the_commit(self):
        assert permalinks.commit_permalink(_REPO, _SHA) == (
            f"https://github.com/{_REPO}/commit/{_SHA}")

    def test_no_caller_still_builds_the_url_by_hand(self):
        """The summary cell, the fixed reply and the addressed reply.

        Each built this URL itself before it had an owner. A reviewer reads a
        404 here as the tool lying about a fix, so a fourth hand-rolled copy
        should fail rather than drift.
        """
        import pr.thread_replies
        from pathlib import Path as _Path

        # permalinks.py itself is the owner and is where the literal belongs.
        lib = _Path(pr.thread_replies.__file__).parent
        sources = [lib / "thread_replies.py",
                   lib.parent.parent / "bin" / "review-threads"]
        for src in sources:
            text = src.read_text(encoding="utf-8")
            hand_rolled = [
                line for line in text.splitlines()
                if "/commit/" in line and "commit_permalink" not in line
                and not line.lstrip().startswith("#")
            ]
            assert hand_rolled == [], f"{src.name}: {hand_rolled}"


class TestAnchoredLineWithoutARepository:
    """The four answers that need no worktree to reach."""

    def test_a_line_read_in_the_rendered_tree_keeps_its_anchor(self):
        entry = CommentItem(id="t1", read_sha=_SHA)
        assert permalinks.anchored_line(entry, "f.py", 4, _SHA, None) == 4

    def test_an_entry_with_no_recorded_tree_loses_its_anchor(self):
        """Unknown reads as no: the anchor is the part that has to be earned."""
        entry = CommentItem(id="t1", read_sha="")
        assert permalinks.anchored_line(entry, "f.py", 4, _SHA, None) == 0

    def test_no_line_is_no_anchor(self):
        entry = CommentItem(id="t1", read_sha=_SHA)
        assert permalinks.anchored_line(entry, "f.py", 0, _SHA, None) == 0

    def test_a_different_tree_with_no_worktree_to_check_loses_the_anchor(self):
        entry = CommentItem(id="t1", read_sha="0ldtree")
        assert permalinks.anchored_line(entry, "f.py", 4, _SHA, None) == 0


class TestEvidenceIsReal:
    """Both halves of a citation, neither of which a path join checks."""

    @pytest.fixture
    def tree(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("one\ntwo\nthree\n")
        return tmp_path

    def test_a_line_inside_the_file_is_real(self, tree):
        entry = CommentItem(id="t1", evidence_file="src/a.py", evidence_line=2)
        assert permalinks.evidence_is_real(tree, entry) is True

    def test_the_last_line_is_real(self, tree):
        entry = CommentItem(id="t1", evidence_file="src/a.py", evidence_line=3)
        assert permalinks.evidence_is_real(tree, entry) is True

    def test_a_line_past_the_end_is_not(self, tree):
        """A permalink past EOF highlights nothing."""
        entry = CommentItem(id="t1", evidence_file="src/a.py", evidence_line=99)
        assert permalinks.evidence_is_real(tree, entry) is False

    def test_a_missing_file_is_not(self, tree):
        entry = CommentItem(id="t1", evidence_file="src/gone.py", evidence_line=1)
        assert permalinks.evidence_is_real(tree, entry) is False

    def test_a_directory_is_not_a_cited_file(self, tree):
        entry = CommentItem(id="t1", evidence_file="src", evidence_line=1)
        assert permalinks.evidence_is_real(tree, entry) is False

    def test_an_entry_citing_nothing_is_not(self, tree):
        assert permalinks.evidence_is_real(tree, CommentItem(id="t1")) is False

    def test_a_relative_path_escaping_the_tree_is_refused(self, tree):
        """`..` walks out, and the file it lands on is real \u2014 hence `resolve`."""
        outside = tree.parent / "outside.py"
        outside.write_text("secrets\n")
        entry = CommentItem(
            id="t1", evidence_file="../outside.py", evidence_line=1)
        assert permalinks.evidence_is_real(tree, entry) is False

    def test_an_absolute_path_is_refused(self, tree):
        """Joining a directory with an absolute path discards the directory."""
        outside = tree.parent / "abs.py"
        outside.write_text("secrets\n")
        entry = CommentItem(id="t1", evidence_file=str(outside), evidence_line=1)
        assert permalinks.evidence_is_real(tree, entry) is False


class TestCodeLink:
    def test_the_citation_is_preferred_over_the_comment_location(self, tmp_path):
        (tmp_path / "cited.py").write_text("a\nb\n")
        entry = CommentItem(
            id="t1", file="thread.py", line=1,
            evidence_file="cited.py", evidence_line=2, read_sha=_SHA)
        assert "cited.py" in permalinks.code_link(entry, _REPO, _SHA, tmp_path)

    def test_an_uncited_entry_falls_back_to_its_own_file(self, tmp_path):
        entry = CommentItem(id="t1", file="thread.py", line=1, read_sha=_SHA)
        link = permalinks.code_link(entry, _REPO, _SHA, tmp_path)
        assert link == (
            f"[`thread.py:1`](https://github.com/{_REPO}/blob/{_SHA}/thread.py#L1)")

    def test_an_entry_with_nothing_to_point_at_links_nothing(self, tmp_path):
        assert permalinks.code_link(CommentItem(id="t1"), _REPO, _SHA, tmp_path) == ""

    def test_no_sha_links_nothing(self, tmp_path):
        """There is no such thing as an unpinned link here."""
        entry = CommentItem(id="t1", file="thread.py", line=1)
        assert permalinks.code_link(entry, _REPO, "", tmp_path) == ""

    def test_an_unreal_citation_falls_back_rather_than_linking_it(self, tmp_path):
        entry = CommentItem(
            id="t1", file="thread.py", line=1,
            evidence_file="gone.py", evidence_line=9, read_sha=_SHA)
        assert "thread.py" in permalinks.code_link(entry, _REPO, _SHA, tmp_path)

    def test_verification_off_takes_the_citation_unchecked(self, tmp_path):
        entry = CommentItem(
            id="t1", file="thread.py", line=1,
            evidence_file="gone.py", evidence_line=9, read_sha=_SHA)
        link = permalinks.code_link(
            entry, _REPO, _SHA, tmp_path, verify_evidence=False)
        assert "gone.py" in link


class TestCommentSource:
    def test_an_issue_comment_anchors_to_its_comment(self):
        source = permalinks.CommentSource("issue_comment", "900")
        assert source.permalink(_REPO, 42) == (
            f"https://github.com/{_REPO}/pull/42#issuecomment-900")

    def test_a_review_body_anchors_to_its_review(self):
        source = permalinks.CommentSource("review_body", "901")
        assert source.permalink(_REPO, 42) == (
            f"https://github.com/{_REPO}/pull/42#pullrequestreview-901")

    def test_an_empty_source_has_no_permalink(self):
        assert permalinks.CommentSource().permalink(_REPO, 42) is None
        assert permalinks.CommentSource().ok is False

    def test_an_unknown_type_has_no_permalink(self):
        source = permalinks.CommentSource("something_else", "902")
        assert source.permalink(_REPO, 42) is None


class TestCommentItemSource:
    def test_declared_fields_are_used_as_given(self):
        entry = CommentItem(id="x", source_id="900", source_type="issue_comment")
        assert permalinks.comment_item_source(entry) == permalinks.CommentSource(
            "issue_comment", "900")

    def test_a_synthetic_issue_comment_id_is_parsed(self):
        entry = CommentItem(id="ic-900-2")
        assert permalinks.comment_item_source(entry) == permalinks.CommentSource(
            "issue_comment", "900")

    def test_a_synthetic_review_body_id_is_parsed(self):
        entry = CommentItem(id="rb-901-0")
        assert permalinks.comment_item_source(entry) == permalinks.CommentSource(
            "review_body", "901")

    def test_a_hyphenated_source_id_survives_the_parse(self):
        """The id is rejoined, so only the prefix and index are stripped."""
        entry = CommentItem(id="ic-900-abc-1")
        assert permalinks.comment_item_source(entry).id == "900-abc"

    def test_a_thread_id_parses_to_no_source(self):
        assert permalinks.comment_item_source(CommentItem(id="t1")).ok is False

    def test_an_outcome_is_read_the_same_way(self):
        """Reconciliation asks this of the record before an entry exists."""
        outcome = ItemOutcome(id="ic-900-0")
        assert permalinks.comment_item_source(outcome) == permalinks.CommentSource(
            "issue_comment", "900")


class TestThreadPermalink:
    def test_a_thread_anchors_to_its_first_comment(self):
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        assert permalinks.thread_permalink(
            CommentItem(id="t1"), threads, _REPO, 42) == (
            f"https://github.com/{_REPO}/pull/42#discussion_r111")

    def test_a_thread_with_no_database_id_falls_back(self):
        threads = {"t1": ReportThread(id="t1", comments=[{}])}
        assert permalinks.thread_permalink(
            CommentItem(id="t1"), threads, _REPO, 42) is None

    def test_a_comment_item_anchors_to_its_source_comment(self):
        assert permalinks.thread_permalink(
            CommentItem(id="ic-900-0"), {}, _REPO, 42) == (
            f"https://github.com/{_REPO}/pull/42#issuecomment-900")

    def test_an_unknown_entry_has_no_permalink(self):
        assert permalinks.thread_permalink(
            CommentItem(id="t9"), {}, _REPO, 42) is None


class TestTheAnchorSpellingsAreGitHubs:
    """The anchors are pinned as literals, because GitHub chose them.

    Every other test here reads the spelling off the same member that wrote it,
    so they hold for any spelling at all — swap two members' anchors and they
    stay green. Two things make that insufficient. A fragment GitHub does not
    serve is a link that 404s for the reviewer it was written for. And the
    anchors on every summary comment already published are the real ones: a
    reader that stops matching them reads each of those rows as new, so the
    whole table duplicates on the next round.

    So these values are an external contract, and changing one is a breaking
    change to comments this tool has already posted rather than a rename.
    """

    def test_the_anchors_are_the_ones_github_serves(self):
        assert CommentSourceKind.ISSUE_COMMENT.anchor == "issuecomment"
        assert CommentSourceKind.REVIEW_BODY.anchor == "pullrequestreview"
        assert THREAD_ANCHOR == "discussion_r"

    def test_the_persisted_tokens_are_unchanged(self):
        """State files written before this enum existed still load."""
        assert CommentSourceKind.ISSUE_COMMENT.value == "issue_comment"
        assert CommentSourceKind.REVIEW_BODY.value == "review_body"

    def test_the_id_prefixes_are_unchanged(self):
        """A synthetic id in a live state file has to keep resolving."""
        assert CommentSourceKind.ISSUE_COMMENT.id_prefix == "ic"
        assert CommentSourceKind.REVIEW_BODY.id_prefix == "rb"

    def test_a_published_anchor_is_still_recognised(self):
        """Read as the published comments hold it, not as the writer builds it."""
        published = (
            "| [x](https://github.com/owner/repo/pull/42#pullrequestreview-88) "
            "| @kgn | `a.go:7` | Fixed |"
        )
        assert summary_model.ITEM_ANCHOR_RE.search(published).group(0) == (
            "#pullrequestreview-88")


class TestEveryKindRoundTrips:
    """The writer and both readers are held to one vocabulary.

    Parametrized over the enum rather than over a literal list, so a kind added
    to `CommentSourceKind` and wired nowhere fails here instead of shipping a
    permalink that no reader recognises — which is a published row losing its
    identity between rounds, the failure this vocabulary exists to prevent.
    """

    @pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
    def test_the_summary_reader_matches_what_the_writer_emits(self, kind):
        url = permalinks.CommentSource(kind, "900").permalink(_REPO, 42)
        assert summary_model.ITEM_ANCHOR_RE.search(url)

    @pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
    def test_the_settlement_reader_recovers_the_id(self, kind):
        url = permalinks.CommentSource(kind, "900").permalink(_REPO, 42)
        assert settlement._SOURCE_ANCHOR_RE.search(url).group(1) == "900"

    @pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
    def test_a_synthetic_id_parses_back_to_the_kind_that_wrote_it(self, kind):
        """`assign_item_ids` is the writer; `comment_item_source` is the reader.

        Read through both rather than asserting the id's spelling: what matters
        is that the prefix one writes is the prefix the other resolves, not
        which two letters they agreed on.
        """
        item = CommentItem(source_id="900", source_type=kind, index=2)
        triage.assign_item_ids([item])
        assert permalinks.comment_item_source(
            CommentItem(id=item.id)) == permalinks.CommentSource(kind, "900")

    @pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
    def test_a_thread_anchor_is_not_read_as_a_comment_item(self, kind):
        """The two anchors are alternatives, and identity branches on which."""
        thread_url = f"https://github.com/{_REPO}/pull/42#{THREAD_ANCHOR}111"
        assert summary_model.ITEM_ANCHOR_RE.search(thread_url) is None
        assert summary_model.THREAD_ANCHOR_RE.search(thread_url)


class TestAnUnknownKindDegradesRatherThanMisreports:
    """An unrecognised token is UNSET, and UNSET claims nothing.

    The old ternary in `assign_item_ids` had no such case: anything that was
    not `issue_comment` was called a review body, so a drifted token produced
    an `rb-` id and a `#pullrequestreview` link to a review that never existed.
    A row with no permalink is recoverable; one pointing at the wrong comment
    is not.
    """

    def test_an_invented_token_becomes_unset(self):
        assert CommentItem(source_type="banana").source_type is CommentSourceKind.UNSET

    def test_an_unset_source_has_no_permalink(self):
        source = permalinks.CommentSource(CommentSourceKind.UNSET, "900")
        assert source.permalink(_REPO, 42) is None

    def test_an_invented_token_is_not_filed_under_a_real_kind(self):
        item = CommentItem(source_id="900", source_type="banana", index=0)
        triage.assign_item_ids([item])
        assert not item.id.startswith(CommentSourceKind.REVIEW_BODY.id_prefix)
        assert permalinks.comment_item_source(CommentItem(id=item.id)).ok is False

    def test_a_thread_id_is_claimed_by_no_kind(self):
        assert CommentSourceKind.from_id_prefix("") is CommentSourceKind.UNSET
        assert CommentSourceKind.from_id_prefix("zz") is CommentSourceKind.UNSET
