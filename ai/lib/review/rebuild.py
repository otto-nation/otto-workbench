"""Rebuild a review record from its group finding files.

Loads the `group-N.json` artifacts, merges their records, post-processes them
and writes a new review through the store. It is the recovery path when
synthesis drifted or the review file was corrupted; `cli.review_rebuild` is
the command over it. A group file that no longer loads is skipped with a
warning, because rebuilding exists to salvage. When none of them load, the
existing review is left untouched. A directory holding only the `group-N.md`
files of a run from before agents wrote JSON is refused by name.
"""

# doc-group: findings

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import core.errors
import core.log
import core.proc
from core.trail import Trail
from agent.registry import PHASES
from core.phases import Phase
from review.artifacts import GroupArtifact, declinable_sids
from review.focus import uncovered_areas
from review.ingest import IngestRejected, load_artifact
from review.merge import merge_records
from review.paths import read_review_meta
from review.record import ReviewRecord
from review.store import read_prior, read_review, write_review
from review.verdict import build_mechanical_record, states_verdict
from review.verify import post_process_record

REBUILD_SUMMARY = "Rebuilt mechanically from group finding files."

# ceiling: names where a run from before agents wrote JSON left its group
# files, only to tell the operator why such a directory cannot be rebuilt.
# Upgrade trigger: delete this and its branch in `rebuild` once SCHEMA_VERSION
# moves past 2, when a directory that old is stale on every other path too.
_MARKDOWN_GROUP_GLOB = str(Path(PHASES[Phase.GROUP].output_filename.format("*")).with_suffix(".md"))


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


def _group_records(group_files: list[str], declinable: frozenset[str]) -> list[ReviewRecord]:
    """The record of every group file that loads; the rest are skipped and named.

    A carried decline is believed only for a sid in `declinable` (D19).
    """
    records = []
    for path in group_files:
        # A failed group's pre-created file is empty (or whitespace); it is
        # not a malformed artifact.
        if not Path(path).read_text().strip():
            continue
        try:
            records.append(load_artifact(path, GroupArtifact).to_record(declinable=declinable))
        except IngestRejected as exc:
            core.log.warn(f"{path} skipped — {exc}")
    return records


def rebuild(review_dir: Path, pr: str, *, script: str, debug: bool = False) -> int:
    """Rebuild *review_dir*'s review from its group finding files.

    Returns 1 when there are no group files, none of them load, or the sidecar
    names no repo, and `core.proc.INTERRUPT_RETURNCODE` on an interrupt.
    *script* is the name the trail records — the binary the operator ran.
    """
    review_file = review_dir / "review.md"

    group_files = discover_group_files(review_dir)
    if not group_files:
        if any(review_dir.glob(_MARKDOWN_GROUP_GLOB)):
            core.log.error(f"Group files in {review_dir} predate JSON artifacts — re-run the review")
        else:
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

        # --repair exists to salvage: a group file that no longer loads is skipped.
        records = _group_records(group_files, declinable_sids(read_prior(review_dir)))
        # Overwriting the review when none of them loaded would destroy the last
        # good record.
        if not records:
            core.log.error(
                f"none of {len(group_files)} group files in {review_dir} "
                f"could be read — nothing rebuilt"
            )
            return 1
        merged = merge_records(records)
        trail.info("merge_records", "merged group outputs", data={"group_count": len(group_files)})
        done = post_process_record(merged)
        existing = read_review(review_file)
        record = build_mechanical_record(
            done.record, group_count=len(group_files), summary_note=REBUILD_SUMMARY,
            include_verdict=states_verdict(meta.mode), file_count=meta.changed_files,
        )
        record = replace(
            record,
            focus=tuple(uncovered_areas(existing.record.focus if existing else ())),
        )
        # Dated today and carrying no status: a rebuild cannot attest to how the
        # pipeline run ended — this is not one. write_review also writes
        # meta.json (today's rebuild never did) because the header is rendered
        # from it.
        write_review(review_file, record, replace(meta, date=date.today().isoformat(), status=None))
        core.log.info(f"Rebuilt {review_file}")
        trail.info("post_process", f"processed {len(record.visible)} findings",
                   data={"finding_count": len(record.visible), "review_file": str(review_file)})
        return 0
    except KeyboardInterrupt:
        return core.proc.INTERRUPT_RETURNCODE
    except Exception as exc:
        core.errors.reraise(trail, exc)
    finally:
        trail.finish()
