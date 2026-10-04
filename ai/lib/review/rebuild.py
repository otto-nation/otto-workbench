"""Rebuild a review document from its group finding files.

Merges the group-N.md outputs, post-processes the findings and writes a new
review.md — the recovery path when synthesis drifted or the review file was
corrupted. `cli.review_rebuild` is the command over this.
"""

# doc-group: findings

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import core.log
import core.proc
from core.trail import Trail
from agent.registry import PHASES
from core.phases import Phase
from review.document import ReviewDocument, ReviewHeader, review_title
from review.paths import read_review_meta
from review.verdict import build_mechanical_body, states_verdict
from review.verify import post_process_findings
from review.merge import merge_reviews

REBUILD_SUMMARY = "Rebuilt mechanically from group finding files."


def discover_group_files(review_dir: Path) -> list[str]:
    """The group finding files in *review_dir*, numbered from 1 up to the first gap."""
    groups = []
    i = 1
    while True:
        path = review_dir / PHASES[Phase.GROUP].output_filename.format(i)
        if not path.exists():
            break
        groups.append(str(path))
        i += 1
    return groups


def rebuild(review_dir: Path, pr: str, *, script: str, debug: bool = False) -> int:
    """Rebuild *review_dir*/review.md from its group finding files.

    Returns 1 when there are no group files or the sidecar names no repo, and
    `core.proc.INTERRUPT_RETURNCODE` on an interrupt. *script* is the name the
    trail records — the binary the operator ran.
    """
    review_file = review_dir / "review.md"

    group_files = discover_group_files(review_dir)
    if not group_files:
        core.log.error(f"No group finding files in {review_dir}")
        return 1

    core.log.info(f"Found {len(group_files)} group files")

    meta = read_review_meta(review_dir)
    if not meta.repo:
        core.log.error("Cannot determine repository — meta.json missing or has no 'repo' field")
        return 1
    repo = meta.repo
    # A sidecar written before the field existed numbers no PR, so the operator's
    # `--pr` stands in — it is the same number, and the title has to state one.
    if meta.pr_number is None and pr.isdigit():
        meta = replace(meta, pr_number=int(pr))

    trail = Trail.start(
        script=script,
        context={"repo": repo, "pr": pr},
        debug=debug,
    )

    try:
        trail.info("discover_groups", f"found {len(group_files)} group files",
                   data={"count": len(group_files), "files": group_files})

        merged_content = merge_reviews(group_files)
        trail.info("merge_reviews", "merged group outputs",
                   data={"group_count": len(group_files)})

        review_file.write_text(merged_content)
        verification = post_process_findings(str(review_file))
        if verification:
            v = verification
            detail = f"checked={v['findings_checked']} passed={v['findings_passed']} dropped={v['findings_dropped']}"
            trail.info("evidence_verification", detail, data=v)
        processed = review_file.read_text()

        # Dated today and carrying no status: a rebuild reconstructs the
        # document from the group files, so the one thing it cannot attest to
        # is how the pipeline run ended — this is not one.
        document = ReviewDocument(
            title=review_title(meta),
            header=ReviewHeader.from_meta(meta, date=date.today().isoformat()),
            body=build_mechanical_body(
                processed,
                group_count=len(group_files),
                summary_note=REBUILD_SUMMARY,
                include_verdict=states_verdict(meta.mode),
                file_count=meta.changed_files,
            ),
        )

        final = document.render()
        review_file.write_text(final)
        core.log.info(f"Rebuilt {review_file}")

        finding_lines = [line for line in final.splitlines() if line.startswith("- [")]
        trail.info("post_process", f"processed {len(finding_lines)} findings",
                   data={"finding_count": len(finding_lines), "review_file": str(review_file)})
        return 0
    except KeyboardInterrupt:
        return core.proc.INTERRUPT_RETURNCODE
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
