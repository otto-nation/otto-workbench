"""SHA-drift re-verification when posting a review.

`run_post` re-places findings against the current diff when the PR head moved,
and refuses a non-comment `--event` on that drift.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr.state import PostedAs, PostEvent, PostTracking
from core.serde import from_dict as serde_from_dict
import review.dedup
from review.types import ReviewMeta
from review_record_support import finding, record, store_record


class TestShaDriftReverify:
    """SHA drift should re-verify positions against the current diff and post
    inline, not fall back to a plain issue comment."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    REVIEW = record(
        finding("M1", "Fix this bug", path="file.go", line=5),
        summary="Ok",
    )

    def _make_args(self, tmp_path, repo="org/repo", pr="1", head_sha="aaa1111bbb2222"):
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir(exist_ok=True)
        review_file = review_dir / "review.md"
        store_record(review_file, self.REVIEW, ReviewMeta(head_sha=head_sha, repo=repo))
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = str(review_file)
        args.dry_run = False
        args.submit = False
        args.chunk_size = 30
        args.severity = "M,S,N,I"
        args.debug = False
        return args, review_file

    def test_drift_posts_inline_not_comment(self, rp, tmp_path):
        args, review_file = self._make_args(tmp_path)
        sidecar = rp.ReviewMeta(repo="org/repo")
        trail = MagicMock()

        new_head = "ccc3333ddd4444"
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=new_head, head_ref="feat", base_ref="main",
            reviews=[],
        )

        post_calls = []
        def capture_post(endpoint, **kw):
            post_calls.append(endpoint)
            return {"id": 42}

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(rp, "dedup_against_posted", return_value=(
                [rp.Finding(id="M1", severity="M", seq=1, path="file.go",
                            line=5, end_line=None, body="Fix this bug", classification="inline",
                            full_path="file.go")],
                [],
            )),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", side_effect=capture_post),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp.run_post(trail, args, "org/repo", sidecar, review_file)

        assert any("pulls" in c and "reviews" in c for c in post_calls), \
            f"Expected review API call, got: {post_calls}"
        assert not any("issues" in c for c in post_calls), \
            "Should not fall back to issue comment API"

    def test_the_classification_diff_is_always_fetched(self, rp, tmp_path):
        """Nothing writes a diff into the sidecar, so nothing reads one back
        out: a cached diff only a test ever populates is a branch the pipeline
        never takes, and findings would be placed against it."""
        args, review_file = self._make_args(tmp_path)
        sidecar = rp.ReviewMeta(repo="org/repo")
        trail = MagicMock()

        new_head = "ccc3333ddd4444"
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=new_head, head_ref="feat", base_ref="main",
            reviews=[],
        )

        diff_calls = []
        def capture_diff(repo, pr):
            diff_calls.append(repo)
            return self.DIFF

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", side_effect=capture_diff),
            patch.object(rp, "dedup_against_posted", return_value=([], [])),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp.run_post(trail, args, "org/repo", sidecar, review_file)

        assert len(diff_calls) == 1, "Should fetch fresh diff, not use sidecar"

    def _run_with_finding(self, rp, tmp_path, head_sha, header_sha=None, trail=None):
        """Run run_post with one inline finding; return the post tracking it saved."""
        trail = trail or MagicMock()
        args, review_file = self._make_args(
            tmp_path, head_sha=header_sha or "aaa1111bbb2222")
        sidecar = rp.ReviewMeta(repo="org/repo")
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=head_sha, head_ref="feat", base_ref="main",
            reviews=[],
        )
        finding = rp.Finding(id="M1", severity="M", seq=1, path="file.go",
                             line=5, end_line=None, body="Fix", classification="inline",
                             full_path="file.go")
        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(rp, "dedup_against_posted", return_value=([finding], [])),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp.run_post(trail, args, "org/repo", sidecar, review_file)
        post_file = review_file.parent / "post.jsonl"
        tracking = serde_from_dict(PostTracking, json.loads(post_file.read_text()))
        return tracking

    def _run_with_args(self, rp, args, review_file, head_sha):
        pr_data = rp.PRData(viewer_login="bot", head_sha=head_sha,
                            head_ref="feat", base_ref="main", reviews=[])
        finding = rp.Finding(id="M1", severity="M", seq=1, path="file.go",
                             line=5, end_line=None, body="Fix", classification="inline",
                             full_path="file.go")
        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(rp, "dedup_against_posted", return_value=([finding], [])),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", return_value={"id": 42}),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp.run_post(MagicMock(), args, "org/repo", rp.ReviewMeta(repo="org/repo"), review_file)
        post_file = review_file.parent / "post.jsonl"
        return serde_from_dict(PostTracking, json.loads(post_file.read_text()))

    def test_drift_records_sha_in_tracking(self, rp, tmp_path):
        new_head = "ccc3333ddd4444"
        tracking = self._run_with_finding(rp, tmp_path, new_head)
        assert tracking.review_sha == "aaa1111bbb2222"
        assert tracking.head_sha_at_post == new_head
        assert tracking.sha_drifted is True
        assert tracking.posted_as == PostedAs.REVIEW.value

    def test_an_abbreviated_header_naming_the_head_is_not_drift(self, rp, tmp_path):
        """A header stating the PR head abbreviated is the same commit, not drift.

        Drift is decided by `covers`, which accepts an abbreviation at least
        `MIN_SHA_PREFIX` long; a plain `!=` would flag this review as written
        against another commit and re-verify every position for nothing.
        """
        trail = MagicMock()
        tracking = self._run_with_finding(
            rp, tmp_path, "aaa1111bbb2222", header_sha="aaa1111", trail=trail)
        assert tracking.review_sha == "aaa1111"
        assert tracking.sha_drifted is False
        assert not [c for c in trail.info.call_args_list if c.args[0] == "sha_drift"]

    def test_an_approval_is_refused_when_the_head_moved(self, rp, tmp_path):
        """Approving commits the review never read is the one post that must
        not go out on drift; a comment still can, as it does today."""
        args, review_file = self._make_args(tmp_path)
        args.event = "approve"
        args.submit = True
        pr_data = rp.PRData(viewer_login="bot", head_sha="ccc3333ddd4444",
                            head_ref="feat", base_ref="main", reviews=[])
        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch("gh.client.api_json") as mock_post,
        ):
            rc = rp.run_post(MagicMock(), args, "org/repo", rp.ReviewMeta(repo="org/repo"), review_file)

        assert rc == 1
        mock_post.assert_not_called()
        assert not (review_file.parent / "post.jsonl").exists()

    def test_an_approval_records_its_event_in_post_tracking(self, rp, tmp_path):
        args, review_file = self._make_args(tmp_path)
        args.event = "approve"
        args.submit = True
        tracking = self._run_with_args(rp, args, review_file, head_sha="aaa1111bbb2222")
        assert tracking.status == PostEvent.APPROVE.value
