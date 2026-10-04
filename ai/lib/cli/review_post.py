"""Post a review file to GitHub as a PR review.

Parses a markdown review file (produced by `review`), validates
finding positions against the PR diff, renumbers findings by posted
location (inline first, then body), and creates a PENDING review via
the GitHub API. Pass --submit to submit the review immediately.

Usage:
  review-post --pr NUMBER --review-file PATH
              [--severity M,S,N] [--dry-run]
"""

# doc-group: cli

from __future__ import annotations

import argparse
from pathlib import Path

from core.trail import Trail, add_trail_args
import gh.client
import review.dedup
import review.format
import gh.pr_data
import gh.pr_reads
import review.paths
import review.post_file
import review.posting
import review.sections

# Most of what follows is never referenced in this file. The names are re-exported
# so a test can reach them at `review_post.<name>` and patch one, which the proxy
# installed below forwards to the module that actually defines it. An import an
# editor calls unused is therefore load-bearing — deleting it silently turns the
# patch it serves into a no-op.
import git.client
import core.log
import core.module_proxy
import core.proc
from review.dedup import dedup_against_posted
from review.document import ReviewDocument
from review.paths import read_review_meta
from core.text import plural
from review.types import Finding, ReviewMeta, SEVERITIES
from review.format import (
    CLASS_FILE_LEVEL, CLASS_INLINE,
    classify_findings, format_body_text, format_inline_comment,
    renumber_for_posting, resolve_permalinks,
    _format_path_ref, _format_finding_line, resolve_path, _hunk_end,
    _build_permalink,
)
from gh.client import LineResolutionError
from gh.pr_data import PRData, fetch_pr_data
from gh.pr_reads import (
    _fetch_pr_refs, _get_diff,
    _check_existing_pending, _count_new_commits,
)
from review.dedup import (
    word_set, jaccard, _extract_body_findings, _fetch_bot_comments,
    get_bot_login, fetch_bot_reviews,
    check_review_already_posted, REVIEW_BODY_DEDUP_THRESHOLD,
)
from review.posting import (
    DEFAULT_CHUNK_SIZE, HEAD_SHA_RE, _CHUNK_PATTERN,
    _post_and_track, _post_as_comment, _print_dry_run,
    _chunk_comments, _post_chunked_review, post_review,
    _submit_review, write_post_tracking, _format_submit_command,
    _reclassify_and_retry,
)
from review.sections import ReviewSections

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "review-post"

_SUBMODULES = (
    gh.client, review.dedup, review.format, gh.pr_data, gh.pr_reads, review.paths, review.post_file, review.posting, review.sections, git.client, core.log, core.module_proxy, core.proc,
)

core.module_proxy.install(__name__, _SUBMODULES)


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SCRIPT,
                                     description="Post a review file to GitHub")
    parser.add_argument("--pr", required=True, help="PR number")
    parser.add_argument("--review-file", required=True, help="Path to review markdown file")
    all_sevs = ",".join(s.key for s in SEVERITIES)
    parser.add_argument("--severity", default=all_sevs,
                        help=f"Severity levels to post (comma-separated, default: {all_sevs})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and classify without posting")
    parser.add_argument("--submit", action="store_true",
                        help="Submit the review after posting (default: leave PENDING)")
    parser.add_argument("--expect-ref",
                        help="Refuse the review unless its sidecar names this "
                             "branch as the one it was written for. Opt-in: "
                             "omitted, or a sidecar with no branch recorded, "
                             "always publishes")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help=f"Max inline comments per review chunk (default: {DEFAULT_CHUNK_SIZE})")
    add_trail_args(parser)
    args = parser.parse_args(argv)

    review_path = Path(args.review_file)
    if not review_path.exists():
        core.log.error(f"Review file not found: {review_path}")
        return 1

    sidecar = read_review_meta(review_path.parent)
    if not sidecar.repo:
        core.log.error("Cannot determine repository — meta.json missing or has no 'repo' field")
        return 1
    repo = sidecar.repo
    args.repo = repo
    # Stamped beside `repo` so every posting path reads the host the same way it
    # reads the slug. `review.post_file.run_post` is handed the sidecar and takes it from there;
    # the comment-fallback path only ever sees `args`, and a host it cannot read
    # is an enterprise link rendered on public GitHub.
    args.host = sidecar.host

    # The artifact keys on the PR number and the run lock keys on the branch, so
    # two runs the lock treats as unrelated can still reach one review.md. This
    # is where they are told apart: the caller says which branch it is
    # publishing for, and a sidecar naming a different one is another run's
    # review — possibly one still being written.
    #
    # Both halves are optional and a missing one publishes, which is deliberate
    # rather than lax. A sidecar predating the field has no branch to check, and
    # refusing on that would strand every review already on disk; a caller that
    # does not pass the flag has not claimed a branch to check against.
    if args.expect_ref and sidecar.head_ref and sidecar.head_ref != args.expect_ref:
        core.log.error(
            f"This review was written for {sidecar.head_ref}, not "
            f"{args.expect_ref} — refusing to post another run's review")
        core.log.dim(f"Review file: {review_path}")
        return 1

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": repo, "pr": args.pr},
        debug=args.debug,
    )

    try:
        return review.post_file.run_post(trail, args, repo, sidecar, review_path)
    except KeyboardInterrupt:
        return core.proc.INTERRUPT_RETURNCODE
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
