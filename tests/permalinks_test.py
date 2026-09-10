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
from pr.fix import ItemOutcome  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

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
