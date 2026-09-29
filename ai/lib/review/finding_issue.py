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

What it does not do is decide *which* findings are filable. That is
`ReviewMeta.open_findings`, written by the fix pass, because a deferred finding
leaves no trace on the document to read back.
"""

# doc-group: publishing

from __future__ import annotations

from core import log
from core import markdown
from core import publishing
from core.trail import Trail
from pr import comments as pr_comments
from pr import target as pr_target
from review import issue as review_issue
from review.deferred_issue import TRACK_ALL
from review.issue import IssueResult
from review.paths import read_review_meta, write_review_meta
from review.types import OpenFinding, ReviewJob

from dataclasses import replace
from pathlib import Path

# The columns a person triaging the backlog needs. Not the review document's
# severity ordering: by the time this is read the findings are a list of
# deferred work, and where each one was matters more than how it was ranked.
_TABLE_COLUMNS = ("Finding", "Location", "Why it is still open")


def _branch_of(job: ReviewJob) -> str:
    """The branch the review covers.

    `job.pr.head` for a PR review, which is the PR's head ref. A self-review's
    metadata carries the same field, filled from the checkout.
    """
    return getattr(job.pr, "head", "") or ""


def filable(review_dir: Path) -> list[OpenFinding]:
    """The findings the last fix pass left open, in document order.

    Read from the sidecar rather than the review document: a deferral writes no
    annotation, so an unreached finding and a finding the pass never had look
    identical on disk.
    """
    return list(read_review_meta(review_dir).open_findings)


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
    log.error(f"--track named findings that are not open: {sorted(unknown)}")
    return False


def build_body(findings: list[OpenFinding], job: ReviewJob) -> str:
    """The tracking issue's body — a table of what was left, and where.

    Written and never read back, so it carries what a person needs to pick the
    work up rather than what a parser would need to recover a row.

    The heading's link is built from the repo's own forge, not github.com: a
    self-review on an enterprise host would otherwise link an issue to a PR
    number on a forge the repo is not on.
    """
    if job.pr_number:
        url = f"{pr_target.forge_base_url(job.host)}/{job.repo}/pull/{job.pr_number}"
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
        markdown.render_row(list(_TABLE_COLUMNS)),
        markdown.table_divider(len(_TABLE_COLUMNS)),
    ]
    for finding in findings:
        parts.append(markdown.render_row([
            markdown.escape_cell(finding.summary or finding.id),
            f"`{finding.location}`" if finding.location else "—",
            markdown.escape_cell(finding.reason or _default_reason(finding)),
        ]))
    parts.append("")
    return "\n".join(parts)


def _default_reason(finding: OpenFinding) -> str:
    """What to say when the pass recorded no reason of its own."""
    if finding.outcome == "needs_human":
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
        review_issue.ensure_issue_provider(job.wt_path)
        if publishing.enabled()
        else review_issue.load_issue_provider(job.wt_path)
    )
    if not provider_info.resolved:
        log.warn("no issue provider configured — not filing the open findings")
        return None

    where = f"PR #{job.pr_number}" if job.pr_number else _branch_of(job)
    result = review_issue.create_issue(
        provider_info.name,
        provider_info.options.get("team", ""),
        f"fix(review): unresolved review findings — {where}",
        build_body(selected, job),
        # Linear links the follow-up under the branch's issue; GitHub has no
        # parent concept, and gets a comment instead — see `note_on_parent`.
        parent_id=review_issue.extract_issue_id(provider_info.name, _branch_of(job)),
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
    url = pr_comments.post_issue_comment(job.repo, int(parent_id), body)
    if url is None:
        log.warn(f"could not comment on the parent issue #{parent_id}")
        return
    if trail:
        trail.info("finding_issue", f"noted the deferral on #{parent_id}",
                   data={"url": url})


def clear_filed(review_dir: Path, filed_ids: set[str]) -> None:
    """Drop the filed findings from the sidecar.

    So a second `--track-all` on the same review does not open a second issue
    for work already tracked. The findings stay on the review document, which
    is what the next round reads; this list is only the queue of what has not
    reached a tracker yet.
    """
    meta = read_review_meta(review_dir)
    remaining = tuple(f for f in meta.open_findings if f.id not in filed_ids)
    if len(remaining) != len(meta.open_findings):
        write_review_meta(review_dir, replace(meta, open_findings=remaining))


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

    provider = review_issue.load_issue_provider(job.wt_path).name
    note_on_parent(
        provider, review_issue.extract_issue_id(provider, _branch_of(job)) or "",
        result, job, trail,
    )
    clear_filed(review_dir, {f.id for f in selected})
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
    log.info(
        f"{len(unfiled)} finding(s) left open and not filed: {ids}. "
        f"Pass --track <id> to file one, or --track-all for every one."
    )
