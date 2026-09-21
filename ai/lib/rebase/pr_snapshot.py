"""What GitHub says about the PR being rebased, read once per run.

A fresh rebase asked GitHub about its PR twice: once for ``baseRefName``, to
know what to replay onto, and once for ``state``, to refuse a branch whose PR
already merged. Two round trips for one PR, and each new question — is it a
draft, who is reviewing it — would have added a third.

So the PR is read once and the answer is passed around. ``gh pr view`` takes a
field list, so asking for six costs exactly what asking for one did.

The same read also settles whether to warn before a force-push reaches a
shared branch: ``name_the_open_pr`` gates on ``state`` and ``isDraft`` and is
called from both places that force-push — ``rebase_success`` for the modes
that land there, and ``cmd_push`` for the bare ``pr rebase`` that does not.

Best effort, like every tracker read in this codebase: ``gh`` may be absent,
unauthenticated, rate-limited, or the branch may have no PR at all. All of those
arrive as ``PRSnapshot()`` with ``answered`` false, which every consumer reads as
"the tracker has nothing to say" — never as an answer that stops a rebase. The
git-side signals still get their turn.

One of those causes is different in kind, and ``refused`` separates it. When the
budget breaker declined to make the call, the silence is one this machine
imposed on itself a moment ago rather than a property of the environment: the
PR is knowable, we simply did not ask. Everything else — no gh, no auth, no
network, no PR — is a machine that cannot answer this question at all, and a
rebase that refused on it would never run there. `pr rebase` is the one caller
that acts on the difference, because its "proceed" branch force-pushes.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass

from core import log
from core.trail import Trail, tinfo
from gh import budget as gh_budget
from gh import client as gh_client
from pr import context as pr_context

# The fields one read collects. `state`, `number` and `url` answer the
# already-landed refusal; `baseRefName` picks the ref to replay onto; the last
# two are what a force-push notice needs to say whose PR it is about to rewrite.
FIELDS = ("state", "number", "url", "baseRefName", "isDraft", "reviewDecision")

MERGED = "MERGED"
OPEN = "OPEN"


@dataclass(frozen=True)
class PRSnapshot:
    """One read of a PR, or the empty answer when GitHub could not be asked."""

    state: str = ""
    number: int = 0
    url: str = ""
    base_ref: str = ""
    is_draft: bool = False
    review_decision: str = ""
    # Set only when the budget breaker declined the call. False covers both a
    # read that succeeded and one that failed for any other reason, so a caller
    # that does not care about the distinction sees today's behaviour.
    refused: bool = False
    # The remedy for the latch this read found armed, captured here rather
    # than re-derived later. The latch's own window can pass between this read
    # and whatever builds the refusal message from it, and a re-query at that
    # later point would silently lose the reset time — or the whole hint — to
    # a latch that already expired.
    remedy: str = ""

    @property
    def answered(self) -> bool:
        """Whether GitHub told us anything at all.

        Keyed on ``state``, which every real PR has. The distinction matters
        because an unanswered read and an open PR are both "do not refuse", and
        only one of them means we know that.
        """
        return bool(self.state)

    @property
    def merged(self) -> bool:
        """Whether the PR has already landed \u2014 the one state that refuses."""
        return self.state == MERGED

    @property
    def open_and_ready(self) -> bool:
        """Whether this is a PR someone may be reviewing right now.

        Draft is excluded deliberately: a draft is the author's own workspace,
        and force-pushing to one is the routine case rather than a surprise to
        anybody. A closed-but-unmerged PR is excluded too — nobody is reviewing
        an abandoned PR, so only ``OPEN`` qualifies.
        """
        return self.state == OPEN and not self.is_draft


def name_the_open_pr(
    snapshot: PRSnapshot | None, *, trail: Trail | None = None,
) -> None:
    """Say whose PR is about to be rewritten, when there is one.

    A branch with an open PR is shared: someone may be reading it, may have
    marked it ready, may be merging it. Rewriting its history is a legitimate
    thing to do — review findings, CI fixes, a rebase a reviewer asked for — so
    this is a notice and not a gate, the same call the pre-push hook makes for
    the same reason. A gate here would fire on the common good case and be
    waived by reflex.

    Lives beside the snapshot rather than in ``lifecycle`` because both places
    that force-push have to call it: ``rebase_success`` for the modes that land
    there, and ``cmd_push`` for ``RunMode.PUSH``, which is what a bare
    ``pr rebase`` selects and which lands nowhere near the other.

    Says nothing for a draft, a closed PR, or when GitHub could not be asked.
    """
    if snapshot is None or not snapshot.open_and_ready:
        return
    where = snapshot.url or f"#{snapshot.number}"
    log.warn(f"This branch has an open PR, marked ready for review: {where}")
    log.dim("Force-pushing rewrites what a reviewer may be reading — say on the "
            "PR what this push changed.")
    tinfo(trail, "ready_pr_push", "force-pushing a branch with a ready PR",
          data={"pr": snapshot.number, "url": snapshot.url,
                "review_decision": snapshot.review_decision})


def fetch(cwd: str, ctx: pr_context.ResolvedContext) -> PRSnapshot:
    """Read the PR for *ctx*, or the empty snapshot when it cannot be read.

    Asked by number when one is resolved and by branch otherwise, matching what
    ``gh pr view`` accepts — so a branch whose PR nobody has looked up yet is
    still found.
    """
    target = str(ctx.pr_number) if ctx.pr_number else ctx.branch
    # Neither is set only when both pr_number and branch are empty, which the
    # guard below turns into the empty snapshot before an empty target ever
    # reaches gh — never a bare `gh pr view` with nothing to look up.
    if not target:
        return PRSnapshot()
    data = gh_client.pr_view(target, *FIELDS, repo=ctx.repo, cwd=cwd)
    if not data:
        # Asked after the call, not before: a latch that armed *during* this
        # read is the case that matters, and one that expires between the two
        # readings would have let the call through anyway. `gh pr view` spends
        # the GraphQL budget despite looking like neither `api` nor `graphql`.
        # The remedy is read from the same latch, in the same instant, rather
        # than left for a later caller to re-derive from a latch that may have
        # since expired.
        latch = gh_budget.latched(gh_budget.Resource.GRAPHQL)
        return PRSnapshot(
            refused=latch is not None,
            remedy=latch.remedy() if latch else "",
        )
    return PRSnapshot(
        state=data.get("state") or "",
        number=data.get("number") or 0,
        url=data.get("url") or "",
        base_ref=data.get("baseRefName") or "",
        is_draft=bool(data.get("isDraft")),
        review_decision=data.get("reviewDecision") or "",
    )
