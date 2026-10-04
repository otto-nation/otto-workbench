"""A PR's comment threads: read them, reconcile them with the state file, and triage, fix, or settle them.

`run_threads` is the flow `pr comments` runs over a resolved target: fetch the
PR's threads and conversation, carry the state file's records forward, mark
what has been seen, and hand off to triage, the fix pass, or settlement as the
arguments ask. `cli.review_threads` is the command over this — the parser, the
run lock, the trail. Thread state is `pr.comments_state`; triage is
`pr.triage`; the fix pass is `pr.comments_fix`. This is also where the
fix-engine and GitHub-read dependency moved: `fix.comments` and
`gh.pr_data` used to be pulled in by `cli.review_threads` directly, and now
live here instead.
"""

# doc-group: publishing

from __future__ import annotations

import dataclasses
import json
import sys

import core.log
import core.publishing
import core.run_lock
import fix.comments
from gh.pr_data import fetch_pr_data
import pr.comments
import pr.comments_fix
import pr.comments_state
import pr.context
import pr.domains
import pr.settlement
import pr.state
import pr.thread_replies
import pr.triage
import pr.triage_round
from pr.comments_state import ThreadRecord, ThreadState
from pr.thread_models import PRReport, ReportThread
import review.closeout
from review.deferred_issue import TRACK_ALL


def resolve_verified_threads(threads_raw: list, threads: dict[str, ThreadRecord]) -> int:
    """Resolve all verified threads on GitHub. Returns count resolved."""
    resolved = 0
    for thread_data in threads_raw:
        tid = thread_data["id"]
        record = threads.get(tid)
        if record is None or record.state != ThreadState.VERIFIED:
            continue
        if not pr.comments.resolve_thread(tid):
            continue
        threads[tid] = dataclasses.replace(record, state=ThreadState.RESOLVED)
        resolved += 1
    return resolved



def edit_stamp(comment: dict) -> str:
    """When this comment's body was last rewritten, or "" if never.

    One spelling for the two helpers below, so the key they record and the key
    they compare against cannot come to disagree about which field dates a
    comment.
    """
    return comment.get("last_edited_at", "") or ""


def mark_seen(comments: list[dict], prior: dict[int, str]) -> None:
    """Flag each comment the last round already read, in place.

    Seen means *this* text was read, not that this comment id was: an edit
    keeps the id, so matching on id alone treats a rewritten comment as handled
    and `triage.collect_unseen_comments` drops it before an agent ever sees it.
    A reviewer who edits a comment to add a demand is asking for something, and
    that is the case an id-only record loses.

    A comment absent from `prior` is unseen, which is also how a state file
    written before the stamps existed reads. That direction is deliberate: a
    false unseen re-reports something once, a false seen drops it for good.
    """
    for c in comments:
        cid = c.get("id")
        c["seen"] = cid is not None and cid in prior and prior[cid] == edit_stamp(c)


def seen_record(comments: list[dict]) -> dict[int, str]:
    """What this round read, for the next round to compare against.

    A comment with no id is left out rather than recorded under one. Both
    builders take the id from `databaseId`, which GraphQL can omit, and the key
    type here is `int`: a `None` key serialises to the JSON string `"null"`,
    which `serde` then refuses to coerce back on load, so `load_state`
    discards the whole file as unreadable. Under the former `list[int]` shape
    the bad entry cost one dropped field; under this one it would cost every
    verdict, round and outcome the PR had accumulated.
    """
    return {c["id"]: edit_stamp(c) for c in comments if c.get("id") is not None}


def run_threads(trail, args, ctx) -> int:
    repo = ctx.repo
    pr_number = ctx.pr_number
    branch = ctx.branch
    toplevel = ctx.require_worktree()
    head_sha = ctx.head_sha

    owner, repo_name = repo.split("/", 1)

    pr_data = fetch_pr_data(repo, str(pr_number))
    my_login = pr_data.viewer_login

    state_path = pr.comments.threads_state_path(ctx.target_dir)

    # Load prior state
    prior_state = pr.comments_state.load_state(state_path)
    prior_threads = prior_state.threads if prior_state else {}

    # Fetch from GitHub (served from pr_data, no extra API calls)
    fetched = pr.comments.fetch_threads(owner, repo_name, pr_number, pr_data)
    threads_raw = fetched.threads
    verdicts = pr.comments.fetch_reviewer_verdicts(repo, pr_number, pr_data)
    issue_comments = pr.comments.fetch_issue_comments(repo, pr_number, my_login, pr_data)
    review_body_comments = pr.comments.fetch_review_body_comments(
        repo, pr_number, my_login, pr_data,
    )
    trail.info("fetch_threads", f"fetched {len(threads_raw)} threads",
               data={"count": len(threads_raw), "complete": fetched.complete})
    if not fetched.complete:
        core.log.warn(
            f"Only {len(threads_raw)} review threads could be read — the rest are "
            "unreachable right now, so their triage verdicts are being kept rather "
            "than treated as resolved")

    # Sync
    threads = pr.comments.sync_threads(fetched, prior_threads, my_login)
    synced_resolved = sum(1 for t in threads.values() if t.state == ThreadState.RESOLVED)
    synced_open = len(threads) - synced_resolved
    trail.info("sync_threads", f"{synced_resolved} resolved, {synced_open} open",
               data={"resolved": synced_resolved, "open": synced_open, "total": len(threads)})

    # Resolve verified threads if requested (or as part of triage). A --finish
    # that will refuse below because the fetch is short must refuse before
    # this runs too — otherwise the "nothing was published" message it prints
    # is false: GitHub already got the resolutions this loop sent.
    if args.triage or (args.finish and fetched.complete):
        resolved_count = resolve_verified_threads(threads_raw, threads)
        if resolved_count:
            core.log.info(f"Resolved {resolved_count} verified threads")

    # Save state
    pr.comments_state.save_state(state_path, pr.comments_state.CommentsState(
        repo=repo, pr_number=pr_number, my_login=my_login, threads=threads,
    ))

    # Dashboard to stderr
    dashboard = pr.comments.render_dashboard(
        pr_number, threads, verdicts, issue_comments,
        review_body_comments=review_body_comments,
    )
    print(dashboard, file=sys.stderr)

    # Build report
    report_threads = []
    for thread_data in threads_raw:
        tid = thread_data["id"]
        t = threads.get(tid) or ThreadRecord()
        report_threads.append(ReportThread.from_node(
            thread_data, my_login,
            state=t.state,
            classification=t.classification,
            reviewer=t.reviewer,
        ))
    report = PRReport(
        repo=repo,
        pr_number=pr_number,
        my_login=my_login,
        threads=report_threads,
        issue_comments=issue_comments,
        review_body_comments=review_body_comments,
        verdicts=verdicts,
    )

    # Update unified PR state (best-effort)
    ctx_args = dict(
        target_dir=ctx.target_dir, worktree_root=str(toplevel), repo=repo, branch=branch,
        pr_number=pr_number, head_sha=head_sha,
    )
    try:
        thread_states: dict[str, int] = {}
        for t in report.threads:
            thread_states[t.state] = thread_states.get(t.state, 0) + 1
        blocking = [
            v.get("user", "unknown")
            for v in report.verdicts
            if v.get("state") == "CHANGES_REQUESTED"
        ]
        has_approvals = any(
            v.get("state") == "APPROVED" for v in report.verdicts
        )
        st = pr.state.load_or_init(**ctx_args)
        mark_seen(issue_comments, st.comments.seen_issue_comments)
        mark_seen(review_body_comments, st.comments.seen_review_body_comments)
        pr.state.apply(st, pr.domains.CommentsSummary(
            total_threads=len(report.threads),
            by_state=thread_states,
            blocking_reviewers=blocking,
            has_approvals=has_approvals,
            seen_issue_comments=seen_record(issue_comments),
            seen_review_body_comments=seen_record(review_body_comments),
            complete=fetched.complete,
            updated_at=pr.state.now_iso(),
        ))

        pr.state.save_state(ctx.target_dir, st)
    except Exception as exc:
        trail.error("state_update", f"state update failed: {exc}")
        core.log.error(f"state update failed: {exc}")

    # Triage and/or fix mode
    if args.triage:
        triage_mode = "fix" if args.fix else "triage"
        trail.decision("triage_mode", f"running {triage_mode}",
                       reason="--fix flag" if args.fix else "--triage flag",
                       data={"fix": args.fix, "triage": args.triage})
        triage_result, rc = pr.triage.run_triage(report, toplevel, ctx_args, trail=trail)
        if rc != 0:
            return rc

        if args.fix and triage_result:
            fixable_count = len(
                pr.triage_round.classify_entries(triage_result.threads).fixable)
            fixable_items_count = len(
                pr.triage_round.classify_entries(triage_result.comment_items).fixable)
            trail.decision(
                "fix_classification",
                f"{fixable_count} fixable threads, {fixable_items_count} fixable items identified",
                reason="the entries triage_round.classify_entries routes to the fix agent",
                data={"fixable": fixable_count, "fixable_items": fixable_items_count,
                      "total": len(triage_result.threads),
                      "total_items": len(triage_result.comment_items)},
            )
            fix_pass = fix.comments.run_pass(
                triage_result, report, toplevel, ctx, trail=trail,
                verify=not args.no_verify,
            )
            trail.info("fix_result", "fix pass complete",
                       data={"fixable": fixable_count,
                             "deferred": len(fix_pass.deferred),
                             "dismissed": len(fix_pass.dismissed),
                             "already_addressed": len(fix_pass.already_addressed),
                             "batches": fix_pass.batches,
                             "max_turns": fix_pass.max_turns,
                             "max_budget": fix_pass.max_budget,
                             "commit_sha": fix_pass.commit_sha,
                             "commit_status": fix_pass.commit_status})
            output = dataclasses.asdict(triage_result)
            output["fix_pass"] = dataclasses.asdict(fix_pass)
            output["issue_comments"] = report.issue_comments
            output["review_body_comments"] = report.review_body_comments
        else:
            output = dataclasses.asdict(triage_result) if triage_result else {}
            output["issue_comments"] = report.issue_comments
            output["review_body_comments"] = report.review_body_comments
    else:
        output = dataclasses.asdict(report)

    # After the fix pass, not before: a combined --fix --finish run would
    # otherwise close out the previous run's deferred set and leave this one's
    # untouched. Outside the state-update try/except, because a caller running
    # this to close the loop needs a failure to be an error, not a log line.
    if args.finish and not fetched.complete:
        # Closeout publishes a summary and replies asserting the round is
        # answered. Asserting that over a thread set we could not finish
        # reading is the same error as writing the ledger from it: the threads
        # we never saw are the ones most likely to be unanswered.
        core.log.error(
            "--finish needs the whole thread set and this fetch was short — "
            "nothing was published. Re-run once the threads are readable again "
            "(an exhausted GitHub API budget is the usual cause, and it refills "
            "on its own)")
        return 1

    if args.finish:
        track = TRACK_ALL if args.track_all else frozenset(args.track)
        if not review.closeout.finish_deferred_work(ctx, report, trail=trail, track=track):
            return 1

    json.dump(output, sys.stdout, indent=2)
    print()
    return 0
