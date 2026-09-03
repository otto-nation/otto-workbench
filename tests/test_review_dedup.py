import sys
from pathlib import Path
from unittest.mock import patch

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

    def test_api_failure_returns_empty(self):
        with patch("gh_client.login", return_value=""):
            assert review_dedup._fetch_bot_comments("org/repo", "1") == []

    def test_empty_login_returns_empty(self):
        with patch("gh_client.login", return_value=""):
            assert review_dedup._fetch_bot_comments("org/repo", "1") == []
