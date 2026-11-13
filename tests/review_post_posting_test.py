"""Tests for the GitHub side of posting a review — `review.posting`'s chunked
posting, retry on unresolvable lines, submission and tracking, and the
`gh.pr_reads` / `gh.client` lookups it depends on, including how each reports a
lookup that could not be answered.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr.state import PostedAs, PostEvent, PostTracking
from core.proc import CmdResult
from core.serde import from_dict as serde_from_dict, load_file as serde_load_file
import review.dedup
import review.posting
from review.posting import ReviewEvent
from review.types import ReviewMeta
from review_record_support import record

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

    def test_post_tracking_records_no_verdict(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[123], commit_id="abc",
        ))
        path = d / "post.jsonl"
        raw = json.loads(path.read_text())
        assert "verdict" not in raw
        path.write_text(json.dumps({**raw, "verdict": "Approve."}) + "\n")
        loaded = serde_load_file(PostTracking, path)
        assert loaded is not None
        assert loaded.review_id == 123

    def test_leaves_no_partial_file_when_json_dump_raises(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        with patch("json.dump", side_effect=TypeError("boom")):
            with pytest.raises(TypeError):
                rp.write_post_tracking(review, PostTracking(
                    posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
                    review_ids=[1], commit_id="abc",
                ))
        assert not (d / "post.jsonl").exists()


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


class TestPostChunkedReview:
    def test_no_existing_pending(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}),
        ):
            result = rp._post_chunked_review(
                "org/repo", "1", "abc123", "test", [], 30,
            )
            assert result == [{"id": 42}]

    def test_deletes_existing_pending(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview(999)),
            patch("gh.client.api", return_value=CmdResult(0, '{"id": 42}')) as mock_api,
            patch("gh.client.api_json", return_value={"id": 42}),
        ):
            result = rp._post_chunked_review(
                "org/repo", "1", "abc123", "test", [], 30,
            )
            assert result == [{"id": 42}]
            delete_calls = [c for c in mock_api.call_args_list
                            if c.kwargs.get("method") == "DELETE"]
            assert len(delete_calls) == 1

    def test_a_draft_sends_no_event_key(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}) as mock_api,
        ):
            rp._post_chunked_review("org/repo", "1", "abc123", "test", [], 30)
        sent = json.loads(mock_api.call_args.kwargs["input_text"])
        assert "event" not in sent

    def test_publish_is_the_payloads_event(self, rp):
        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}) as mock_api,
        ):
            rp._post_chunked_review(
                "org/repo", "1", "abc123", "test", [], 30,
                publish=ReviewEvent.APPROVE,
            )
        sent = json.loads(mock_api.call_args.kwargs["input_text"])
        assert sent["event"] == "APPROVE"


class TestChunkedEvent:
    """GitHub shows each reviewer's latest review as their state, so the event
    goes on the chunk posted last; earlier chunks are comments."""

    def test_only_the_last_chunk_carries_the_event(self, rp):
        comments = [{"path": "f", "line": i, "body": "x"} for i in range(3)]
        sent = []

        def capture(endpoint, **kw):
            sent.append(json.loads(kw["input_text"]))
            return {"id": len(sent)}

        with (
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", side_effect=capture),
            patch("time.sleep"),
        ):
            rp._post_chunked_review("org/repo", "1", "abc", "body", comments,
                                    chunk_size=1,
                                    publish=ReviewEvent.REQUEST_CHANGES)

        assert [p["event"] for p in sent] == ["COMMENT", "COMMENT", "REQUEST_CHANGES"]


class TestReviewEvent:
    def test_each_event_maps_to_github_and_post_tracking(self):
        assert [(e.github, e.post_event) for e in ReviewEvent] == [
            ("COMMENT", PostEvent.COMMENT),
            ("APPROVE", PostEvent.APPROVE),
            ("REQUEST_CHANGES", PostEvent.REQUESTED_CHANGES),
        ]


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


class TestReclassifyAndRetry:
    DIFF_NEW = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -40,3 +40,10 @@\n"
        "+line\n"
    )
    DIFF_TWO = (
        "diff --git a/a.go b/a.go\n"
        "--- a/a.go\n"
        "+++ b/a.go\n"
        "@@ -8,3 +8,10 @@\n"
        "+line\n"
        "diff --git a/b.go b/b.go\n"
        "--- a/b.go\n"
        "+++ b/b.go\n"
        "@@ -18,3 +18,10 @@\n"
        "+line\n"
    )

    def _make_args(self, repo="org/repo", pr="1"):
        import argparse
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = "/tmp/test-review.md"
        return args

    def _reverse_path_musts(self, rp):
        later = rp.Finding(
            id="M1", severity="M", seq=1, path="b.go", line=20,
            end_line=None, body="error check", full_path="b.go",
            classification="inline",
        )
        earlier = rp.Finding(
            id="M2", severity="M", seq=2, path="a.go", line=10,
            end_line=None, body="lock never released", full_path="a.go",
            classification="inline",
        )
        return [later, earlier]

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
                "abc123", 30, {"M", "N"},
                record=record(), meta=ReviewMeta(),
            )
            assert len(inline) == 1
            assert "**[M1] [must-fix]**" in inline_comments[0]["body"]
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
            rec = record(summary="Clean refactor.")
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [],
                "abc123", 30, {"M"},
                record=rec, meta=ReviewMeta(),
            )
            assert (inline_comments, inline, results) == ([], [], [{"id": 99}])
            assert "Clean refactor." in body_text
            assert "**Verdict: Approve.** No must-fix or should-fix finding is open." in body_text

    def test_reclassify_recovered_inline_comments_keep_record_ids(self, rp):
        posted = {}

        def capture(*a, **kw):
            posted["body"] = a[3]
            posted["comments"] = a[4]
            return [{"id": 42}]

        with (
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_TWO),
            patch("review.posting._post_chunked_review", side_effect=capture),
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
        ):
            rp._reclassify_and_retry(
                self._make_args(), self._reverse_path_musts(rp), [],
                "abc123", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        by_path = {c["path"]: c["body"] for c in posted["comments"]}
        assert "**[M2] [must-fix]**" in by_path["a.go"]
        assert "lock never released" in by_path["a.go"]
        assert "**[M1] [must-fix]**" in by_path["b.go"]
        assert "**[M1]" not in by_path["a.go"]

    def test_reclassify_demoted_body_keeps_record_ids(self, rp):
        posted = {}
        call_count = [0]

        def failing_then_capture(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise rp.LineResolutionError("still broken")
            posted["body"] = a[3]
            posted["comments"] = a[4]
            return [{"id": 99}]

        with (
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_TWO),
            patch("review.posting._post_chunked_review", side_effect=failing_then_capture),
            patch("gh.pr_reads._check_existing_pending", return_value=rp.PendingReview()),
        ):
            rp._reclassify_and_retry(
                self._make_args(), self._reverse_path_musts(rp), [],
                "abc123", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        lock = [line for line in posted["body"].splitlines() if "lock never released" in line]
        err = [line for line in posted["body"].splitlines() if "error check" in line]
        assert posted["comments"] == []
        assert lock and "**[M2] [must-fix]**" in lock[0]
        assert err and "**[M1] [must-fix]**" in err[0]
        assert "**[M1]" not in lock[0]

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
                "abc123", 30, {"M", "I"},
                record=record(), meta=ReviewMeta(),
            )
            assert any(f.body == "Good pattern" for f in body)

    def test_a_retry_after_unresolvable_lines_keeps_the_record_prose(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        captured = []

        def first_fails_then_captures(*a, **kw):
            if not captured:
                captured.append("raised")
                raise rp.LineResolutionError("unresolvable")
            captured.append(a[3])
            return [{"id": 1}]

        rec = record(summary="Clean refactor.")
        with (
            patch("review.dedup.fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch("review.dedup.check_review_already_posted", return_value=[]),
            patch("gh.pr_reads._get_diff", return_value=self.DIFF_NEW),
            patch("review.posting._post_chunked_review", side_effect=first_fails_then_captures),
            patch("review.posting.write_post_tracking"),
        ):
            review.posting._post_and_track(
                self._make_args(),
                [{"path": "file.go", "line": 45, "body": "Fix", "side": "RIGHT"}],
                "initial",
                [f], [], [],
                "abc123", "abc123", 30, {"M"},
                record=rec, meta=ReviewMeta(models="single m (low)"),
            )
        assert "Clean refactor." in captured[1]
        assert "**Verdict: Approve.** No must-fix or should-fix finding is open." in captured[1]
        assert "<sub>Models: single m (low)</sub>" in captured[1]


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
                "org/repo", "1", "abc", "body", [], 10)

        assert "Could not check for an existing PENDING review" in capsys.readouterr().err

    def test_an_answered_pending_check_says_nothing(self, rp, capsys):
        """The control: the warning must not fire on the ordinary path."""
        with patch("gh.pr_reads._check_existing_pending",
                   return_value=rp.PendingReview()), \
             patch("gh.client.api", return_value=CmdResult(0, "{}")):
            review.posting._post_chunked_review(
                "org/repo", "1", "abc", "body", [], 10)

        assert "existing PENDING review" not in capsys.readouterr().err


class TestPostAndTrackRecordsGithubState:
    def _args(self, tmp_path, publish=None):
        import argparse
        d = tmp_path / "rev"
        d.mkdir()
        args = argparse.Namespace(
            repo="org/repo", pr="1",
            review_file=str(d / "review.md"),
            publish=publish,
        )
        return args, d

    def _read(self, d):
        return serde_from_dict(PostTracking, json.loads((d / "post.jsonl").read_text()))

    def test_an_unsubmitted_post_records_pending(self, rp, tmp_path):
        args, d = self._args(tmp_path)
        with (
            patch("review.dedup.fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch("review.dedup.check_review_already_posted", return_value=[]),
            patch("review.posting._post_chunked_review", return_value=[{"id": 11}]),
        ):
            review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        tracking = self._read(d)
        assert (tracking.status, tracking.submitted) == (PostEvent.PENDING.value, False)

    def test_a_submitted_post_records_its_event(self, rp, tmp_path):
        args, d = self._args(tmp_path, publish="approve")
        with (
            patch("review.dedup.fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch("review.dedup.check_review_already_posted", return_value=[]),
            patch("review.posting._post_chunked_review", return_value=[{"id": 11}]),
        ):
            review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        tracking = self._read(d)
        assert (tracking.status, tracking.submitted) == (PostEvent.APPROVE.value, True)

    def test_a_chunked_post_records_its_event_submitted(self, rp, tmp_path):
        args, d = self._args(tmp_path)
        with (
            patch("review.dedup.fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch("review.dedup.check_review_already_posted", return_value=[]),
            patch("review.posting._post_chunked_review",
                  return_value=[{"id": 1}, {"id": 2}]),
        ):
            review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        tracking = self._read(d)
        assert (tracking.status, tracking.submitted) == (PostEvent.COMMENT.value, True)

    def test_dedup_records_the_matched_review_state(self, rp, tmp_path):
        args, d = self._args(tmp_path)
        bots = review.dedup.BotReviews([
            {"id": 99, "body": "body", "state": "APPROVED"},
        ])
        with (
            patch("review.dedup.fetch_bot_reviews", return_value=bots),
            patch("review.dedup.check_review_already_posted", return_value=[99]),
            patch("review.posting._post_chunked_review") as post,
        ):
            review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        post.assert_not_called()
        tracking = self._read(d)
        assert (tracking.status, tracking.submitted) == (PostEvent.APPROVE.value, True)
        assert tracking.head_sha_at_post == "def456"
        assert tracking.review_sha == "def456"

    def test_a_too_large_draft_exits_without_github_writes(self, rp, tmp_path):
        args, d = self._args(tmp_path)
        comments = [{"path": "f", "line": i, "body": "x"} for i in range(3)]
        with (
            patch("review.dedup.fetch_bot_reviews") as fetch,
            patch("gh.client.api") as api,
            patch("gh.client.api_json") as api_json,
            patch("gh.pr_reads._check_existing_pending") as pending,
            patch("time.sleep"),
        ):
            rc = review.posting._post_and_track(
                args, comments, "body", [], [], [],
                "abc123", "def456", 1, {"M"},
                record=record(), meta=ReviewMeta(),
            )
        assert rc == 1
        fetch.assert_not_called()
        api.assert_not_called()
        api_json.assert_not_called()
        pending.assert_not_called()
        assert not (d / "post.jsonl").exists()

    def test_own_draft_without_publish_exits_0_without_write(self, rp, tmp_path):
        args, d = self._args(tmp_path)
        (d / "post.jsonl").write_text(json.dumps({
            "posted_as": "review", "status": "pending", "review_id": 11,
            "submitted": False, "head_sha_at_post": "abc",
        }) + "\n")
        original = (d / "post.jsonl").read_text()
        pr_data = MagicMock(pending_review_id=11)
        with (
            patch("review.dedup.fetch_bot_reviews") as fetch,
            patch("gh.client.api") as api,
            patch("gh.client.api_json") as api_json,
        ):
            rc = review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"}, pr_data,
                record=record(), meta=ReviewMeta(),
            )
        assert rc == 0
        fetch.assert_not_called()
        api.assert_not_called()
        api_json.assert_not_called()
        assert (d / "post.jsonl").read_text() == original

    def test_own_draft_with_publish_exits_1_without_write(self, rp, tmp_path):
        args, d = self._args(tmp_path, publish="comment")
        (d / "post.jsonl").write_text(json.dumps({
            "posted_as": "review", "status": "pending", "review_id": 11,
            "submitted": False, "head_sha_at_post": "abc",
        }) + "\n")
        original = (d / "post.jsonl").read_text()
        pr_data = MagicMock(pending_review_id=11)
        with (
            patch("review.dedup.fetch_bot_reviews") as fetch,
            patch("gh.client.api") as api,
            patch("gh.client.api_json") as api_json,
        ):
            rc = review.posting._post_and_track(
                args, [], "body", [], [], [],
                "abc123", "def456", 30, {"M"}, pr_data,
                record=record(), meta=ReviewMeta(),
            )
        assert rc == 1
        fetch.assert_not_called()
        api.assert_not_called()
        api_json.assert_not_called()
        assert (d / "post.jsonl").read_text() == original
