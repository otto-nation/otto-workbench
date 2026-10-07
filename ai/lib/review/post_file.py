"""Post one review file to its PR: parse, check positions, renumber, and post as a PENDING review.

`run_post` is the flow `review-post` runs: read the review document and its
sidecar, resolve the PR's refs and diff, drop findings already posted,
classify the rest as inline or body comments, and post them in chunks — or
print the plan on a dry run. `cli.review_post` is the command over this; the
posting mechanics are `review.posting`, formatting is `review.format`.
"""

# doc-group: publishing

from __future__ import annotations

# Most of what follows is never referenced in this file. The names are re-exported
# so a test can reach them at `review_post.<name>` and patch one, which the proxy
# installed in `cli.review_post` forwards to the module that actually defines it.
# An import an editor calls unused is therefore load-bearing — deleting it
# silently turns the patch it serves into a no-op.
import git.client
import core.log
from review.dedup import dedup_against_posted
from review.document import ReviewDocument
from core.text import plural
from review.types import ReviewMeta
from review.format import (
    CLASS_FILE_LEVEL, CLASS_INLINE,
    classify_findings, format_body_text, format_inline_comment,
    renumber_for_posting, resolve_permalinks,
)
from gh.pr_data import fetch_pr_data
from gh.pr_reads import (
    _get_diff,
)
from review.posting import (
    HEAD_SHA_RE, _post_and_track, _print_dry_run,
)
from review.sections import ReviewSections


def run_post(trail, args, repo, sidecar: ReviewMeta, review_path) -> int:
    severity_filter = set(args.severity.upper().split(","))
    text = review_path.read_text()

    sha_match = HEAD_SHA_RE.search(text)
    review_sha = sha_match.group(1) if sha_match else None

    sections = ReviewSections.from_text(text)

    doc = ReviewDocument.parse(text)
    declared = doc.findings
    core.log.info(f"Parsed {len(declared)} findings from review file")
    trail.info("parse_findings", f"parsed {len(declared)} findings",
               data={"total": len(declared), "review_sha": review_sha})

    # A finding whose box the fix pass ticked is resolved, and posting it asks a
    # reviewer to look at work that is already done. Dropped outright rather than
    # stated in the body, which is where a *declined* finding goes: a decline is a
    # judgement someone may still want to read, a fix is nothing left to say.
    # `open_findings` is the document's own reading of which is which.
    all_findings = doc.open_findings
    fixed = len(declared) - len(all_findings)
    if fixed:
        core.log.info(f"Excluded {fixed} finding{plural(fixed)} the fix pass already resolved")
    trail.decision(
        "filter_fixed",
        f"kept {len(all_findings)} of {len(declared)} findings",
        reason="a checked finding is resolved and has nothing to post",
        data={"kept": len(all_findings), "excluded": fixed},
    )

    findings = [f for f in all_findings if f.severity in severity_filter]
    filtered = len(all_findings) - len(findings)
    if filtered:
        core.log.info(f"Filtered to {len(findings)} findings (excluded {filtered} by severity)")
    trail.decision(
        "filter_severity",
        f"kept {len(findings)} of {len(all_findings)} findings",
        reason=f"severity filter: {args.severity}",
        data={"kept": len(findings), "excluded": filtered, "filter": args.severity},
    )

    if not findings:
        core.log.warn("No findings to post")
        return 0

    head_sha = sidecar.head_sha
    head_ref = sidecar.head_ref
    base_ref = sidecar.base_ref
    pr_data = None
    if not args.dry_run:
        pr_data = fetch_pr_data(repo, args.pr)
        head_sha = pr_data.head_sha
        head_ref = head_ref or pr_data.head_ref
        base_ref = base_ref or pr_data.base_ref
    else:
        head_sha = head_sha or review_sha or "dry-run"

    sha_drifted = bool(review_sha and review_sha != head_sha)
    if sha_drifted:
        core.log.warn(
            f"Review was written against {git.client.abbrev(review_sha)}, "
            f"PR HEAD is now {git.client.abbrev(head_sha)}")
        core.log.info("Re-verifying positions against current diff")
        trail.info(
            "sha_drift",
            f"review SHA {git.client.abbrev(review_sha) or '?'} "
            f"!= HEAD {git.client.abbrev(head_sha) or '?'}",
            data={"review_sha": review_sha, "head_sha": head_sha},
        )

    if args.dry_run:
        core.log.info("Fetching diff for classification...")
    diff_text = _get_diff(repo, args.pr)
    # `classify_findings` calls its third list "skipped", but nothing in it is
    # left unposted: a path outside the diff and a declined finding both still
    # go in the body, they just cannot anchor to a line. Named for where they
    # land so the count below cannot be read as findings that never reached
    # the PR.
    inline, file_level, body_only = classify_findings(findings, diff_text)
    core.log.info(
        f"Classified: {len(inline)} inline, {len(file_level)} file-level, "
        f"{len(body_only)} body-only (path not in diff, or declined)")
    trail.info("classify_findings",
               f"{len(inline)} inline, {len(file_level)} file-level, {len(body_only)} body-only",
               data={"inline": len(inline), "file_level": len(file_level),
                     "body_only": len(body_only)})

    # A duplicate is the one finding that is genuinely not posted. It is kept
    # apart from `body_only` on purpose: the body renders every finding it is
    # handed, so a duplicate that joined that list would be dropped from the
    # inline comments and then posted again in the summary.
    duplicates = []
    all_postable = inline + file_level
    if not args.dry_run and all_postable:
        kept, duplicates = dedup_against_posted(all_postable, repo, args.pr, pr_data)
        if duplicates:
            core.log.info(f"Skipped {len(duplicates)} findings duplicating existing comments")
            inline = [f for f in kept if f.classification == CLASS_INLINE]
            file_level = [f for f in kept if f.classification == CLASS_FILE_LEVEL]
        trail.info("dedup", f"removed {len(duplicates)} duplicate findings",
                   data={"deduped": len(duplicates), "kept": len(kept)})

    body_findings = file_level + body_only

    findings_for_links = inline + body_findings
    resolve_permalinks(findings_for_links, repo, diff_text, head_ref, base_ref,
                       sidecar.host)

    inline, body_findings = renumber_for_posting(inline, body_findings)

    inline_comments = [format_inline_comment(f) for f in inline]
    body_text = format_body_text(
        body_findings, len(inline) > 0, severity_filter,
        sections=sections,
    )

    commit_id = head_sha
    chunk_size = args.chunk_size

    if args.dry_run:
        trail.decision("post_mode", "dry-run mode", reason="--dry-run flag set")
        payload = {
            "commit_id": commit_id,
            "body": body_text,
        }
        if inline_comments:
            payload["comments"] = inline_comments
        _print_dry_run(payload, inline, body_findings, body_only, chunk_size)
        return 0

    trail.decision("post_mode", "posting to GitHub",
                   reason="not dry-run",
                   data={"inline_count": len(inline_comments), "has_body": bool(body_text)})
    _post_and_track(
        args, inline_comments, body_text,
        inline, body_findings, duplicates,
        commit_id, head_sha, chunk_size, severity_filter, pr_data,
        review_sha=review_sha or head_sha, sha_drifted=sha_drifted,
        sections=sections,
    )
    return 0
