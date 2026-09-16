"""What happens to a review once the pipeline has produced it.

The sequence both flows share: drop the prior copy, show the review, summarise
it, record it. Order is the contract, and it is the same order whichever flow
got here, which is what makes this a module rather than a parameter.

Kept apart from `review.run` because the two answer different questions. That
module decides how a review is *reached* — which worktree, which checks, which
prompts — and the flows differ throughout. This one decides what a finished
review *means*, and nothing here differs at all.
"""

# doc-group: pipeline

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core import log
from core import prompt
from core.trail import Trail
from gh import client as gh_client
from pr import context as pr_context
from pr.review_sync import sync_review_domain
from review import invoke as review_invoke
from review import publish as review_publish
from review.paths import FILENAME_PIPELINE_STATE, FILENAME_PRIOR, archive_review
from review.summary import build_review_summary, print_summary


@dataclass(frozen=True)
class ReviewOutcome:
    """Where the review landed, for a caller that has to report on it."""

    repo: str
    pr_number: str
    review_file: Path


def _display(path: Path) -> None:
    log.blank()
    log.info("─── Review ───")
    print()
    print(path.read_text())
    print()


def resolve_prior_review(review_file: Path, session_log: str, resume: bool) -> str:
    """The prior review this run reconciles against.

    Destructive when it archives, and that is why `resume` exists: a resumed run
    is finishing the review already on disk, not replacing it. Two gates below
    every call site exit rather than return — a `--fix` refused for a drifted
    recover, and a pin whose commit is gone — and neither is reachable except
    under ``--recover``, which forces `resume` true. Archiving above a gate that
    can still abort would rotate a review the run never replaced.
    """
    review_dir = review_file.parent
    if resume:
        prior = review_dir / FILENAME_PRIOR
        if prior.is_file():
            return str(prior)
        return ""
    return archive_review(review_file, session_log)


def cleanup_prior_review(review_file: Path, prior_path: str) -> None:
    """Drop the prior copy once the run that needed it has finished with it."""
    if not prior_path:
        return
    pipeline_state = review_file.parent / FILENAME_PIPELINE_STATE
    if not pipeline_state.is_file():
        try:
            os.unlink(prior_path)
        except OSError:
            pass


def summarise(
    repo: str, pr_number: str, review_file: Path, session_log: str,
    *, pr_url: str = "", post_session_log: str = "", branch_name: str = "",
    wall_clock_ms: int | None = None,
) -> None:
    print_summary(
        build_review_summary(repo, pr_number, str(review_file)),
        session_log, pr_url, post_session_log, branch_name, wall_clock_ms,
    )


def record_domain(
    ctx: pr_context.ResolvedContext, review_file: Path, *, trail: Trail,
) -> None:
    """Record the review's outcome in the state of the target *ctx* names.

    ``ctx`` is the identity the entry point resolved for this run, threaded down
    rather than derived a second time here. Re-deriving read the *caller's*
    checkout, which on the ``--pr`` path is routinely a different branch than
    the PR's — so the verdict, finding counts, and cost landed with whatever the
    caller happened to be standing on. One identity per run, derived once.
    """
    if not review_file or not review_file.is_file():
        return
    report = build_review_summary(ctx.repo, str(ctx.pr_number or ""), str(review_file))
    sync_review_domain(ctx, report, trail=trail)


def verify_pr_ownership(pr_number: str, repo: str) -> None:
    """Confirm before self-reviewing a PR someone else owns.

    Only the self flow asks. A `--self --fix` pass commits to the branch it is
    reviewing, so running it against another author's PR writes to their work;
    a plain PR review reads and posts, which is what reviewing is for.
    """
    author = gh_client.pr_view(pr_number, "author", repo=repo).get("author") or {}
    pr_author = author.get("login", "")
    gh_user = gh_client.login()

    if not pr_author or not gh_user or pr_author == gh_user:
        return

    log.warn(f"PR #{pr_number} is owned by {pr_author}, not {gh_user}")
    if not prompt.confirm("Continue with self-review?"):
        raise SystemExit(0)


def finish_review(
    request: review_invoke.OrchestrateRequest,
    wall_ms: int,
    *,
    ctx: pr_context.ResolvedContext,
    trail: Trail,
    posting: review_publish.PostResult,
    branch_name: str = "",
    pr_url: str = "",
) -> ReviewOutcome:
    """Everything after the pipeline returns, for both flows.

    Order is the contract: clean up the prior copy, show the review, summarise
    it, then record it. The domain write comes last and only here, so a run that
    failed upstream — `invoke.run` exits rather than returning — records
    nothing, and a run that merely declined to post still does.
    """
    cleanup_prior_review(request.review_file, request.prior_review_path)
    _display(request.review_file)

    summarise(
        request.repo, request.pr_number, request.review_file, request.session_log,
        pr_url=pr_url, post_session_log=posting.post_session_log,
        branch_name=branch_name, wall_clock_ms=wall_ms,
    )
    for hint in posting.hints:
        log.dim(hint)

    record_domain(ctx, request.review_file, trail=trail)
    return ReviewOutcome(request.repo, request.pr_number, request.review_file)


