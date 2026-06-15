"""High-level posting orchestration for review-post.

Handles chunked review submission, SHA-drift fallback (posting as a
comment), reclassification after LineResolutionError, dry-run display,
and post-tracking metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import review_dedup
import review_format
import review_github

from review_common import _err, _info, _warn
from review_findings import Finding
from review_github import LineResolutionError


# ── Constants ───────────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 30
INTER_CHUNK_DELAY = 30

HEAD_SHA_RE = re.compile(r"<!-- head_sha: ([a-f0-9]+) -->")

_CHUNK_PATTERN = re.compile(r"\*\(continued — part (\d+)/(\d+)\)\*")


# ── Chunking ────────────────────────────────────────────────────────────────

def _chunk_comments(
    inline_comments: list[dict], chunk_size: int,
) -> list[list[dict]]:
    """Split inline comments into chunks of at most chunk_size."""
    if not inline_comments or len(inline_comments) <= chunk_size:
        return [inline_comments]
    return [
        inline_comments[i:i + chunk_size]
        for i in range(0, len(inline_comments), chunk_size)
    ]


def _handle_chunk_failure(
    chunk_num: int, total_chunks: int, prior_results: list[dict],
) -> None:
    """Log chunk failure details and exit."""
    _err(f"Failed to post chunk {chunk_num}/{total_chunks}")
    if prior_results:
        posted_ids = [r.get("id", "?") for r in prior_results]
        _warn(f"Partial post: chunks 1-{chunk_num - 1} succeeded (review IDs: {posted_ids})")
    sys.exit(1)


# ── Chunked review posting ──────────────────────────────────────────────────

def _post_chunked_review(
    repo: str, pr: str, commit_id: str,
    body_text: str, inline_comments: list[dict],
    chunk_size: int, submit: bool,
) -> list[dict]:
    """Post a review, chunking if the comment count exceeds chunk_size.

    Chunked reviews are submitted immediately (event=COMMENT) because
    GitHub only allows one PENDING review per user at a time.
    """
    chunks = _chunk_comments(inline_comments, chunk_size)
    is_chunked = len(chunks) > 1
    results: list[dict] = []

    existing_id = review_github._check_existing_pending(repo, pr)
    if existing_id:
        _warn(f"Deleting existing PENDING review #{existing_id}")
        review_github._gh_api(f"repos/{repo}/pulls/{pr}/reviews/{existing_id}", method="DELETE")

    for i, chunk in enumerate(chunks):
        chunk_num = i + 1
        total_chunks = len(chunks)

        payload: dict = {"commit_id": commit_id}

        if i == 0:
            payload["body"] = body_text
        else:
            payload["body"] = f"*(continued — part {chunk_num}/{total_chunks})*"

        if chunk:
            payload["comments"] = chunk

        if is_chunked or submit:
            payload["event"] = "COMMENT"

        if is_chunked:
            _info(f"Posting chunk {chunk_num}/{total_chunks} ({len(chunk)} comments)...")
        else:
            _info(f"Posting review ({len(chunk)} inline)...")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="review-post-", delete=False
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False)
            tmp_path = tmp.name

        try:
            result = review_github._post_with_retries(f"repos/{repo}/pulls/{pr}/reviews", tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        if result is None:
            _handle_chunk_failure(chunk_num, total_chunks, results)

        results.append(result)

        if not is_chunked:
            continue
        _info(f"  Chunk {chunk_num}/{total_chunks} posted: #{result.get('id', '?')}")
        if i < len(chunks) - 1:
            _info(f"  Waiting {INTER_CHUNK_DELAY}s before next chunk...")
            time.sleep(INTER_CHUNK_DELAY)

    return results


def post_review(repo: str, pr: str, payload: dict, submit: bool = False) -> dict | None:
    """Post a review to GitHub, optionally submitting it.

    Legacy single-review interface. Delegates to _post_chunked_review
    with no chunking.
    """
    comments = payload.get("comments", [])
    body_text = payload.get("body", "")
    commit_id = payload["commit_id"]

    results = _post_chunked_review(
        repo, pr, commit_id, body_text, comments,
        chunk_size=len(comments) + 1,
        submit=submit,
    )
    return results[0] if results else None


def _submit_review(repo: str, pr: str, review_id: int) -> bool:
    """Submit a PENDING review with event=COMMENT. Returns True on success."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="review-submit-", delete=False
    ) as tmp:
        json.dump({"event": "COMMENT"}, tmp)
        tmp_path = tmp.name

    try:
        result = review_github._post_with_retries(
            f"repos/{repo}/pulls/{pr}/reviews/{review_id}/events",
            tmp_path,
        )
        if result is None:
            _warn(f"Failed to submit review #{review_id} after {review_github.MAX_RETRIES} attempts")
            return False
        return True
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Post tracking ───────────────────────────────────────────────────────────

def write_post_tracking(
    review_file: str, review_ids: list[int] | int, commit_id: str,
    inline_count: int, body_count: int, skipped_count: int,
    submitted: bool = False,
    chunk_count: int = 1,
    posted_as: str = "review",
    review_sha: str = "",
    head_sha_at_post: str = "",
    sha_drifted: bool = False,
):
    """Write a post tracking entry alongside the review file."""
    post_file = str(Path(review_file).parent / "post.jsonl")

    ids = [review_ids] if isinstance(review_ids, int) else review_ids

    entry = {
        "review_id": ids[0],
        "review_ids": ids,
        "commit_id": commit_id,
        "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inline_count": inline_count,
        "body_count": body_count,
        "skipped_count": skipped_count,
        "submitted": submitted,
        "chunk_count": chunk_count,
        "posted_as": posted_as,
        "review_sha": review_sha,
        "head_sha_at_post": head_sha_at_post,
        "sha_drifted": sha_drifted,
    }
    try:
        with open(post_file, "w") as f:
            json.dump(entry, f)
            f.write("\n")
    except OSError as e:
        _warn(f"Failed to write post tracking ({post_file}): {e}")


# ── SHA-drift fallback ──────────────────────────────────────────────────────

def _format_comment_body(
    findings: list[Finding], severity_filter: set[str],
    review_sha: str, head_sha: str, new_commit_count: int,
) -> str:
    """Format findings as a single comment body for stale-SHA posting."""
    _, body_findings = review_format.renumber_for_posting([], findings)
    body = review_format.format_body_text(body_findings, False, severity_filter)

    header = (
        f"> **Note:** This review was written against commit `{review_sha[:7]}`. "
        f"PR HEAD has since moved to `{head_sha[:7]}` "
        f"({new_commit_count} new commit{'s' if new_commit_count != 1 else ''}). "
        f"Inline positions may be inaccurate — posted as a comment instead of a review.\n"
    )
    return f"{header}\n{body}"


def _post_as_comment(
    args: argparse.Namespace,
    findings: list[Finding], severity_filter: set[str],
    review_sha: str, head_sha: str,
    head_ref: str, base_ref: str,
):
    """Post review as a single PR comment when SHA has drifted."""
    kept, deduped = review_dedup.dedup_against_posted(findings, args.repo, args.pr)
    if deduped:
        _info(f"Skipped {len(deduped)} findings duplicating existing comments")
    findings = kept

    if not findings:
        _warn("No findings to post after dedup")
        write_post_tracking(
            args.review_file, [0], head_sha,
            inline_count=0, body_count=0, skipped_count=len(deduped),
            submitted=True,
            posted_as="comment",
            review_sha=review_sha,
            head_sha_at_post=head_sha,
            sha_drifted=True,
        )
        return

    diff_text = review_github._get_diff(args.repo, args.pr)
    review_format.resolve_permalinks(findings, args.repo, diff_text, head_ref, base_ref)

    new_commits = review_github._count_new_commits(args.repo, args.pr, review_sha)
    body = _format_comment_body(
        findings, severity_filter, review_sha, head_sha, new_commits,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="review-comment-", delete=False,
    ) as tmp:
        json.dump({"body": body}, tmp, ensure_ascii=False)
        tmp_path = tmp.name

    try:
        result = review_github._post_with_retries(
            f"repos/{args.repo}/issues/{args.pr}/comments", tmp_path,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if result is None:
        _err("Failed to post review as comment")
        sys.exit(1)

    comment_id = result.get("id", 0)
    _info(f"Review posted as comment #{comment_id} (SHA drifted: {review_sha[:7]} → {head_sha[:7]})")

    write_post_tracking(
        args.review_file, [comment_id], head_sha,
        inline_count=0, body_count=len(findings), skipped_count=0,
        submitted=True,
        posted_as="comment",
        review_sha=review_sha,
        head_sha_at_post=head_sha,
        sha_drifted=True,
    )


# ── Reclassification after line-resolution failure ──────────────────────────

def _reclassify_and_retry(
    args: argparse.Namespace,
    inline: list[Finding], body_findings: list[Finding],
    commit_id: str, chunk_size: int, severity_filter: set[str],
    submit: bool,
) -> tuple[list[dict], list[Finding], list[Finding], str, list[dict]]:
    """Re-fetch diff and reclassify findings after a LineResolutionError.

    Returns (inline_comments, inline, body_findings, body_text, results).
    """
    _warn("Inline comments could not be resolved — re-fetching diff and retrying")
    fresh_diff = review_github._get_diff(args.repo, args.pr)
    all_findings = list(inline) + [
        f for f in body_findings if f.classification != review_format.CLASS_SKIPPED
    ]
    for f in all_findings:
        f.classification = ""
        f.full_path = ""
        f.skip_reason = ""

    new_inline, new_file_level, new_skipped = review_format.classify_findings(
        all_findings, fresh_diff,
    )
    skipped_body = [f for f in body_findings if f.classification == review_format.CLASS_SKIPPED]
    new_body = new_file_level + new_skipped + skipped_body

    if new_inline:
        _info(f"Reclassified: {len(new_inline)} inline, {len(new_body)} body")
        new_inline, new_body = review_format.renumber_for_posting(new_inline, new_body)
        new_inline_comments = [review_format.format_inline_comment(f) for f in new_inline]
        new_body_text = review_format.format_body_text(new_body, True, severity_filter)
        try:
            results = _post_chunked_review(
                args.repo, args.pr, commit_id,
                new_body_text, new_inline_comments,
                chunk_size, submit,
            )
            return new_inline_comments, new_inline, new_body, new_body_text, results
        except LineResolutionError:
            _warn("Retry still failed — demoting all to body-level")

    _, new_body = review_format.renumber_for_posting([], list(inline) + list(body_findings))
    new_body_text = review_format.format_body_text(new_body, False, severity_filter)
    results = _post_chunked_review(
        args.repo, args.pr, commit_id,
        new_body_text, [],
        chunk_size, submit,
    )
    return [], [], new_body, new_body_text, results


# ── Main post + track ───────────────────────────────────────────────────────

def _post_and_track(
    args: argparse.Namespace,
    inline_comments: list[dict], body_text: str,
    inline: list[Finding],
    body_findings: list[Finding], skipped: list[Finding],
    commit_id: str, head_sha: str,
    chunk_size: int, severity_filter: set[str],
):
    submit = getattr(args, "submit", False)

    # Fetch bot reviews once for both dedup and orphan detection
    bot_reviews = review_dedup.fetch_bot_reviews(args.repo, args.pr)

    # Check for already-posted duplicate
    existing_ids = review_dedup.check_review_already_posted(bot_reviews, body_text)
    if existing_ids:
        _warn(f"Review already posted (review IDs: {existing_ids})")
        write_post_tracking(
            args.review_file, list(existing_ids), commit_id,
            inline_count=0, body_count=0, skipped_count=0,
            submitted=submit, chunk_count=len(existing_ids),
        )
        return

    # Check for orphaned continuation chunks from a prior killed run.
    # Only mark INCOMPLETE chunk sets — if all N/M parts exist, the
    # prior post was complete and should not be struck through.
    # Separate sets handle multiple runs with the same chunk count.
    chunk_sets: list[tuple[int, dict[int, int]]] = []  # [(total, {part: review_id})]
    for rev in bot_reviews:
        m = _CHUNK_PATTERN.search(rev["body"])
        if m:
            part, total = int(m.group(1)), int(m.group(2))
            placed = False
            for cs_total, cs_parts in chunk_sets:
                if cs_total == total and part not in cs_parts:
                    cs_parts[part] = rev["id"]
                    placed = True
                    break
            if not placed:
                chunk_sets.append((total, {part: rev["id"]}))

    orphaned_ids = []
    for total, parts in chunk_sets:
        # Part 1 is the main body (no chunk pattern), so a complete
        # set has total-1 continuation chunks.
        if len(parts) < total - 1:
            orphaned_ids.extend(parts.values())

    if orphaned_ids:
        _warn(f"Marking {len(orphaned_ids)} orphaned chunk reviews as duplicates: {orphaned_ids}")
        for rid in orphaned_ids:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="review-orphan-", delete=False,
            ) as tmp:
                json.dump({"body": "~~Duplicate — review reposted due to prior partial post.~~"}, tmp)
                tmp_path = tmp.name
            try:
                code, _ = review_github._gh_api(
                    f"repos/{args.repo}/pulls/{args.pr}/reviews/{rid}",
                    method="PUT", input_file=tmp_path,
                )
                if code != 0:
                    _warn(f"Failed to mark review #{rid} as duplicate (exit code {code})")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

    try:
        results = _post_chunked_review(
            args.repo, args.pr, commit_id,
            body_text, inline_comments,
            chunk_size, submit,
        )
    except LineResolutionError:
        inline_comments, inline, body_findings, body_text, results = _reclassify_and_retry(
            args, inline, body_findings,
            commit_id, chunk_size, severity_filter, submit,
        )

    if not results:
        return

    is_chunked = len(results) > 1
    review_ids = [r.get("id", 0) for r in results]

    if is_chunked:
        _info(f"All {len(results)} chunks posted (review IDs: {review_ids})")
    elif submit:
        _info(f"Review submitted: #{review_ids[0]}")
    else:
        _info(f"Review created: #{review_ids[0]} (PENDING)")
        print()
        print(f"  Submit: {_format_submit_command(args.repo, args.pr, review_ids[0])}")

    write_post_tracking(
        args.review_file, review_ids, commit_id,
        len(inline_comments), len(body_findings), len(skipped),
        submitted=submit or is_chunked,
        chunk_count=len(results),
        review_sha=head_sha,
        head_sha_at_post=head_sha,
    )


# ── Dry-run display ─────────────────────────────────────────────────────────

def _print_dry_run(
    payload: dict, inline: list[Finding],
    body_findings: list[Finding], skipped: list[Finding],
    chunk_size: int,
):
    comments = payload.get("comments", [])
    chunks = _chunk_comments(comments, chunk_size)
    if len(chunks) > 1:
        _info(f"Would post in {len(chunks)} chunks of <={chunk_size} comments")
        for i, chunk in enumerate(chunks):
            _info(f"  Chunk {i + 1}: {len(chunk)} comments")

    print()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print()
    _info(f"Inline: {', '.join(f.posted_id for f in inline) or 'none'}")
    _info(f"Body: {', '.join(f.posted_id for f in body_findings) or 'none'}")
    for f in skipped:
        _warn(f"Skipped {f.id}: {f.skip_reason} ({f.path})")


def _format_submit_command(repo: str, pr: str, review_id: int) -> str:
    return f"gh api repos/{repo}/pulls/{pr}/reviews/{review_id}/events --method POST -f event=COMMENT"
