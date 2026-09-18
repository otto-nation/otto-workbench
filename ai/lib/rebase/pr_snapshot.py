"""What GitHub says about the PR being rebased, read once per run.

A fresh rebase asked GitHub about its PR twice: once for ``baseRefName``, to
know what to replay onto, and once for ``state``, to refuse a branch whose PR
already merged. Two round trips for one PR, and each new question — is it a
draft, who is reviewing it — would have added a third.

So the PR is read once and the answer is passed around. ``gh pr view`` takes a
field list, so asking for six costs exactly what asking for one did.

Best effort, like every tracker read in this codebase: ``gh`` may be absent,
unauthenticated, rate-limited, or the branch may have no PR at all. All of those
arrive as ``PRSnapshot()`` with ``answered`` false, which every consumer reads as
"the tracker has nothing to say" — never as an answer that stops a rebase. The
git-side signals still get their turn.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass

from gh import client as gh_client
from pr import context as pr_context

# The fields one read collects. `state`, `number` and `url` answer the
# already-landed refusal; `baseRefName` picks the ref to replay onto; the last
# two are what a force-push notice needs to say whose PR it is about to rewrite.
FIELDS = ("state", "number", "url", "baseRefName", "isDraft", "reviewDecision")

MERGED = "MERGED"


@dataclass(frozen=True)
class PRSnapshot:
    """One read of a PR, or the empty answer when GitHub could not be asked."""

    state: str = ""
    number: int = 0
    url: str = ""
    base_ref: str = ""
    is_draft: bool = False
    review_decision: str = ""

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
        anybody.
        """
        return self.answered and not self.merged and not self.is_draft


def fetch(cwd: str, ctx: pr_context.ResolvedContext) -> PRSnapshot:
    """Read the PR for *ctx*, or the empty snapshot when it cannot be read.

    Asked by number when one is resolved and by branch otherwise, matching what
    ``gh pr view`` accepts — so a branch whose PR nobody has looked up yet is
    still found.
    """
    target = str(ctx.pr_number) if ctx.pr_number else ctx.branch
    if not target:
        return PRSnapshot()
    data = gh_client.pr_view(target, *FIELDS, repo=ctx.repo, cwd=cwd)
    if not data:
        return PRSnapshot()
    return PRSnapshot(
        state=data.get("state") or "",
        number=data.get("number") or 0,
        url=data.get("url") or "",
        base_ref=data.get("baseRefName") or "",
        is_draft=bool(data.get("isDraft")),
        review_decision=data.get("reviewDecision") or "",
    )
