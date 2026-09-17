"""The two review flows, and the tail they share.

A PR review and a self review are not one flow with a mode switch. They differ
in *order*, not only in values: the self flow refuses a superseded review as its
first statement, before it has paid for anything, while the PR flow refuses only
once it holds a worktree, so that a refusal still cleans up what it read. The
self flow releases its recover pin the moment the pipeline is done with it; the
PR flow holds both worktrees to the end because the pin's own failure path can
exit. Four more differences of that kind are listed against
`tests/review_flow_characterisation_test.py`, which exists to keep them honest.

A parameter cannot express where a statement runs. Trying to make one do it
produces a single flow whose reader must hold a dozen booleans to know what
happens next, and whose ordering tests all pass because every ordering is
reachable. So: two flows, stated plainly, over one shared tail —
`finish_review` — which is the part that genuinely does not differ.

Both return a `ReviewOutcome`. The caller needs the review file back: a self
review's lives under a branch-derived directory that `review_file_path` cannot
produce, so a CLI that recomputed it would emit an all-zero summary for a review
that ran.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core import log
from core import prompt
from core.phases import Phase
from core.trail import Trail
from gh import client as gh_client
from git import client as git_client
from pr import context as pr_context
from review import invoke as review_invoke
from review import issue as review_issue
from review import preflight as review_preflight
from review import publish as review_publish
from review import recover as review_recover
from review import worktree as review_worktree
from review.completion import ReviewOutcome, finish_review, resolve_prior_review
from review.paths import FILENAME_PIPELINE_STATE, FILENAME_SESSION

GITHUB_BASE_URL = "https://github.com"


@dataclass(frozen=True)
class ReviewFlags:
    """What the operator asked for, as a value rather than an argv namespace.

    The flows read these and nothing else off the command line, which is what
    lets them be called from a test without building a parser. `bin_dir` and
    `generator_version` are resolved by the entry point: both live under
    ``ai/bin``, which a module here must not reach.
    """

    bin_dir: Path
    generator_version: str
    issue_link: str = ""
    max_parallel: int = 1
    max_cost: float | None = None
    model: str | None = None
    effort: str | None = None
    max_groups: int | None = None
    skip_phases: frozenset[Phase] = frozenset()
    disprove: bool | None = None
    generated: bool = False
    recover: bool = False
    force: bool = False
    fix: bool = False
    may_publish: bool = False
    no_post: bool = False
    auto_post: bool = False
    auto_submit: bool = False
    repo_dir: str = ""


def run_pr_review(
    ctx: pr_context.ResolvedContext,
    flags: ReviewFlags,
    review_file: Path,
    *,
    trail: Trail,
) -> ReviewOutcome:
    """Review the PR *ctx* names, in a worktree checked out for the purpose."""
    pr_number = str(ctx.pr_number)
    repo = ctx.repo
    review_dir = review_file.parent

    trail.info("resolve_context", f"resolved PR #{pr_number} in {repo}",
               data={"pr": pr_number, "repo": repo, "branch": ctx.branch})

    # Resolved before the issue lookup below, not with the worktree setup it
    # feeds further down: the repo's own .workbench.yml is what says which
    # tracker to read, and looking it up without one resolves to nothing.
    repo_root = review_worktree.find_repo_root(repo, flags.repo_dir)
    if not repo_root:
        trail.error("setup_worktree", f"cannot find local clone of {repo}")
        log.error(f"Cannot find local clone of {repo}. Clone it first and re-run from within the repo.")
        raise SystemExit(1)

    pr_meta = gh_client.pr_view(pr_number, "headRefName", "body", repo=repo)
    pr_head = pr_meta.get("headRefName", "")
    pr_body = pr_meta.get("body", "")

    # Detected here because the lookup needs `repo_root` above and `pr_head`
    # from the metadata just fetched. Asking the operator for one is a separate
    # step, deferred until after the gates below — see the prompt further down.
    issue_link = flags.issue_link
    issue_context = ""
    if not issue_link:
        provider_info = review_issue.load_issue_provider(repo_root)
        issue_id = review_issue.extract_issue_id(provider_info.name, pr_head, pr_body)
        issue_result = review_issue.fetch_issue_context(
            provider_info.name, issue_id, repo, provider_info.options,
        )
        issue_context = issue_result.context
        if issue_result.link:
            issue_link = issue_result.link

    pr_url = f"{GITHUB_BASE_URL}/{repo}/pull/{pr_number}"

    # --force absorbs the unattended flags for the prompts below, and only for
    # those: supersession reads the raw flag — see preflight.supersession_override.
    force_prompts = flags.force or flags.no_post or flags.auto_post

    if flags.recover:
        recover_sha = review_recover.resolve_recover_sha(
            review_dir, review_recover.get_pr_head_sha(repo, pr_number))
        has_pipeline_state = True
        force_prompts = True
    else:
        recover_sha = ""
        has_pipeline_state = (review_dir / FILENAME_PIPELINE_STATE).is_file()

    if has_pipeline_state and review_file.exists():
        review_recover.should_auto_recover(repo, pr_number, review_file)

    if not has_pipeline_state:
        review_preflight.check_stale_review(repo, pr_number, review_file, force_prompts)
    trail.decision(
        "review_freshness",
        "resume" if has_pipeline_state else "fresh review",
        reason="pipeline state found" if has_pipeline_state else "no pipeline state",
        data={"has_pipeline_state": has_pipeline_state},
    )

    review_preflight.check_pending_review(repo, pr_number, force_prompts)

    # Last, because it is the expensive step: the three checks above can all end
    # the run, and each of them is a `gh` call against an unshallowed clone and
    # a checkout.
    trail.info("setup_worktree", "setting up PR worktree",
               data={"repo_root": repo_root, "pr_head": pr_head})
    wt_result = review_worktree.setup_pr_worktree(repo, pr_number, repo_root, pr_head)

    # pin_recover_worktree can exit, so it runs under the same finally as the
    # review itself — otherwise a fallback PR worktree would be left on disk.
    pinned_wt = None
    try:
        wt_path, pinned_wt = review_recover.pin_recover_worktree(
            recover_sha, wt_result.path, repo_root, f"recover-pr-{pr_number}",
        )

        # Inside the try, so a refusal still cleans up the worktree it read.
        # Its own flag rather than force_prompts, which has absorbed
        # --post/--no-post — see review_preflight.supersession_override.
        review_preflight.refuse_if_superseded(
            wt_path, repo, ctx.target_dir, ctx.branch,
            override=review_preflight.supersession_override(flags.force, flags.recover),
            trail=trail,
        )

        # Below every gate that can end the run, and below the refusal above:
        # three of them exit, two by prompting, so an issue link asked for any
        # earlier is one the operator types and then watches be discarded.
        if not issue_link and not issue_context and not flags.no_post and not flags.auto_post:
            issue_link = prompt.ask("Issue link (optional, Enter to skip): ")

        if issue_link or issue_context:
            trail.info("issue_context", "issue context available",
                       data={"issue_link": issue_link, "has_context": bool(issue_context)})

        session_log = str(review_dir / FILENAME_SESSION)
        prior_review_path = resolve_prior_review(review_file, session_log, has_pipeline_state)

        trail.info("delegate_orchestrate", "delegating to review-orchestrate",
                   data={"wt_path": wt_path, "max_parallel": flags.max_parallel,
                         "skipped": sorted(str(p) for p in flags.skip_phases),
                         "max_cost": flags.max_cost, "model": flags.model})

        request = review_invoke.OrchestrateRequest(
            repo=repo, pr_number=pr_number, review_file=review_file,
            wt_path=wt_path, target_dir=ctx.target_dir, session_log=session_log,
            bin_dir=flags.bin_dir, generator_version=flags.generator_version,
            prior_review_path=prior_review_path, issue_link=issue_link,
            issue_context=issue_context, max_parallel=flags.max_parallel,
            max_cost=flags.max_cost, model=flags.model, effort=flags.effort,
            max_groups=flags.max_groups, skip_phases=flags.skip_phases,
            disprove=flags.disprove, generated=flags.generated,
            recover_sha=recover_sha,
        )
        wall_ms = review_invoke.run(request)

        posting = review_publish.resolve(
            repo, pr_number, review_file,
            no_post=flags.no_post, auto_post=flags.auto_post,
            auto_submit=flags.auto_submit, bin_dir=flags.bin_dir,
        )

        return finish_review(
            request, wall_ms, ctx=ctx, trail=trail, posting=posting,
            branch_name=ctx.branch or "", pr_url=pr_url,
        )
    finally:
        review_worktree.cleanup_worktree(pinned_wt, repo_root)
        review_worktree.cleanup_worktree(wt_result, repo_root)


def run_self_review(
    ctx: pr_context.ResolvedContext,
    flags: ReviewFlags,
    review_dir: Path,
    wt_path: str,
    *,
    recover_head_sha: str,
    trail: Trail,
) -> ReviewOutcome:
    """Review the checkout at *wt_path*, which the caller has already switched.

    The worktree arrives resolved because acquiring it is what the run lock
    protects: two `--self --fix` runs on one branch are two processes committing
    to it, so the lock is claimed by the entry point before the switch, and this
    function never switches anything.
    """
    repo = ctx.repo
    pr_number = str(ctx.pr_number) if ctx.pr_number else ""
    branch_name = ctx.branch or ""
    review_file = review_dir / "review.md"
    session_log = str(review_dir / FILENAME_SESSION)

    trail.info("resolve_context", f"self-review in {repo}",
               data={"pr": pr_number, "repo": repo, "branch": branch_name,
                     "wt_path": wt_path})

    # First, ahead of the issue fetch below: this is the cheapest point at which
    # the run can still cost nothing.
    review_preflight.refuse_if_superseded(
        wt_path, repo, ctx.target_dir, branch_name,
        override=review_preflight.supersession_override(flags.force, flags.recover),
        trail=trail,
    )

    if flags.recover:
        recover_sha = review_recover.resolve_recover_sha(review_dir, recover_head_sha)
        has_pipeline_state = True
    else:
        recover_sha = ""
        has_pipeline_state = (review_dir / FILENAME_PIPELINE_STATE).is_file()
    prior_review_path = resolve_prior_review(review_file, session_log, has_pipeline_state)

    issue_link = flags.issue_link
    issue_context = ""
    if not issue_link:
        provider_info = review_issue.load_issue_provider(wt_path)
        issue_id = review_issue.extract_issue_id(provider_info.name, branch_name)
        issue_result = review_issue.fetch_issue_context(
            provider_info.name, issue_id, repo, provider_info.options,
        )
        issue_context = issue_result.context
        if issue_result.link:
            issue_link = issue_result.link

    if flags.fix and review_recover.recover_drifted(recover_sha, wt_path):
        log.error(
            f"--fix cannot run on a review recovered at {git_client.abbrev(recover_sha)} — HEAD has "
            f"moved, so the edits would land in a throwaway checkout. Recover without "
            f"--fix, then run `pr review --self --fix`."
        )
        raise SystemExit(1)

    review_wt_path, pinned_wt = review_recover.pin_recover_worktree(
        recover_sha, wt_path, wt_path, f"recover-{branch_name}",
    )

    request = review_invoke.OrchestrateRequest(
        repo=repo, pr_number=pr_number, review_file=review_file,
        wt_path=review_wt_path, target_dir=ctx.target_dir,
        session_log=session_log, bin_dir=flags.bin_dir,
        generator_version=flags.generator_version, mode="self",
        prior_review_path=prior_review_path, issue_link=issue_link,
        issue_context=issue_context, max_parallel=flags.max_parallel,
        max_cost=flags.max_cost, model=flags.model, effort=flags.effort,
        max_groups=flags.max_groups, skip_phases=flags.skip_phases,
        disprove=flags.disprove, generated=flags.generated,
        recover_sha=recover_sha, fix_pass=flags.fix,
        may_publish=flags.may_publish,
    )

    # The pin is released as soon as the pipeline is done with it, and before
    # the review is rendered — a throwaway checkout has no business outliving
    # the process that reads it. The PR flow holds its pin longer because the
    # same `finally` also owns a worktree that `pin_recover_worktree` can exit
    # out of; here there is nothing else in scope to lose.
    try:
        wall_ms = review_invoke.run(request)
    finally:
        review_worktree.cleanup_worktree(pinned_wt, wt_path)

    pr_url = f"{GITHUB_BASE_URL}/{repo}/pull/{pr_number}" if pr_number else ""

    # A self review files no GitHub review, so there is no posting decision to
    # make and no post log to aggregate.
    return finish_review(
        request, wall_ms, ctx=ctx, trail=trail,
        posting=review_publish.PostResult(False, False, ""),
        branch_name=branch_name, pr_url=pr_url,
    )
