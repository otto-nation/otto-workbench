"""Tests for the GitHub side of posting a review — `review.posting`'s chunked
posting, retry on unresolvable lines, submission and tracking, and the
`gh.pr_reads` / `gh.client` lookups it depends on, including how each reports a
lookup that could not be answered.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr.state import PostedAs, PostEvent, PostTracking
from core.proc import CmdResult
from core.serde import from_dict as serde_from_dict
import review.posting

# A GitHub outage as gh reports it: nothing on stdout, the status line on
# stderr. Shared so a failure-path test never has to restate the shape.
_API_UNAVAILABLE = CmdResult(1, "", "gh: Service unavailable (HTTP 503)")


class TestWritePostTracking:
    def _review_dir(self, tmp_path):
        """Create and return the folder-layout review directory."""
        d = tmp_path / "test-review"
        d.mkdir()
        return d

    def _read_tracking(self, d):
        return serde_from_dict(PostTracking, json.loads((d / "post.jsonl").read_text()))

    def test_submitted_true(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[123], commit_id="abc",
            inline_count=5, body_count=2, skipped_count=1, submitted=True,
        ))
        tracking = self._read_tracking(d)
        assert tracking.submitted is True

    def test_submitted_defaults_false(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[123], commit_id="abc",
            inline_count=5, body_count=2, skipped_count=1,
        ))
        tracking = self._read_tracking(d)
        assert tracking.submitted is False

    def test_review_id_from_review_ids(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[456], commit_id="abc",
            inline_count=3, body_count=1,
        ))
        tracking = self._read_tracking(d)
        assert tracking.review_id == 456
        assert tracking.review_ids == [456]
        assert tracking.chunk_count == 1

    def test_list_of_review_ids_with_chunk_count(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[100, 200, 300], commit_id="abc",
            inline_count=90, body_count=5, skipped_count=2,
            submitted=True, chunk_count=3,
        ))
        tracking = self._read_tracking(d)
        assert tracking.review_id == 100
        assert tracking.review_ids == [100, 200, 300]
        assert tracking.chunk_count == 3
        assert tracking.submitted is True


class TestFormatSubmitCommand:
    def test_produces_correct_command(self, rp):
        result = rp._format_submit_command("org/repo", "42", 12345)
        assert result == "gh api repos/org/repo/pulls/42/reviews/12345/events --method POST -f event=COMMENT"


class TestChunkComments:
    def test_no_chunking_under_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(10)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 10

    def test_exact_boundary_not_chunked(self, rp):
        comments = [{"body": f"c{i}"} for i in range(30)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 30

    def test_splits_above_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(82)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 3
        assert [len(c) for c in chunks] == [30, 30, 22]

    def test_empty_input(self, rp):
        chunks = rp._chunk_comments([], 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 0


class TestCheckExistingPending:
    def test_returns_review_id(self, rp):
        reviews = json.dumps([{"id": 12345, "state": "PENDING"}])
        with patch("gh.client.api", return_value=CmdResult(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1").review_id == 12345

    def test_returns_none_when_no_pending(self, rp):
        reviews = json.dumps([{"id": 1, "state": "APPROVED"}])
        with patch("gh.client.api", return_value=CmdResult(0, reviews)):
            found = rp._check_existing_pending("org/repo", "1")
        assert found.review_id is None
        # Asked and answered: no pending review exists.
        assert found.looked

    def test_returns_none_for_empty_list(self, rp):
        with patch("gh.client.api", return_value=CmdResult(0, "[]")):
            found = rp._check_existing_pending("org/repo", "1")
        assert found.review_id is None
        assert found.looked

    def test_a_failed_lookup_is_not_an_absent_pending_review(self, rp):
        """The two were the same value, and the caller posts on the strength
        of it — GitHub allows one pending review per user."""
        with patch("gh.client.api", return_value=CmdResult(1)):
            found = rp._check_existing_pending("org/repo", "1")
        assert found.review_id is None
        assert found.looked is False

    def test_api_failure_warns_with_the_cause(self, rp, capsys):
        # None also means "no pending review", and a caller that reads it that
        # way opens a second one — so the failure has to be audible.
        failure = _API_UNAVAILABLE
        with patch("gh.client.api", return_value=failure):
            found = rp._check_existing_pending("org/repo", "1")
        assert found.looked is False
        assert "HTTP 503" in capsys.readouterr().err


class TestPostReview:
    PAYLOAD = {"body": "test", "commit_id": "abc123"}

    def test_no_existing_pending(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api", return_value=CmdResult(0, '{"id": 42}')),
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}

    def test_deletes_existing_pending(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview(999)),
            patch("gh.client.api", return_value=CmdResult(0, '{"id": 42}')) as mock_api,
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}
            delete_calls = [c for c in mock_api.call_args_list
                            if c.kwargs.get("method") == "DELETE"]
            assert len(delete_calls) == 1

    def test_with_submit(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api", return_value=CmdResult(0, '{"id": 42}')) as mock_api,
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD, submit=True)
        assert result == {"id": 42}
        sent = json.loads(mock_api.call_args.kwargs["input_text"])
        assert sent["event"] == "COMMENT"


class TestSubmitReview:
    def test_success(self, rp):
        with patch("gh.client.api", return_value=CmdResult(0, '{"ok": true}')) as mock_api:
            assert rp._submit_review("org/repo", "1", 42) is True
        assert mock_api.call_args[0][0] == "repos/org/repo/pulls/1/reviews/42/events"

    def test_failure_warns(self, rp, capsys):
        with patch("gh.client.api", return_value=CmdResult(1, '{"message": "bad request"}')):
            assert rp._submit_review("org/repo", "1", 42) is False
        assert "Failed to submit" in capsys.readouterr().err


class TestFetchPrRefs:
    def test_success(self, rp):
        pr_json = json.dumps({
            "head": {"sha": "abc123", "ref": "feat/branch"},
            "base": {"ref": "main"},
        })
        with patch("gh.client.api", return_value=CmdResult(0, pr_json)):
            meta = rp._fetch_pr_refs("org/repo", "1")
            assert meta["head_sha"] == "abc123"
            assert meta["head_ref"] == "feat/branch"
            assert meta["base_ref"] == "main"

    def test_failure_exits(self, rp):
        with (
            patch("gh.client.api", return_value=CmdResult(1)),
            pytest.raises(SystemExit),
        ):
            rp._fetch_pr_refs("org/repo", "1")


class TestGetDiff:
    def test_success(self, rp):
        with patch("gh.client.api", return_value=CmdResult(0, "diff --git a/f b/f\n")):
            assert rp._get_diff("org/repo", "1") == "diff --git a/f b/f\n"

    def test_failure_returns_empty(self, rp):
        with patch("gh.client.api", return_value=CmdResult(1)):
            assert rp._get_diff("org/repo", "1") == ""


class TestIsLineResolutionError:
    def test_matching_text(self, rp):
        assert rp._is_line_resolution_error("Line could not be resolved to a position") is True

    def test_non_matching_text(self, rp):
        assert rp._is_line_resolution_error("Something else went wrong") is False

    def test_case_insensitive(self, rp):
        assert rp._is_line_resolution_error("LINE COULD NOT BE RESOLVED") is True


class TestHeadShaRegex:
    def test_standard_sha(self, rp):
        text = "<!-- head_sha: abc123def456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc123def456"

    def test_uppercase_hex_does_not_match(self, rp):
        text = "<!-- head_sha: ABC123DEF456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is None

    def test_short_sha_matches(self, rp):
        text = "<!-- head_sha: abc1234 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc1234"


class TestReclassifyAndRetry:
    DIFF_OLD = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )
    DIFF_NEW = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -40,3 +40,10 @@\n"
        "+line\n"
    )

    def _make_args(self, repo="org/repo", pr="1"):
        import argparse
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = "/tmp/test-review.md"
        args.submit = False
        return args

    def test_reclassify_recovers_inline_with_fresh_diff(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        body_f = rp.Finding(
            id="N1", severity="N", seq=1, path="file.go", line=None,
            end_line=None, body="Nit", full_path="file.go",
            classification="file_level", skip_reason="no line number",
        )

        with (
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_NEW),
            patch("review.posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [body_f],
                "abc123", 30, {"M", "N"}, False,
            )
            assert len(inline) == 1
            assert inline[0].posted_id == "M1"
            assert results == [{"id": 42}]

    def test_reclassify_demotes_all_when_retry_also_fails(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )

        call_count = [0]
        def failing_then_succeeding(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise rp.LineResolutionError("still broken")
            return [{"id": 99}]

        with (
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_NEW),
            patch("review.posting._post_chunked_review", side_effect=failing_then_succeeding),
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [],
                "abc123", 30, {"M"}, False,
            )
            assert len(inline_comments) == 0
            assert len(inline) == 0
            assert results == [{"id": 99}]

    def test_reclassify_preserves_skipped_findings_in_body(self, rp):
        inline_f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=5,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        skipped_f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern",
            classification="skipped", skip_reason="general finding",
        )

        with (
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_NEW),
            patch("review.posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
        ):
            _, _, body, _, _ = rp._reclassify_and_retry(
                self._make_args(), [inline_f], [skipped_f],
                "abc123", 30, {"M", "I"}, False,
            )
            assert any(f.body == "Good pattern" for f in body)


class TestFormatCommentBody:
    def test_includes_sha_drift_header(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=10,
            end_line=None, body="Fix this", posted_id="M1",
        )
        body = rp._format_comment_body([f], {"M"}, "aaa1111", "bbb2222", rp.NewCommits(3))
        assert "aaa1111" in body
        assert "bbb2222" in body
        assert "3 new commits" in body
        assert "Fix this" in body

    def test_single_commit_no_plural(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1,
            end_line=None, body="body", posted_id="S1",
        )
        body = rp._format_comment_body([f], {"S"}, "aaa", "bbb", rp.NewCommits(1))
        assert "1 new commit)" in body
        assert "commits" not in body

    def test_renumbers_findings(self, rp):
        findings = [
            rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=1,
                       end_line=None, body="first"),
            rp.Finding(id="M2", severity="M", seq=2, path="b.go", line=2,
                       end_line=None, body="second"),
        ]
        body = rp._format_comment_body(findings, {"M"}, "aaa", "bbb", rp.NewCommits(1))
        assert "[M1]" in body
        assert "[M2]" in body


class TestHandleChunkFailure:
    def test_exits_with_error(self, rp):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [])

    def test_logs_partial_post(self, rp, capsys):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [{"id": 100}])
        err = capsys.readouterr().err
        assert "Partial post" in err
        assert "100" in err


class TestCountNewCommits:
    def test_finds_review_sha_and_counts_after(self, rp):
        commits = [
            {"sha": "aaa111"},
            {"sha": "bbb222"},
            {"sha": "ccc333"},
        ]
        with patch("gh.client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "bbb222").count == 1

    def test_no_match_returns_total(self, rp):
        commits = [{"sha": "aaa"}, {"sha": "bbb"}]
        with patch("gh.client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "zzz").count == 2

    def test_a_failed_count_is_not_a_branch_that_has_not_moved(self, rp):
        """0 was also the answer for "nothing new", so the drift banner said
        the branch had not moved when nobody had managed to look."""
        with patch("gh.client.api", return_value=CmdResult(1)):
            drift = rp._count_new_commits("org/repo", "1", "aaa")
        assert drift.count == 0
        assert drift.counted is False

    def test_prefix_match(self, rp):
        commits = [{"sha": "aabbccdd1234"}, {"sha": "eeff5678"}]
        with patch("gh.client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "aabbccdd").count == 1


# ── A lookup that failed must not read as an authoritative empty answer ─────


class TestUnansweredLookupsAreAudible:
    """#1364: each of these returned the same value for "no" and "could not ask".

    The consumers act on that value by publishing, so the tests assert what
    reaches the reader rather than only the type.
    """

    def test_an_unanswered_pending_check_says_why_a_duplicate_may_follow(
            self, rp, capsys):
        """GitHub allows one PENDING review per user, so posting into an
        unanswered check is how a run collides with one it could not see."""
        with patch("gh.pr_reads._check_existing_pending",
                   return_value=rp.PendingReview(looked=False)), \
             patch("gh.client.api", return_value=CmdResult(0, "{}")):
            review.posting._post_chunked_review(
                "org/repo", "1", "abc", "body", [], 10, False)

        assert "Could not check for an existing PENDING review" in capsys.readouterr().err

    def test_an_answered_pending_check_says_nothing(self, rp, capsys):
        """The control: the warning must not fire on the ordinary path."""
        with patch("gh.pr_reads._check_existing_pending",
                   return_value=rp.PendingReview()), \
             patch("gh.client.api", return_value=CmdResult(0, "{}")):
            review.posting._post_chunked_review(
                "org/repo", "1", "abc", "body", [], 10, False)

        assert "existing PENDING review" not in capsys.readouterr().err

    def test_an_uncounted_drift_does_not_claim_the_branch_stood_still(self, rp):
        """"0 new commits" reads as "nothing changed" — the opposite of the
        warning this banner exists to give."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="a.py", line=1,
                       end_line=None, body="b")
        body = rp._format_comment_body(
            [f], {"M"}, "aaa1111", "bbb2222", rp.NewCommits(counted=False))

        assert "0 new commit" not in body
        assert "could not be read" in body

    def test_a_counted_drift_still_states_the_number(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="a.py", line=1,
                       end_line=None, body="b")
        body = rp._format_comment_body(
            [f], {"M"}, "aaa1111", "bbb2222", rp.NewCommits(3))

        assert "3 new commits" in body
