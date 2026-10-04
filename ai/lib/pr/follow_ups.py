"""What this branch deferred, and whether a reviewer can see it.

A follow-up filed while working on a branch is the one artifact of that work
that outlives it: the branch merges, the review file is reclaimed, and the issue
stays open. Until now nothing recorded that a filing happened — `PRState.fix`
carries three fields for the single aggregate issue the comment pass files, and
a follow-up filed by a self-review, a CI pass, or by hand left no trace at all.
The trail saw the subprocess, never the intent.

So this is a ledger rather than a counter: each entry records what was filed,
which run filed it, what the branch looked like at the time, why the work was
deferred instead of done, and whether it has reached the PR description. The
last of those is what a reviewer actually depends on, and it is the only field
anything else writes after the entry is created.

Keyed on the branch, not the PR, because `pr/target.py` keys its directory on
`(repo-key, branch-slug)` with no network call — which means entries accrue from
the moment work starts, including before a PR exists. That is when a self-review
files the most.
"""

# doc-group: pr-state

from dataclasses import dataclass, field, replace as dataclass_replace
from typing import TYPE_CHECKING, ClassVar
from enum import StrEnum

from config.workbench_config import IssueProvider
import core.log
from core.serde import from_dict as _serde_from_dict
from pr.domains import Domain, Readiness

if TYPE_CHECKING:
    # Only under the type checker, as in pr.domains: pr.state imports this
    # module and nothing here imports it back.
    from pr.state import PRState


# The marked region this domain owns in a PR body. A closing marker as well as
# an opening one, unlike `SUMMARY_MARKER` in pr/summary_render.py — that one
# heads a whole issue comment the publisher replaces wholesale, so it needs no
# end. This block sits inside a body someone else's prose surrounds, so the
# rewriter has to know where it stops.
FOLLOW_UPS_OPEN = "<!-- pr:follow-ups -->"
FOLLOW_UPS_CLOSE = "<!-- /pr:follow-ups -->"


class FollowUpSource(StrEnum):
    """Which pass filed a follow-up.

    Values are persisted, so they stay as spelled even if the passes are
    renamed.
    """

    SELF_REVIEW = "self_review"
    PR_COMMENTS = "pr_comments"
    CI = "ci"
    MANUAL = "manual"


@dataclass(frozen=True)
class IssueRef:
    """A filed issue, discriminated by the tracker it lives in.

    Not a bare string. `review.issue` already has to tell these apart —
    `_ISSUE_PATTERN_JIRA_LINEAR` and `_GITHUB_CLOSE_PATTERN` match different
    shapes, and `pr.close_refs` refuses a tracker key
    outright unless the provider is Linear — so a ledger that stored `"1455"`
    would be handing every reader that discrimination problem again.

    `url` is stored rather than derived. `review.issue._issue_link` owns URL
    construction per provider and `_DEFAULT_BASE_URL` owns the hosts; rebuilding
    one here would be a second answer to a question that module already answers,
    and it would be the wrong one on a GitHub Enterprise host.
    """

    provider: IssueProvider
    id: str
    url: str = ""

    def render(self) -> str:
        """The reference as a reader of the PR body should see it.

        A GitHub number gets its `#`, which is what makes it a link there. A
        tracker key is already its own reference and takes nothing.
        """
        if self.provider is IssueProvider.GITHUB:
            return f"#{self.id.lstrip('#')}"
        return self.id


@dataclass(frozen=True)
class FollowUp:
    """One filed follow-up, and the context that explains it.

    Both `invocation` and `trail_root` are recorded. A follow-up filed by a
    subprocess records its own leaf invocation, and only the root correlates
    that back to the command a person ran — `Trail.root` returns the inherited
    root or its own invocation when it is outermost, so the two differ exactly
    when it matters.
    """

    ref: IssueRef
    title: str
    source: FollowUpSource
    filed_at: str = ""
    head_sha: str = ""
    invocation: str = ""
    trail_root: str = ""
    reason: str = ""
    # Whether this entry has reached the PR description. The only field a later
    # pass rewrites, and the one merge-readiness reads.
    in_pr_body: bool = False


@dataclass
class FollowUpDomain(Domain):
    """Every follow-up filed while working on this branch.

    Reports bookkeeping the state file owns rather than a measurement of the
    world, so `ages` is off: a filing recorded a week ago is as true now as it
    was then, and ageing it would mark the ledger unchecked on every PR whose
    last follow-up predates the day. `FixSummary` is off for the same reason.
    """

    ages: ClassVar[bool] = False

    entries: list[FollowUp] = field(default_factory=list)

    def merge_into(self, prior: "Domain") -> "FollowUpDomain":
        """Accumulate, rather than replace.

        The base replaces the domain wholesale, which would drop every entry
        filed in an earlier round — and unlike a measurement, these cannot be
        taken again: the issues are already filed. Keyed on the reference's id
        the way `FixRecord.merge_into` keys on an item's, so a re-file of the
        same issue supersedes its entry instead of doubling it, and entries the
        writer did not mention survive untouched.

        `in_pr_body` is the exception to "the newer write wins". Projection sets
        it, and a later filing pass constructing an entry for the same issue
        knows nothing about the body — so the flag is OR-ed across the merge,
        the way `FixSummary` carries its cycle-scoped debts. Without that a
        second filing silently un-projects an entry the reviewer can already
        see, and the blocker reappears with nothing to do about it.
        """
        assert isinstance(prior, FollowUpDomain)
        merged = {entry.ref.id: entry for entry in prior.entries if entry.ref.id}
        # An entry with no id cannot be de-duplicated. Kept side by side rather
        # than collapsed onto one "" key, as FixRecord does.
        # ceiling: an id-less entry is kept once per round it appears in. It can
        # only arise from a filing that reported no issue id, which is a
        # failure worth seeing twice — cap this if a pass turns out to produce
        # them routinely.
        anonymous = [entry for entry in prior.entries if not entry.ref.id]
        for entry in self.entries:
            if not entry.ref.id:
                anonymous.append(entry)
                continue
            was = merged.get(entry.ref.id)
            if was is not None and was.in_pr_body:
                entry = dataclass_replace(entry, in_pr_body=True)
            merged[entry.ref.id] = entry
        return dataclass_replace(
            super().merge_into(prior),
            entries=list(merged.values()) + anonymous,
        )

    @classmethod
    def _from_raw(cls, data: dict | None) -> "FollowUpDomain":
        """Read the ledger, keeping the entries that are readable.

        `serde._coerce_list` raises on the first element it cannot convert and
        the field then falls back to its default, so one entry naming a provider
        this build does not know would discard the whole ledger. Every other
        domain can be rebuilt by re-running its pass; this one cannot, because
        the issues it records are already filed and nothing else remembers them.
        So each entry is read on its own and a bad one is dropped loudly.
        """
        data = dict(data or {})
        raw_entries = data.pop("entries", None) or []
        domain = _serde_from_dict(cls, data)
        entries = []
        for raw in raw_entries:
            try:
                entries.append(_serde_from_dict(FollowUp, raw))
            except (TypeError, ValueError) as exc:
                core.log.warn(f"dropping an unreadable follow-up entry: {exc}")
        domain.entries = entries
        return domain

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """What `_from_raw` accepts, for the schema `pr --tool-schema` publishes.

        Reachable from PRState, so the published schema has to describe what is
        actually readable rather than what the dataclass spells.
        """
        return object_schema

    def render_status(self) -> list[str]:
        if not self.entries:
            return []
        unprojected = [e for e in self.entries if not e.in_pr_body]
        lines = [f"**Follow-ups**: {len(self.entries)} filed"]
        for entry in self.entries:
            mark = " " if entry.in_pr_body else "!"
            lines.append(f"  {mark} {entry.ref.render()} — {entry.title}")
        if unprojected:
            lines.append(f"  {len(unprojected)} not in the PR description")
        return lines

    def readiness(self, state: "PRState") -> Readiness:
        """Whether every filed follow-up has reached the PR description.

        Reported as unchecked rather than as a blocker. Every automated path
        projects its own entries, so the only way one goes unprojected is a
        capture that ran without a projection behind it — a red merge-readiness
        line nobody can act on. `Readiness.unchecked` is the field for something
        the domain could not answer, which is what this is until the projection
        has been seen working on real PRs.

        Silent before a PR exists: entries accrue from the first filing, and
        there is no description to reach until someone opens one.
        """
        if not state.identity.pr_number:
            return Readiness()
        missing = [e for e in self.entries if not e.in_pr_body]
        if not missing:
            return Readiness()
        refs = ", ".join(e.ref.render() for e in missing)
        return Readiness(unchecked=(f"follow-ups not in the PR body ({refs})",))


def render_block(entries: list[FollowUp]) -> str:
    """The marked region, or "" when there is nothing to say.

    Rendered from the ledger every time rather than edited in place, so the
    block is a projection of state and not a second copy of it.
    """
    listed = [e for e in entries if e.ref.id]
    if not listed:
        return ""
    lines = [FOLLOW_UPS_OPEN, "### Follow-ups", ""]
    lines += [f"- {e.ref.render()} — {e.title}" for e in listed]
    lines.append(FOLLOW_UPS_CLOSE)
    return "\n".join(lines)


def strip_block(body: str) -> str:
    """`body` with every marked region removed.

    Every one, not the first. The describe pass shows the model a body that
    already holds the block and asks for a revision, so a model that reproduces
    it faithfully hands back a body the injector would otherwise append a second
    copy to. Removing before writing makes the operation idempotent however many
    copies arrive.

    An unclosed opening marker takes the rest of the body with it: the
    alternative is leaving a marker behind that the next strip would pair with a
    later block's closer, swallowing prose between them.
    """
    out = body
    while FOLLOW_UPS_OPEN in out:
        head, _, rest = out.partition(FOLLOW_UPS_OPEN)
        _, closed, tail = rest.partition(FOLLOW_UPS_CLOSE)
        out = head.rstrip() + ("\n" + tail.lstrip() if closed and tail.strip() else "")
    return out


def project(body: str, entries: list[FollowUp]) -> str:
    """`body` with exactly one marked region, rendered from `entries`.

    Appended at the end rather than inserted, for the reason
    `_pr_append_issue_link` gives about closing refs: the template's section
    headers are a contract the body is checked against, and a block placed among
    them reads as part of whichever section it landed in.
    """
    stripped = strip_block(body).rstrip()
    block = render_block(entries)
    if not block:
        return stripped
    return f"{stripped}\n\n{block}" if stripped else block
