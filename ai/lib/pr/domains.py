"""The domains a PR's state is made of.

Each ``pr`` subcommand owns one domain and writes it as a unit. A domain is a
dataclass subclassing :class:`Domain`; subclassing is the registration, and
``pr_state`` derives its registry from ``PRState``'s own annotations, so a new
domain is added here and named there and nowhere else.

This module holds the domain types, the vocabulary they are written in, and the
two things every domain says about itself: its line on the ``pr status``
dashboard (``render_status``) and its answer to whether the PR may merge
(``readiness``). Both default to saying nothing, so a domain with nothing to
report is silent by declaring nothing rather than by being left off a list.
``pr status`` folds over the registry for both, which is what stops the
dashboard and the registry from disagreeing.

Every domain also carries a :class:`~pr_fix.FixRecord`, declared on the base
class for the same reason: a domain that gains a fix pass gains somewhere to
record it without a field being added anywhere. ``pr_fix`` holds that record and
the vocabulary it is written in, and imports nothing back.

``pr_state`` holds the envelope over these, the registry and the state file
I/O, and imports this module — never the other way round. So does
``pr_comments_fix``, which holds the comment pass's domain: the closeout only
that pass owes, over the same record every domain here carries.

#### Rebase refusals

The already-landed signals answer "is this work already in the base?". Two more
answer a different question — "is replaying this branch onto that base a safe
thing to do at all?" — and refuse on the same exit code, with the same
``--force`` override:

| Signal | What it reads | When it fires |
|---|---|---|
| `no_merge_base` | `git merge-base <base> HEAD` exits nonzero | The branch and its base share no commit |
| `conflicts_over_budget` | distinct conflicted files across the whole rebase | The count passes `_CONFLICT_FILE_BUDGET` |

`no_merge_base` is exact rather than heuristic, and it costs one local git
command, so it is asked before the landed signals rather than after them — those
compare HEAD against a ref an unrelated branch has no relationship to, so they
answer nothing there. A repo that was re-initialised leaves branches descending
from a second root; rebasing one replays its entire history onto a base it has
nothing in common with, which conflicts in every file both roots happen to
contain.

A ref that does not resolve is not this. `git merge-base` fails identically for a
typo'd `--onto` and for a base branch the fetch never brought down, so the check
verifies the ref names a commit first and passes when it does not — refusing
those as unrelated history would send the operator after a root they do not
have, where git's own error for the missing ref says what actually went wrong.

The budget is the circuit breaker for what that produces. Conflict resolution is
an AI call per conflicted file, with edit access to the worktree, and the wider
the spread the less any single call can tell an intended change from an
unrelated one — which is how a rebase resolving 51 conflicts rewrote
`bin/otto-workbench`, a file the branch never touched, into invalid bash. Past
the budget the rebase is aborted before the first resolution call, so the
worktree is left clean rather than half-replayed.

The count is of *distinct files* across the whole rebase, not conflicts: a file
conflicting in every replayed commit is one file's worth of risk, and counting
it once per commit would refuse a narrow rebase over a long branch. The tally
carries across steps, so a rebase that widens gradually is refused at the step
that crosses the line rather than never.

A resumed rebase waives the budget. The conflicts are already sitting in the
worktree by then; refusing would strand it mid-rebase with no path forward
except the manual resolution the command exists to avoid. The waiver is the
resume path passing `force=True` into the same parameter `--force` sets, so
there is one waiver mechanism rather than two.
"""

# doc-group: pr-state

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as dataclass_replace
from enum import Enum, StrEnum
from pathlib import Path

# get_type_hints(CIDomain) resolves its `runs` annotation against the namespace
# of the module CIDomain is defined in, so RunState must be bound here;
# ci_failures keeps its own pr_state import under TYPE_CHECKING, which is what
# keeps this acyclic.
from pr.ci_failures import RunState

from git import client as git_client
from pr.fix import FixRecord


@dataclass(frozen=True)
class Readiness:
    """One domain's answer to whether the PR may merge.

    Two lists rather than a verdict, because the dashboard prints both and a
    boolean would lose the difference between them: a blocker is something the
    domain measured and found wrong, and an unchecked entry is something it
    could not answer because it never ran. A PR with neither is mergeable as far
    as this domain is concerned.
    """

    # What is wrong, in the words the dashboard prints.
    blockers: tuple[str, ...] = ()
    # What this domain cannot answer because it never ran.
    unchecked: tuple[str, ...] = ()


@dataclass
class Domain:
    """A section of PRState that one subcommand owns and writes as a unit.

    Subclassing is the registration. ``_domains`` derives the registry from
    PRState's own annotations, so a new domain is one field there and nothing
    else — no updater, no table entry, no deserializer.

    Every domain records when it was last written, so the field lives here
    rather than being restated by each one.  It is not stamped automatically:
    a default-constructed domain would then claim a write that never happened,
    and the writer is the only code that knows whether one occurred.

    ``fix`` is here for the same reason and one more: a fix pass is something a
    domain either has or does not, and declaring the record once means a domain
    that gains one gains a place to write it rather than a field to add.  A
    domain that never fixes anything carries an empty record, which renders
    nothing and blocks nothing.
    """

    updated_at: str = ""
    fix: FixRecord = field(default_factory=FixRecord)

    def merge_into(self, prior: "Domain") -> "Domain":
        """Combine this update with what is already stored.

        A write replaces what came before, which is what every domain wants
        except the ones that accumulate across rounds. Those override this and
        fold ``prior`` in themselves, so the policy lives with the shape it
        applies to rather than in the code that happens to call the write.

        The fix record is the one part every domain accumulates, so it is folded
        here rather than left to each override — a subcommand writing its own
        domain says nothing about the fix pass that ran against it, and a
        wholesale replace would drop the record every time.
        """
        return dataclass_replace(self, fix=self.fix.merge_into(prior.fix))

    def render_status(self) -> list[str]:
        """This domain's lines on the ``pr status`` dashboard.

        Empty means the domain has nothing to say, and ``pr status`` prints
        neither the lines nor the blank one that would separate them — which is
        how a domain stays off the dashboard by declaring nothing rather than by
        being absent from a list someone maintains.
        """
        return []

    def readiness(self) -> Readiness:
        """This domain's answer to whether the PR may merge.

        Defaulting to "nothing to say" is what lets a domain that has no bearing
        on merging — a description, a supersession verdict — take part in the
        fold without contributing a blocker nobody asked for.
        """
        return Readiness()


@dataclass
class CIDomain(Domain):
    """Full CI domain — summary fields plus detailed run history.

    Merges the former CISummary (summary snapshot) with run-history tracking
    that previously lived in ci_failures.CIState. All CI state now lives in
    a single domain within PRState.
    """
    # Summary fields (formerly CISummary)
    conclusion: str = ""
    failure_count: int = 0
    failure_kinds: dict[str, int] = field(default_factory=dict)
    last_run_id: int | None = None
    last_run_number: int | None = None
    # Detailed run tracking (formerly in CIState)
    # Keyed by run_id. JSON stringifies every key on the way out; serde
    # restores the ints on the way back in.
    runs: dict[int, RunState] = field(default_factory=dict)
    latest_run_id: int | None = None

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**CI**: not checked yet"]
        icon = "green" if self.conclusion == "success" else "red"
        lines = [f"**CI** ({icon}): {self.conclusion} — {self.failure_count} failure(s)"]
        for kind, count in sorted(self.failure_kinds.items()):
            lines.append(f"  {kind}: {count}")
        if self.last_run_number:
            lines.append(f"  run #{self.last_run_number}")
        return lines

    def readiness(self) -> Readiness:
        if not self.updated_at:
            return Readiness(unchecked=("CI",))
        if self.conclusion != "success":
            return Readiness(blockers=("CI failing",))
        return Readiness()


class ReviewVerdict(Enum):
    """The call a review reaches, in both spellings it is written in.

    `value` is the persisted state value; `prose` is the word the synthesis
    prompt asks for and the `## Verdict` section states. One member owns both,
    so what a review says and what `pr status` reports cannot disagree.

    `rank` orders the verdicts the finding counts derive. Disapprove is
    deliberately unranked: per `review-templates/synthesis.md` it means the
    overall approach is wrong and the PR should not land in any form — a
    holistic judgment no count implies and none refutes.

    Declaration order is significant: `VERDICT_OPTIONS` in `review_prompt.py`
    renders the options the synthesis prompt offers by iterating this enum, so
    reordering these members reorders what agents are asked to choose from.
    """

    APPROVE = ("approve", "Approve", 0)
    NEEDS_DISCUSSION = ("needs_discussion", "Needs discussion", 1)
    CHANGES_REQUESTED = ("changes_requested", "Request changes", 2)
    DISAPPROVE = ("disapprove", "Disapprove", None)

    def __new__(cls, value: str, prose: str, rank: int | None) -> ReviewVerdict:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.prose = prose
        obj.rank = rank
        return obj

    @classmethod
    def from_counts(cls, must: int, should: int) -> ReviewVerdict:
        """The strongest verdict the finding counts alone justify.

        Takes the two counts rather than a counts dict because callers key
        theirs differently — by severity letter mid-pipeline, by JSON name in
        the summary — and the rule must not depend on which one it is handed.
        """
        if must:
            return cls.CHANGES_REQUESTED
        if should:
            return cls.NEEDS_DISCUSSION
        return cls.APPROVE

    @classmethod
    def stated_in(cls, text: str) -> ReviewVerdict | None:
        """The verdict `text` opens with, bold markers and case ignored."""
        m = VERDICT_PROSE_RE.match(text.strip())
        return _VERDICT_BY_PROSE[m.group(1).lower()] if m else None

    def outranks(self, other: ReviewVerdict | None) -> bool:
        """Whether this verdict is a stronger call than `other`.

        An unranked verdict outranks nothing and is outranked by nothing, which
        is what leaves a stated Disapprove untouched by any count-derived call.
        """
        if other is None or self.rank is None or other.rank is None:
            return False
        return self.rank > other.rank


_VERDICT_BY_PROSE = {v.prose.lower(): v for v in ReviewVerdict}

# Longest prose first so "Request changes" cannot be shadowed by a shorter
# alternative sharing its prefix.
VERDICT_PROSE_RE = re.compile(
    r"\*{0,2}(" + "|".join(
        re.escape(p) for p in sorted(_VERDICT_BY_PROSE, key=len, reverse=True)
    ) + r")\*{0,2}",
    re.IGNORECASE,
)

# The same words followed by the dash that separates them from the rationale —
# what a renderer strips when the heading already carries the verdict.
VERDICT_PROSE_PREFIX_RE = re.compile(
    r"^" + VERDICT_PROSE_RE.pattern + r"\s*[—–\-]\s*", re.IGNORECASE,
)


class ReviewStatus(Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"


class RebaseStatus(Enum):
    COMPLETED = "completed"
    CONFLICTS = "conflicts"
    ABORTED = "aborted"
    ALREADY_LANDED = "already_landed"
    UNRELATED_HISTORY = "unrelated_history"
    CONFLICTS_OVER_BUDGET = "conflicts_over_budget"


def _verdict_display(verdict: str) -> str:
    """The verdict the way a review states it, or as stored if unrecognised."""
    try:
        return ReviewVerdict(verdict).prose
    except ValueError:
        return verdict


@dataclass
class ReviewSummary(Domain):
    """Snapshot written by ``pr review``."""
    review_file: str = ""
    review_type: str = ""
    head_sha: str = ""
    finding_counts: dict[str, int] = field(default_factory=dict)
    verdict: str = ""
    status: str = ""
    failure_detail: str = ""
    cost_usd: float = 0.0
    total_tokens: int = 0

    @property
    def _incomplete(self) -> bool:
        """Whether the run this describes did not finish its phases."""
        return self.status in (ReviewStatus.PARTIAL.value, ReviewStatus.ERROR.value)

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Review**: not run yet"]
        suffixes = []
        if self.status == ReviewStatus.PARTIAL.value:
            detail = f" — {self.failure_detail}" if self.failure_detail else ""
            suffixes.append(f"[PARTIAL{detail}]")
        elif self.status == ReviewStatus.ERROR.value:
            detail = f" — {self.failure_detail}" if self.failure_detail else ""
            suffixes.append(f"[ERROR{detail}]")
        if self.verdict == ReviewVerdict.DISAPPROVE.value:
            suffixes.append("[DISAPPROVED]")
        suffix = " " + " ".join(suffixes) if suffixes else ""
        # The dashboard shows the verdict the way the review states it, not the
        # way state serializes it. An unrecognised value is shown as stored
        # rather than dropped, so state written by an older version still reads.
        verdict_part = f": {_verdict_display(self.verdict)}" if self.verdict else ""
        lines = [f"**Review** ({self.review_type}){verdict_part}{suffix}"]
        if self.finding_counts:
            parts = [f"{sev}: {count}" for sev, count in sorted(self.finding_counts.items())]
            lines.append(f"  findings: {', '.join(parts)}")
        if self.cost_usd:
            lines.append(f"  cost: ${self.cost_usd:.2f}")
        if self._incomplete:
            lines.append("  recover: pr review --recover")
        return lines

    def readiness(self) -> Readiness:
        if not self.updated_at:
            return Readiness(unchecked=("review",))
        blockers = []
        if self.finding_counts.get("M", 0) > 0:
            blockers.append("must-fix findings")
        if self._incomplete:
            blockers.append("review incomplete")
        return Readiness(blockers=tuple(blockers))


@dataclass
class CommentsSummary(Domain):
    """Snapshot written by ``pr comments``."""
    total_threads: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    blocking_reviewers: list[str] = field(default_factory=list)
    has_approvals: bool = False
    seen_issue_comment_ids: list[int] = field(default_factory=list)
    seen_review_body_comment_ids: list[int] = field(default_factory=list)

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Comments**: not checked yet"]
        lines = [f"**Comments**: {self.total_threads} thread(s)"]
        if self.by_state:
            parts = [f"{s}: {ct}" for s, ct in sorted(self.by_state.items())]
            lines.append(f"  {', '.join(parts)}")
        if self.blocking_reviewers:
            lines.append(f"  blocking: {', '.join(self.blocking_reviewers)}")
        return lines

    def readiness(self) -> Readiness:
        if not self.updated_at:
            return Readiness(unchecked=("comments",))
        if self.blocking_reviewers:
            return Readiness(blockers=("blocking reviewers",))
        return Readiness()


@dataclass
class TriageSummary(Domain):
    """Snapshot written by ``pr triage``."""
    total: int = 0
    actionable: int = 0
    valid: int = 0
    questions: int = 0
    comment_items_total: int = 0
    comment_items_actionable: int = 0

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Triage**: not run yet"]
        return [f"**Triage**: {self.total} threads — {self.actionable} actionable "
                f"({self.valid} valid), {self.questions} questions"]


# Every status `pr rebase` refuses on, and the phrase the dashboard reports it
# with. One table so a new refusal shows up here by adding a row, rather than by
# being forgotten and rendering as a completed rebase. The module docstring
# above says what each one reads and when it fires.
_REFUSAL_REASONS = {
    RebaseStatus.ALREADY_LANDED.value: "branch already landed",
    RebaseStatus.UNRELATED_HISTORY.value: "branch shares no history with its base",
    RebaseStatus.CONFLICTS_OVER_BUDGET.value: "too many conflicts to resolve automatically",
}


@dataclass
class RebaseSummary(Domain):
    """Snapshot written by ``pr rebase``."""
    status: str = ""
    target_base: str = ""
    commits_replayed: int = 0
    conflicts_resolved: int = 0
    files_resolved: list[str] = field(default_factory=list)
    files_stale: list[str] = field(default_factory=list)
    force_pushed: bool = False

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Rebase**: not run yet"]
        if self.status == RebaseStatus.CONFLICTS.value:
            return ["**Rebase**: conflicts — resolve manually or run `pr rebase --fix`"]
        if self.status == RebaseStatus.ABORTED.value:
            return ["**Rebase**: aborted"]
        if self.status in _REFUSAL_REASONS:
            return [f"**Rebase**: refused — {_REFUSAL_REASONS[self.status]} "
                    "(rerun with `pr rebase --force` to override)"]
        if self.conflicts_resolved == 0:
            desc = f"clean rebase — {self.commits_replayed} commit(s) replayed"
        else:
            desc = (f"resolved {self.conflicts_resolved} file(s) across "
                    f"{self.commits_replayed} commit(s)")
        if self.force_pushed:
            desc += ", force-pushed"
        lines = [f"**Rebase**: {desc}"]
        if self.files_stale:
            lines.append(
                f"**Rebase**: regeneration failed for {', '.join(self.files_stale)} — "
                "content is the incoming side, unmerged"
            )
        return lines


@dataclass
class PushDomain(Domain):
    """How far the local branch is ahead of its remote.

    ``ahead`` is None for a branch with no remote ref and a count otherwise, so
    "never pushed" and "pushed and up to date" are different answers rather than
    both reading as zero.

    ``pr status`` refreshes this live and does not persist it — the answer is a
    local git question and costs one command. It is a domain rather than a
    dashboard line computed on the side because merge readiness has to fold it
    in alongside the rest, and because phase 2's ``git/land.py`` is what starts
    writing it, at the moment it learns the answer.
    """

    ahead: int | None = None

    @classmethod
    def observed(cls, worktree_root: Path, branch: str, *, updated_at: str) -> "PushDomain":
        """Read the branch's position against its remote, now.

        ``updated_at`` is passed in rather than stamped here for the reason
        ``Domain`` gives: the writer is what knows a write occurred, and a
        domain that stamps itself would claim one on every default construction.
        """
        r = git_client.run("rev-list", "--count", f"origin/{branch}..HEAD",
                           cwd=worktree_root)
        count = r.stdout.strip() if r.ok else ""
        return cls(ahead=int(count) if count.isdigit() else None,
                   updated_at=updated_at)

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return []
        if self.ahead is None:
            return ["**Push**: branch not pushed to remote"]
        if self.ahead == 0:
            return ["**Push**: up to date"]
        return [f"**Push**: {self.ahead} commit(s) not pushed"]

    def readiness(self) -> Readiness:
        # An unobserved push domain blocks nothing rather than reading as "not
        # pushed": every surface that cares refreshes it first, so an empty one
        # means nobody asked, not that the branch is behind.
        if not self.updated_at or self.ahead == 0:
            return Readiness()
        desc = f"{self.ahead} unpushed commit(s)" if self.ahead else "branch not pushed"
        return Readiness(blockers=(desc,))


@dataclass
class DescribeSummary(Domain):
    """Snapshot written by ``pr describe``.

    ``head_sha`` is what makes the pass commit-aware: a description already
    written for the current HEAD does not need rewriting, so a repeated run is
    a no-op instead of another AI call against an unchanged branch.
    """
    head_sha: str = ""
    template_path: str = ""
    changed: bool = False


class SupersessionKind(StrEnum):
    """Which supersession check produced a finding. Printed, and sent to the trail.

    A `StrEnum` for the same reason `land.CommitStatus` is one: these strings are
    persisted in the state file and read back by a later process, so the values
    are the contract and the enum is for the code.
    """

    # The branch's first commit was written long before it was committed.
    REBASE_SKEW = "rebase_skew"
    # The branch adds a definition the default branch has removed.
    READDS_REMOVED_SYMBOL = "readds_removed_symbol"
    # A merged PR mentions that definition.
    SUPERSEDING_PR = "superseding_pr"


@dataclass
class SupersessionSignal:
    """One cheap reading of a branch's history that argues against working on it.

    `holds` separates evidence from context. Re-adding something the default
    branch deleted is evidence the branch is superseded; a rebase over a moved
    base is only what makes that legible, and every long-lived branch has one —
    acting on it alone would fire on the healthy case.
    """

    kind: SupersessionKind = SupersessionKind.READDS_REMOVED_SYMBOL
    detail: str = ""
    holds: bool = True


@dataclass
class SupersessionDomain(Domain):
    """The supersession verdict, cached against the commits it was computed from.

    Written by whichever branch-acting command ran the check first, and read by
    the next one instead of repeating it. The saving that matters is the
    `gh api search/issues` call per re-added symbol: `pr` runs its delegates as
    separate subprocesses, so an in-memory cache never crosses from `pr review`
    to `pr comments` and this file is the only place a verdict can survive.

    Keyed by both SHAs because the verdict is a function of both. `head_sha`
    alone would go stale the moment the default branch moved: a branch that
    re-adds nothing today re-adds something the hour after `main` deletes it,
    with its own HEAD untouched.
    """

    head_sha: str = ""
    base_sha: str = ""
    signals: list[SupersessionSignal] = field(default_factory=list)

    def matches(self, head_sha: str, base_sha: str) -> bool:
        """Whether this cache entry describes the commits being asked about.

        Empty SHAs never match. A verdict computed where one of the two could
        not be resolved records nothing it can later be keyed on, so it is a
        result to use once and not one to reuse.
        """
        return bool(head_sha) and bool(base_sha) and (
            self.head_sha == head_sha and self.base_sha == base_sha
        )
