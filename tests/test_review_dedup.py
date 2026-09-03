import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_dedup  # noqa: E402


def test_posted_findings_are_typed_not_dicts():
    """The comparison reads named fields, so a caller cannot mistake path for body."""
    body = (
        "- **[M1] [must-fix]** `pkg/handler.go:42` — the lock is never released\n"
    )

    found = review_dedup._extract_body_findings(body)

    assert [f.path for f in found] == ["pkg/handler.go"]
    assert found[0].body.startswith("the lock is never released")


class TestWordSet:
    def test_extracts_lowercase_words(self):
        assert review_dedup.word_set("Hello World_Foo 123") == {"hello", "world_foo", "123"}

    def test_empty_string(self):
        assert review_dedup.word_set("") == set()

    def test_strips_punctuation(self):
        assert review_dedup.word_set("error — missing `check`") == {"error", "missing", "check"}


class TestJaccard:
    def test_identical_sets(self):
        assert review_dedup.jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert review_dedup.jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        assert review_dedup.jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_both_empty(self):
        assert review_dedup.jaccard(set(), set()) == 1.0

    def test_one_empty(self):
        assert review_dedup.jaccard({"a"}, set()) == 0.0


class TestExtractBodyFindings:
    def test_standard_finding_in_body(self):
        body = "- **[M1]** **`handler.go:42`** — Fix the bug"
        results = review_dedup._extract_body_findings(body)
        assert len(results) == 1
        assert results[0].path == "handler.go"
        assert results[0].body == "Fix the bug"

    def test_multiple_findings(self):
        body = (
            "- **[M1]** **`a.go:10`** — First issue\n"
            "- **[S1]** **`b.go:20`** — Second issue\n"
        )
        results = review_dedup._extract_body_findings(body)
        assert len(results) == 2
        assert results[0].path == "a.go"
        assert results[1].path == "b.go"

    def test_no_findings(self):
        body = "Just some regular text with no findings."
        results = review_dedup._extract_body_findings(body)
        assert len(results) == 0

    def test_path_extraction_with_line_number_suffix(self):
        body = "- **[M1]** **`handler.go:42`** — Fix bug"
        results = review_dedup._extract_body_findings(body)
        assert results[0].path == "handler.go"

    def test_a_colon_that_is_not_a_line_suffix_survives(self):
        """Dedup compares the path the rest of the pipeline parsed.

        This reader used to truncate at the last colon, so an already-posted
        comment on `ns:module.py` was recorded against `ns` and matched no
        finding — the same finding posted again on every re-review.
        """
        body = "- **[M1]** **`ns:module.py`** — Fix bug"
        results = review_dedup._extract_body_findings(body)
        assert results[0].path == "ns:module.py"

    def test_a_line_suffix_still_comes_off_a_path_carrying_a_colon(self):
        body = "- **[M1]** **`C:/src/x.py:12`** — Fix bug"
        results = review_dedup._extract_body_findings(body)
        assert results[0].path == "C:/src/x.py"


class TestCollectInlineComments:
    def test_filters_by_bot_user(self):
        comments = [
            {"path": "a.go", "body": "fix", "user": {"login": "bot"}},
            {"path": "b.go", "body": "nit", "user": {"login": "human"}},
            {"path": "c.go", "body": "issue", "user": {"login": "bot"}},
        ]
        with patch("gh_client.api_json", return_value=comments):
            result = review_dedup._collect_inline_comments("org/repo", "1", "bot")
            assert len(result) == 2
            assert all(r.path in ("a.go", "c.go") for r in result)

    def test_empty_comments(self):
        with patch("gh_client.api_json", return_value=[]):
            result = review_dedup._collect_inline_comments("org/repo", "1", "bot")
            assert result == []

    def test_the_identity_marker_comes_off_before_anything_compares_the_text(self):
        """The marker is a handle on the finding, not part of what it says.

        A fresh finding carries no marker, so left on, it is two tokens only the
        posted side has — every similarity score against it comes out lower than
        the wording earns.
        """
        comments = [{
            "path": "a.go",
            "body": "**[M1] [must-fix]** <!-- sid:abc12345 --> Fix bug",
            "user": {"login": "bot"},
        }]
        with patch("gh_client.api_json", return_value=comments):
            result = review_dedup._collect_inline_comments("org/repo", "1", "bot")
        assert result[0].body == "**[M1] [must-fix]** Fix bug"


class TestCollectReviewFindings:
    def test_extracts_from_bot_review_bodies(self):
        reviews = [
            {"body": "- **[M1]** **`a.go:1`** — issue one", "user": {"login": "bot"}},
            {"body": "no findings", "user": {"login": "human"}},
        ]
        with patch("gh_client.api_json", return_value=reviews):
            result = review_dedup._collect_review_findings("org/repo", "1", "bot")
            assert len(result) == 1
            assert result[0].path == "a.go"

    def test_skips_empty_bodies(self):
        reviews = [
            {"body": "", "user": {"login": "bot"}},
        ]
        with patch("gh_client.api_json", return_value=reviews):
            result = review_dedup._collect_review_findings("org/repo", "1", "bot")
            assert result == []


class TestFetchBotComments:
    def test_combines_inline_and_review_findings(self):
        with (
            patch("gh_client.login", return_value="bot"),
            patch("gh_client.api_json", side_effect=[
                [{"path": "a.go", "body": "inline", "user": {"login": "bot"}}],
                [{"body": "- **[M1]** **`b.go:1`** — review", "user": {"login": "bot"}}],
            ]),
        ):
            result = review_dedup._fetch_bot_comments("org/repo", "1")
            assert len(result) == 2

    def test_empty_login_returns_empty(self):
        with patch("gh_client.login", return_value=""):
            assert review_dedup._fetch_bot_comments("org/repo", "1") == []


class TestDedupAgainstPosted:
    def _make_finding(self, id_str, path, body):
        return review_dedup.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_skips_duplicate(self, mock_fetch):
        mock_fetch.return_value = [
            review_dedup.PostedFinding("handler.go", "missing error check on db.Query result"),
        ]
        f = self._make_finding("M1", "handler.go", "missing error check on db.Query result")
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 0
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_keeps_non_duplicate(self, mock_fetch):
        mock_fetch.return_value = [
            review_dedup.PostedFinding("handler.go", "missing error check on db.Query result"),
        ]
        f = self._make_finding("S1", "handler.go", "unused import os")
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0

    @patch("review_dedup._fetch_bot_comments")
    def test_different_file_not_duplicate(self, mock_fetch):
        mock_fetch.return_value = [
            review_dedup.PostedFinding("handler.go", "missing error check"),
        ]
        f = self._make_finding("M1", "other.go", "missing error check")
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1

    @patch("review_dedup._fetch_bot_comments")
    def test_no_existing_comments_keeps_all(self, mock_fetch):
        mock_fetch.return_value = []
        f = self._make_finding("M1", "handler.go", "finding text")
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestDedupAgainstPostedEdgeCases:
    def _make_finding(self, id_str, path, body):
        return review_dedup.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_jaccard_at_threshold_boundary(self, mock_fetch):
        # Build words so Jaccard is exactly 0.6: 3 shared out of 5 total
        # a = {"a", "b", "c"}, b = {"a", "b", "c", "d", "e"} => 3/5 = 0.6
        mock_fetch.return_value = [
            review_dedup.PostedFinding("file.go", "a b c d e"),
        ]
        f = self._make_finding("M1", "file.go", "a b c")
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_empty_path_on_both_sides_not_matched(self, mock_fetch):
        mock_fetch.return_value = [
            review_dedup.PostedFinding("", "missing error check"),
        ]
        f = self._make_finding("M1", "", "missing error check")
        # Override to set path="" since _make_finding sets path to the arg
        f.path = ""
        kept, deduped = review_dedup.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestFetchBotReviews:
    def test_returns_bot_reviews(self, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 1, "user": {"login": "bot"}, "state": "COMMENTED", "body": "review text"},
            {"id": 2, "user": {"login": "human"}, "state": "COMMENTED", "body": "human review"},
            {"id": 3, "user": {"login": "bot"}, "state": "PENDING", "body": "pending"},
        ])
        result = review_dedup.fetch_bot_reviews("org/repo", "1")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_ignores_pending(self, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "PENDING", "user": {"login": "bot"}},
        ])
        assert review_dedup.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_dismissed(self, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "DISMISSED", "user": {"login": "bot"}},
        ])
        assert review_dedup.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_other_users(self, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "COMMENTED", "user": {"login": "alice"}},
        ])
        assert review_dedup.fetch_bot_reviews("org/repo", "1") == []

    def test_api_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "")
        assert review_dedup.fetch_bot_reviews("org/repo", "1") == []


class TestCheckReviewAlreadyPosted:
    def test_no_reviews_returns_empty(self):
        assert review_dedup.check_review_already_posted([], "some body") == []

    def test_match_returns_ids(self):
        bot_reviews = [
            {"id": 42, "body": "some body text here", "state": "COMMENTED"},
        ]
        assert review_dedup.check_review_already_posted(bot_reviews, "some body text here") == [42]

    def test_zero_similarity_not_matched(self):
        bot_reviews = [
            {"id": 42, "body": "completely different content", "state": "COMMENTED"},
        ]
        assert review_dedup.check_review_already_posted(bot_reviews, "unrelated words here") == []

    def test_partial_overlap_below_threshold(self):
        shared = "alpha bravo charlie delta echo foxtrot golf hotel"
        different = "india juliet kilo lima mike november oscar papa quebec romeo sierra tango"
        bot_reviews = [
            {"id": 42, "body": f"{shared} {different}", "state": "COMMENTED"},
        ]
        assert review_dedup.check_review_already_posted(
            bot_reviews, f"{shared} unique words not in review",
        ) == []
