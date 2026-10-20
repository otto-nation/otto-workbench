"""Filing a tracker issue for the findings a review left unresolved.

The threads half of this has existed for a while: `pr comments --finish --track`
files one tracking issue for the review comments a fix pass deferred. Findings
had no path to a tracker at all — the outcome was annotated on the review
markdown, which lives under `~/.local/state/workbench/reviews/` on one machine,
and named in a commit message, which is not a tracker. Both are gone the moment
the branch merges.

Selection is the threads convention rather than a second one: nothing is filed
unless `--track` names it or `--track-all` is passed, and nothing is published
unless the publishing gate is open. Filing posts under the operator's name and
a deferral is a per-finding judgement, so neither is a sensible default.

What is filable now comes from the record's dispositions, not
`ReviewMeta.open_findings`: a skip or an unreached finding the last fix pass
left open, that nobody has filed yet.
"""

# doc-group: publishing

from __future__ import annotations

import core.log
import core.markdown
import core.publishing
from core.trail import Trail
import pr.comments
import pr.target
import review.issue
from review.deferred_issue import TRACK_ALL
from review.issue import IssueResult
from review.paths import FILENAME_REVIEW
from review.record import Disposition, RecordFinding, load_record
from review.store import update_review
from review.types import ReviewJob

from pathlib import Path

# The columns a person triaging the backlog needs. Not the review document's
# severity ordering: by the time this is read the findings are a list of
# deferred work, and where each one was matters more than how it was ranked.
_TABLE_COLUMNS = ("Finding", "Location", "Why it is still open")
_LEFT_OPEN = (Disposition.SKIPPED, Disposition.UNREACHED)


def _branch_of(job: ReviewJob) -> str:
    """The branch the review covers.

    `job.pr.head` for a PR review, which is the PR's head ref. A self-review's
    metadata carries the same field, filled from the checkout.
    """
    return getattr(job.pr, "head", "") or ""


def filable(review_dir: Path) -> list[RecordFinding]:
    """The findings the last fix pass left open and nobody has filed, in document order."""
    record = load_record(review_dir)
    if record is None:
        return []
    return [f for f in record.visible if f.disposition in _LEFT_OPEN and not f.filed]


def validate_track(review_dir: Path, track) -> bool:
    """False once it has reported a `--track` id naming no open finding.

    Mirrors `deferred_issue.validate_track`: an id that names nothing is a
    typo, and filing the rest silently would leave the operator believing they
    had tracked something they had not.
    """
    if track is TRACK_ALL:
        return True
    unknown = set(track) - {f.id for f in filable(review_dir)}
    if not unknown:
        return True
    core.log.error(f"--track named findings that are not open: {sorted(unknown)}")
    return False


def build_body(findings: list[RecordFinding], job: ReviewJob) -> str:
    """The tracking issue's body — a table of what was left, and where.

    Written and never read back, so it carries what a person needs to pick the
    work up rather than what a parser would need to recover a row.

    The heading's link is built from the repo's own forge, not github.com: a
    self-review on an enterprise host would otherwise link an issue to a PR
    number on a forge the repo is not on.
    """
    if job.pr_number:
        url = f"{pr.target.forge_base_url(job.host)}/{job.repo}/pull/{job.pr_number}"
        where = f"[PR #{job.pr_number}]({url})"
    else:
        # A self-review need not have a PR, and often does not: the findings
        # are filed from the branch before one is opened.
        where = f"branch `{_branch_of(job)}`"
    parts = [
        f"## Unresolved review findings — {where}",
        "",
        "A review of this branch left these findings unresolved. They are "
        "recorded here because the review file is local state and the branch "
        "is about to merge.",
        "",
        core.markdown.render_row(list(_TABLE_COLUMNS)),
        core.markdown.table_divider(len(_TABLE_COLUMNS)),
    ]
    for finding in findings:
        location = _location_of(finding)
        parts.append(core.markdown.render_row([
            core.markdown.escape_cell(_summary_of(finding) or finding.id),
            f"`{location}`" if location else "—",
            core.markdown.escape_cell(finding.reason or _default_reason(finding)),
        ]))
    parts.append("")
    return "\n".join(parts)


def _summary_of(f: RecordFinding) -> str:
    return f.body.split("\n", 1)[0] if f.body else f.path


def _location_of(f: RecordFinding) -> str:
    return f"{f.path}:{f.line}" if f.path and f.line else f.path


def _default_reason(f: RecordFinding) -> str:
    """What to say when the pass recorded no reason of its own."""
    if f.disposition is Disposition.SKIPPED:
        return "needs a person"
    return "not reached by the fix pass"


def file_findings(
    review_dir: Path,
    job: ReviewJob,
    track,
    trail: Trail | None = None,
) -> IssueResult | None:
    """File one tracking issue for the selected findings. None when none were.

    One issue listing the selection rather than one per finding, matching the
    threads path. A finding's id is a position in one rendering — the next
    review renumbers — so per-finding issues would need a content hash to
    dedupe against on the following round, and the failure mode this repo
    actually has is volume rather than granularity.
    """
    if not validate_track(review_dir, track):
        return None
    selected = [f for f in filable(review_dir) if f.id in track]
    if not selected:
        return None

    provider_info = (
        review.issue.ensure_issue_provider(job.wt_path)
        if core.publishing.enabled()
        else review.issue.load_issue_provider(job.wt_path)
    )
    if not provider_info.resolved:
        core.log.warn("no issue provider configured — not filing the open findings")
        return None

    where = f"PR #{job.pr_number}" if job.pr_number else _branch_of(job)
    result = review.issue.create_issue(
        provider_info.name,
        provider_info.options.get("team", ""),
        f"fix(review): unresolved review findings — {where}",
        build_body(selected, job),
        # Linear links the follow-up under the branch's issue; GitHub has no
        # parent concept, and gets a comment instead — see `note_on_parent`.
        parent_id=review.issue.extract_issue_id(provider_info.name, _branch_of(job)),
        repo=job.repo,
        opts=provider_info.options,
    )
    if trail and result.filed:
        trail.info("finding_issue", f"filed {result.issue.id}",
                   data={"findings": len(selected), "url": result.issue.url})
    return result


def note_on_parent(
    provider: str,
    parent_id: str,
    result: IssueResult,
    job: ReviewJob,
    trail: Trail | None = None,
) -> None:
    """Tell the parent issue that some of its work was deferred, and where.

    A parent whose PR merged with findings unaddressed otherwise reads as
    complete, and the follow-up exists only in a tracker issue nothing links to
    from there.

    GitHub only. On Linear the sub-issue relation `create_issue` already made
    with `--parent` *is* the write-back — the follow-up shows under the parent
    without a comment, and adding one would say twice what the relation says
    once. GitHub has no parent concept at all (`_create_github` takes no
    `parent_id`), so a comment is the whole of what can be linked there.

    Best-effort: the issue is already filed by the time this runs, so a failed
    comment is reported and not raised past a filing that succeeded.
    """
    if provider != "github" or not parent_id or not result.filed:
        return
    if not parent_id.isdigit():
        # `extract_issue_id` returns a GitHub issue *number*; anything else came
        # from a branch name shaped like a tracker key and names nothing here.
        return
    body = (
        f"A review of `{_branch_of(job)}` left findings unresolved. "
        f"They are tracked in {result.issue.url or '#' + result.issue.id}."
    )
    url = pr.comments.post_issue_comment(job.repo, int(parent_id), body)
    if url is None:
        core.log.warn(f"could not comment on the parent issue #{parent_id}")
        return
    if trail:
        trail.info("finding_issue", f"noted the deferral on #{parent_id}",
                   data={"url": url})


def clear_filed(review_dir: Path, filed_ids: set[str], issue_id: str) -> None:
    """Mark the filed findings with their issue, so a second `--track-all` files nothing twice."""
    def mark(record):
        for fid in filed_ids:
            try:
                record = record.with_finding(fid, filed=issue_id)
            except KeyError:
                # The issue is already filed; a stale id must not crash after it.
                core.log.warn(f"finding {fid} is gone from the record — not marked filed")
        return record
    update_review(review_dir / FILENAME_REVIEW, mark)


def file_and_link(
    review_dir: Path,
    job: ReviewJob,
    track,
    trail: Trail | None = None,
) -> bool:
    """File the selected findings, note them on the parent, and clear the queue.

    The whole of what a caller wants, so a CLI does not have to know the order
    these run in. False only when a `--track` id named nothing, which is the
    one case worth an exit status: the operator asked to file something that
    does not exist, and filing the rest would hide the typo.
    """
    if not validate_track(review_dir, track):
        return False
    selected = [f for f in filable(review_dir) if f.id in track]
    if not selected:
        report_unfiled(review_dir, track)
        return True

    result = file_findings(review_dir, job, track, trail)
    if result is None or not result.filed:
        # Drafted, or undelivered. The queue keeps the findings so a later run
        # with the gate open can still file them.
        return True

    provider = review.issue.load_issue_provider(job.wt_path).name
    note_on_parent(
        provider, review.issue.extract_issue_id(provider, _branch_of(job)) or "",
        result, job, trail,
    )
    clear_filed(review_dir, {f.id for f in selected}, result.issue.id)
    report_unfiled(review_dir, track)
    return True


def report_unfiled(review_dir: Path, track) -> None:
    """Name the open findings nobody asked to file, so the omission is visible.

    Filing nothing is the correct default, but a silent nothing reads as "there
    was nothing to file".
    """
    unfiled = [f for f in filable(review_dir) if f.id not in track]
    if not unfiled:
        return
    ids = ", ".join(f.id for f in unfiled)
    core.log.info(
        f"{len(unfiled)} finding(s) left open and not filed: {ids}. "
        f"Pass --track <id> to file one, or --track-all for every one."
    )
