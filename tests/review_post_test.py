"""Tests for the `review-post` command as a whole — `cli.review_post.run_post`
and the script's `--dry-run` path, end to end: what reaches the payload from a
review a fix pass has already worked through.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr.state import PostTracking
from core.serde import from_dict as serde_from_dict
import review.dedup
from review.record import Disposition, LedgerLine, ProseSection, TriageEntry
from review.types import PriorDisposition, ReviewMeta, review_meta_from_dict
from review_record_support import finding, record, store_record


class TestDryRunIntegration:
    """Integration tests exercising the review-post --dry-run code path via subprocess."""

    from pathlib import Path as _Path
    _REPO_ROOT = _Path(__file__).resolve().parent.parent
    _REVIEW_POST = _REPO_ROOT / "ai" / "bin" / "review-post"

    REVIEW = record(
        finding("M1", "missing error check", path="handler.go", line=11),
        finding("S1", "unclear variable name", path="handler.go", line=25),
        finding("N1", "trailing whitespace", path="config.sh", line=2),
        verdict="Request changes.",
        triage=(TriageEntry("handler.go", "**Tier 2** (application logic)"),),
        ledger=(
            LedgerLine("M4", PriorDisposition.FIXED, "handler.go"),
            LedgerLine("S7", PriorDisposition.STILL_OPEN, "config.sh"),
        ),
    )

    DIFF_TEXT = (
        "diff --git a/handler.go b/handler.go\n"
        "--- a/handler.go\n"
        "+++ b/handler.go\n"
        "@@ -10,3 +10,5 @@ func main() {\n"
        "     existing()\n"
        "+    added()\n"
        "+    alsoAdded()\n"
        "     kept()\n"
        "diff --git a/config.sh b/config.sh\n"
        "--- a/config.sh\n"
        "+++ b/config.sh\n"
        "@@ -1,3 +1,4 @@\n"
        " #!/bin/bash\n"
        "+set -e\n"
        " echo hello\n"
    )

    def _setup_review(self, tmp_path, rec=None, **meta):
        """Write the review through the store into tmp_path."""
        review_dir = tmp_path / "test-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        payload = {"repo": "test/repo", "head_sha": "abc123def456", **meta}
        store_record(
            review_file, rec if rec is not None else self.REVIEW,
            review_meta_from_dict(payload),
        )
        return review_file

    @classmethod
    def _gh_stub(cls, tmp_path):
        """A `gh` on PATH answering the one API call a dry run makes.

        The run fetches the PR diff to place findings on lines. Without a stub
        the subprocess asks the real API about a repo that does not exist, so
        what these tests assert would depend on the network answering.
        """
        bin_dir = tmp_path / "stub-bin"
        bin_dir.mkdir(exist_ok=True)
        diff_file = bin_dir / "pr.diff"
        diff_file.write_text(cls.DIFF_TEXT)
        stub = bin_dir / "gh"
        stub.write_text(f"#!/bin/bash\ncat {diff_file}\n")
        stub.chmod(0o755)
        return bin_dir

    def _run_dry_run(self, review_file, tmp_path, extra_args=None):
        """Run review-post --dry-run and return the CompletedProcess."""
        cmd = [
            sys.executable, str(self._REVIEW_POST),
            "--pr", "42",
            "--review-file", str(review_file),
            "--dry-run",
        ]
        if extra_args:
            cmd.extend(extra_args)
        import os
        env = {
            **os.environ, "NO_COLOR": "1", "TERM": "dumb",
            "PATH": f"{self._gh_stub(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        }
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )

    @staticmethod
    def _extract_json(stdout):
        """Extract the JSON object from stdout which may contain log lines."""
        # The JSON payload is printed between blank lines. Use greedy match
        # to find the outermost { ... } — lazy .*? would stop at the first
        # } at column 0, truncating nested objects.
        match = re.search(r"^\{.*^\}", stdout, re.MULTILINE | re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON payload found in stdout: {stdout!r}")

    def test_dry_run_exits_0(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_dry_run_outputs_json_payload(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "body" in payload or "comments" in payload

    def test_dry_run_omits_file_triage_from_body(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "File Triage" not in payload["body"]
        assert "Tier 2" not in payload["body"]

    def test_dry_run_omits_prior_findings_from_body(self, tmp_path):
        """A ledger reaching review-post unstripped stays out of the posted body.

        The full pipeline strips it in `post_process_record`, so this covers
        the path that skips that pass: a review written by the agent protocol
        directly, or a `--post` of a file never post-processed. Its IDs number
        the prior review, so posting them beside this review's findings shows a
        reader two numbering schemes with nothing telling them apart.
        """
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "Prior findings" not in payload["body"]
        # The prior IDs themselves, which are what would confuse a reader.
        assert "[M4]" not in payload["body"]
        assert "[S7]" not in payload["body"]

    def test_dry_run_with_severity_filter(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path, extra_args=["--severity", "M"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        # With only M severity, the body/comments should reference M1 but not S1 or N1
        payload_text = json.dumps(payload)
        assert "M1" in payload_text
        assert "S1" not in payload_text
        assert "N1" not in payload_text

    def test_dry_run_with_missing_review_file_exits_nonzero(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist.md"
        result = self._run_dry_run(nonexistent, tmp_path)
        assert result.returncode != 0

    def test_dry_run_without_repo_in_meta_exits_nonzero(self, tmp_path):
        review_dir = tmp_path / "no-repo-meta"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        # meta.json present but missing the 'repo' field
        store_record(review_file, self.REVIEW, ReviewMeta(head_sha="abc123"))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode != 0
        assert "repo" in result.stderr.lower() or "meta" in result.stderr.lower()

    def test_a_review_written_for_another_branch_is_refused(self, tmp_path):
        """The artifact keys on the PR; the run lock keys on the branch.

        Two runs that resolve different targets take different locks and so do
        not contend, yet they can share one `review.md`. Posting reads whatever
        is on disk, so without this the publish path could send a review another
        run is still writing — or one belonging to a different branch entirely.
        """
        review_dir = tmp_path / "foreign-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, self.REVIEW, review_meta_from_dict({
            "repo": "test/repo", "head_sha": "abc123def456",
            "head_ref": "someone-else/feat/x",
        }))

        result = self._run_dry_run(
            review_file, tmp_path, extra_args=["--expect-ref", "isaac/feat/mine"])

        assert result.returncode != 0
        assert "someone-else/feat/x" in result.stderr
        assert "isaac/feat/mine" in result.stderr

    def test_a_review_written_for_this_branch_is_posted(self, tmp_path):
        """Pairs with the case above: the guard must not refuse everything."""
        review_dir = tmp_path / "own-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, self.REVIEW, review_meta_from_dict({
            "repo": "test/repo", "head_sha": "abc123def456",
            "head_ref": "isaac/feat/mine",
        }))

        result = self._run_dry_run(
            review_file, tmp_path, extra_args=["--expect-ref", "isaac/feat/mine"])

        assert result.returncode == 0

    def test_a_sidecar_naming_a_forge_renders_links_on_it(self, tmp_path):
        """End to end, through the process that actually posts.

        `review-post` is spawned with a review file and never reads a remote, so
        the sidecar is the only thing that can tell it a link belongs on an
        enterprise host. Asserted here rather than on the builder alone because
        it is the wiring — sidecar to `args` to renderer — that this closes.
        """
        review_dir = tmp_path / "ghe-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        # A prose reference is what `resolve_permalinks` rewrites, and it only
        # runs with both refs in hand — the stock fixture has neither, so it
        # renders no permalink at all and could not see a wrong forge.
        store_record(
            review_file,
            record(
                finding("M1", "missing error check, see handler.go:11",
                        path="handler.go", line=11),
                finding("S1", "unclear variable name", path="handler.go", line=25),
                finding("N1", "trailing whitespace", path="config.sh", line=2),
                verdict="Request changes.",
                triage=(TriageEntry("handler.go", "**Tier 2** (application logic)"),),
                ledger=(
                    LedgerLine("M4", PriorDisposition.FIXED, "handler.go"),
                    LedgerLine("S7", PriorDisposition.STILL_OPEN, "config.sh"),
                ),
            ),
            review_meta_from_dict({
                "repo": "test/repo", "head_sha": "abc123def456",
                "head_ref": "feat/x", "base_ref": "main",
                "host": "ghe.acme.com",
            }),
        )

        result = self._run_dry_run(review_file, tmp_path)

        assert result.returncode == 0
        assert "https://ghe.acme.com/test/repo/blob/" in result.stdout
        assert "https://github.com/test/repo/blob/" not in result.stdout

    def test_a_sidecar_naming_no_branch_is_posted(self, tmp_path):
        """A review written before the field existed still publishes.

        The sidecar is read leniently everywhere else for the same reason, and
        refusing here would strand every review already on disk.
        """
        result = self._run_dry_run(
            self._setup_review(tmp_path),
            tmp_path, extra_args=["--expect-ref", "isaac/feat/mine"])

        assert result.returncode == 0

    def test_a_caller_naming_no_branch_still_posts(self, tmp_path):
        """`--expect-ref` is the caller volunteering what it is publishing for.

        A direct `review-post` invocation that does not know its branch is not
        an error — the guard is for the dispatcher that does.
        """
        review_dir = tmp_path / "unclaimed"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, self.REVIEW, review_meta_from_dict({
            "repo": "test/repo", "head_sha": "abc123def456",
            "head_ref": "someone-else/feat/x",
        }))

        result = self._run_dry_run(review_file, tmp_path)

        assert result.returncode == 0

    def test_dry_run_includes_static_analysis(self, tmp_path):
        rec = record(
            finding("M1", "missing error check", path="handler.go", line=11),
            summary="Clean refactor.",
            verdict="Request changes.",
            static_analysis=(
                "### Nesting depth\n"
                "1 violation in 1 of 3 files checked\n\n"
                "- **`config.sh:42`** — depth 5 exceeds limit 4 (in main())"
            ),
        )
        review_dir = tmp_path / "sa-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, rec, review_meta_from_dict({
            "repo": "test/repo", "head_sha": "abc123def456",
        }))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "Nesting depth" in payload["body"]
        assert "depth 5 exceeds limit 4" in payload["body"]

    def test_dry_run_auto_discovers_unknown_section(self, tmp_path):
        rec = record(
            finding("M1", "missing error check", path="handler.go", line=11),
            summary="Clean refactor.",
            verdict="Request changes.",
            extra=(ProseSection(
                "Performance Notes",
                "Consider caching the DB query at handler.go:15.",
            ),),
        )
        review_dir = tmp_path / "autodiscovery-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, rec, review_meta_from_dict({
            "repo": "test/repo", "head_sha": "abc123def456",
        }))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "Performance Notes" in payload["body"]
        assert "caching the DB query" in payload["body"]


class TestPostSkipsResolvedAndDeclinedFindings:
    """What reaches the payload from a review a fix pass has already worked
    through: not the findings it ticked, and not the ones it declined."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    REVIEW = record(
        finding("S1", "already fixed by the fix pass", path="file.go", line=4,
                checkbox=True, disposition=Disposition.FIXED),
        finding("S2", "declined on the merits.", path="file.go", line=5,
                checkbox=True, disposition=Disposition.DECLINED,
                reason="the tradeoff is deliberate"),
        finding("S3", "genuinely open", path="file.go", line=6, checkbox=True),
        summary="Ok",
    )

    def _run_dry(self, rp, tmp_path, rec=None) -> int:
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir(exist_ok=True)
        review_file = review_dir / "review.md"
        store_record(
            review_file, rec if rec is not None else self.REVIEW,
            ReviewMeta(head_sha="aaa1111bbb2222"),
        )

        args = argparse.Namespace()
        args.repo = "org/repo"
        args.pr = "1"
        args.review_file = str(review_file)
        args.dry_run = True
        args.submit = False
        args.chunk_size = 30
        args.severity = "M,S,N,I"
        args.debug = False

        with patch.object(rp, "_get_diff", return_value=self.DIFF):
            return rp.run_post(MagicMock(), args, "org/repo",
                                rp.ReviewMeta(repo="org/repo"), review_file)

    def _dry_run_payload(self, rp, tmp_path, capsys, rec=None):
        assert self._run_dry(rp, tmp_path, rec) == 0
        out = capsys.readouterr().out
        return json.loads(out[out.index("{"):out.rindex("}") + 1])

    def test_only_the_open_finding_is_commented_on(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        bodies = [c["body"] for c in payload["comments"]]
        assert len(bodies) == 1
        assert "genuinely open" in bodies[0]

    def test_a_fixed_finding_reaches_neither_comments_nor_body(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        whole = json.dumps(payload)
        assert "already fixed by the fix pass" not in whole

    def test_a_declined_finding_is_stated_in_the_body_only(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        assert "declined on the merits" in payload["body"]
        assert not any("declined on the merits" in c["body"] for c in payload["comments"])

    def test_a_wholly_resolved_review_posts_its_body_alone(self, rp, tmp_path, capsys):
        rec = record(
            finding("S1", "already fixed", path="file.go", line=4,
                    checkbox=True, disposition=Disposition.FIXED),
            summary="Every finding is resolved.",
        )
        payload = self._dry_run_payload(rp, tmp_path, capsys, rec=rec)
        assert "comments" not in payload
        assert "Every finding is resolved." in payload["body"]
        assert "already fixed" not in payload["body"]


class TestDuplicateFindingsAreNotReposted:
    """A finding already on the PR is posted nowhere: not inline, and not in
    the summary. Dedup runs the real `dedup_against_posted` against a stubbed
    lookup of what the bot already said, so what is under test is how the
    poster routes the duplicates it returns."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    DUPLICATE = "the conversion error is discarded and nothing logs it"

    REVIEW = record(
        finding("S1", DUPLICATE, path="file.go", line=4),
        finding("S2", "a separate problem nobody has raised", path="file.go", line=5),
        finding("N1", "outside the diff, so it goes in the body", path="elsewhere.go", line=9),
        summary="Ok",
    )

    def _post(self, rp, tmp_path):
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, self.REVIEW, ReviewMeta(head_sha="aaa1111bbb2222"))

        args = argparse.Namespace(
            repo="org/repo", pr="1", review_file=str(review_file),
            dry_run=False, submit=False, chunk_size=30,
            severity="M,S,N,I", debug=False,
        )
        pr_data = rp.PRData(viewer_login="bot", head_sha="aaa1111bbb2222",
                            head_ref="feat", base_ref="main", reviews=[])
        already_posted = review.dedup.PostedFindings(
            findings=[review.dedup.PostedFinding("file.go", self.DUPLICATE)])

        payloads = []

        def capture(endpoint, **kw):
            if kw.get("input_text"):
                payloads.append(json.loads(kw["input_text"]))
            return {"id": 42}

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(review.dedup, "_fetch_bot_comments", return_value=already_posted),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", side_effect=capture),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp.run_post(MagicMock(), args, "org/repo",
                        rp.ReviewMeta(repo="org/repo"), review_file)

        review_payload = next(p for p in payloads if "body" in p)
        tracking = serde_from_dict(
            PostTracking, json.loads((review_dir / "post.jsonl").read_text()))
        return review_payload, tracking

    def test_a_duplicate_is_not_reposted_in_the_body(self, rp, tmp_path):
        payload, _ = self._post(rp, tmp_path)
        assert self.DUPLICATE not in json.dumps(payload)

    def test_the_new_finding_and_the_out_of_diff_finding_still_post(self, rp, tmp_path):
        payload, _ = self._post(rp, tmp_path)
        assert [c["path"] for c in payload.get("comments", [])] == ["file.go"]
        assert "a separate problem nobody has raised" in payload["comments"][0]["body"]
        assert "outside the diff, so it goes in the body" in payload["body"]

    def test_tracking_counts_add_up_to_the_findings(self, rp, tmp_path):
        """`skipped_count` is what was not posted; the out-of-diff nit is a
        body finding, not a skip, and is counted once."""
        _, tracking = self._post(rp, tmp_path)
        assert (tracking.inline_count, tracking.body_count, tracking.skipped_count) == (1, 1, 1)


class TestACleanReviewStillPosts:
    """A review with no findings is posted as its body alone, and tracked.

    A caller that approves a PR submits the pending review this leaves, read
    back from `post.jsonl`. With nothing posted there was nothing to submit, so
    the approval went out with an empty body and the summary, verdict and
    static-analysis results never reached the PR."""

    STATIC_ANALYSIS = (
        "### Nesting depth\n\n"
        "1 violation in 1 of 3 files checked\n\n"
        "- [ ] **[SA1]** **`tasks/check.py:116`** — depth 3 exceeds limit 2"
    )

    CLEAN = record(
        summary="No findings across 3 files in 1 group.",
        verdict="Approve — clean review.",
        static_analysis=STATIC_ANALYSIS,
    )

    def _post(self, rp, tmp_path, rec, models=""):
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        store_record(review_file, rec, ReviewMeta(head_sha="aaa1111bbb2222", models=models))

        args = argparse.Namespace(
            repo="org/repo", pr="1", review_file=str(review_file),
            dry_run=False, submit=False, chunk_size=30,
            severity="M,S,N,I", debug=False,
        )
        pr_data = rp.PRData(viewer_login="bot", head_sha="aaa1111bbb2222",
                            head_ref="feat", base_ref="main", reviews=[])
        payloads = []

        def capture(endpoint, **kw):
            if kw.get("input_text"):
                payloads.append(json.loads(kw["input_text"]))
            return {"id": 42}

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=""),
            patch.object(rp, "fetch_bot_reviews", return_value=review.dedup.BotReviews()),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=rp.PendingReview()),
            patch("gh.client.api_json", side_effect=capture),
        ):
            code = rp.run_post(MagicMock(), args, "org/repo",
                               rp.ReviewMeta(repo="org/repo"), review_file)
        return code, payloads, review_dir / "post.jsonl"

    def test_the_body_carries_summary_verdict_and_static_analysis(self, rp, tmp_path):
        code, payloads, _ = self._post(rp, tmp_path, self.CLEAN)
        assert code == 0
        [payload] = payloads
        assert "comments" not in payload
        assert "No findings across 3 files" in payload["body"]
        assert "**Verdict: Approve.** No must-fix or should-fix finding is open." in payload["body"]
        assert "depth 3 exceeds limit 2" in payload["body"]
        assert "Review findings" not in payload["body"]

    def test_the_pending_review_is_tracked_for_the_approver(self, rp, tmp_path):
        _, _, post_file = self._post(rp, tmp_path, self.CLEAN)
        tracking = serde_from_dict(PostTracking, json.loads(post_file.read_text()))
        assert tracking.review_id == 42
        assert tracking.submitted is False

    # passes-at-base: pins the verdict post.jsonl records across the move off the sections registry
    def test_post_tracking_records_the_record_verdict(self, rp, tmp_path):
        _, _, post_file = self._post(rp, tmp_path, self.CLEAN)
        tracking = serde_from_dict(PostTracking, json.loads(post_file.read_text()))
        assert tracking.verdict == "Approve — clean review."

    def test_the_body_carries_the_models_meta_json_records(self, rp, tmp_path):
        code, payloads, _ = self._post(rp, tmp_path, self.CLEAN, models="single m (low)")
        assert code == 0
        [payload] = payloads
        assert payload["body"].endswith("<sub>Models: single m (low)</sub>")

    def test_a_review_with_nothing_at_all_posts_nothing(self, rp, tmp_path):
        code, payloads, post_file = self._post(rp, tmp_path, record())
        assert code == 0
        [payload] = payloads
        assert "**Verdict: Approve.** No must-fix or should-fix finding is open." in payload["body"]
        assert post_file.exists()
