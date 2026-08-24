"""The domains a PR's state is made of.

Each ``pr`` subcommand owns one domain and writes it as a unit. A domain is a
dataclass subclassing :class:`Domain`; subclassing is the registration, and
``pr_state`` derives its registry from ``PRState``'s own annotations, so a new
domain is added here and named there and nowhere else.

This module holds the domain types and the vocabulary they are written in.
``pr_state`` holds the envelope over them, the registry and the state file I/O,
and imports this module — never the other way round.
"""

# doc-group: pr-state

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as dataclass_replace
from enum import Enum, StrEnum

# get_type_hints(CIDomain) resolves its `runs` annotation against the namespace
# of the module CIDomain is defined in, so RunState must be bound here;
# ci_failures keeps its own pr_state import under TYPE_CHECKING, which is what
# keeps this acyclic.
from ci_failures import RunState

from serde import from_dict as _serde_from_dict


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
    """

    updated_at: str = ""

    def merge_into(self, prior: "Domain") -> "Domain":
        """Combine this update with what is already stored.

        A write replaces what came before, which is what every domain wants
        except the ones that accumulate across rounds. Those override this and
        fold ``prior`` in themselves, so the policy lives with the shape it
        applies to rather than in the code that happens to call the write.
        """
        return self


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


@dataclass
class CommentsSummary(Domain):
    """Snapshot written by ``pr comments``."""
    total_threads: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    blocking_reviewers: list[str] = field(default_factory=list)
    has_approvals: bool = False
    seen_issue_comment_ids: list[int] = field(default_factory=list)
    seen_review_body_comment_ids: list[int] = field(default_factory=list)


@dataclass
class TriageSummary(Domain):
    """Snapshot written by ``pr triage``."""
    total: int = 0
    actionable: int = 0
    valid: int = 0
    questions: int = 0
    comment_items_total: int = 0
    comment_items_actionable: int = 0


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

    A `StrEnum` for the same reason `CommitStatus` is one: these strings are
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


class ThreadAction(StrEnum):
    FIXED = "fixed"
    DEFERRED = "deferred"
    NEEDS_HUMAN = "needs_human"
    DISMISSED = "dismissed"
    ALREADY_ADDRESSED = "already_addressed"


@dataclass
class ThreadOutcome:
    """Per-thread outcome from a comment processing pass."""
    id: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""
    action: ThreadAction = ThreadAction.FIXED
    reason: str = ""
    # The commit that landed this thread's fix. Per-outcome rather than
    # per-pass: FixSummary accumulates outcomes across rounds, so one envelope
    # SHA would relabel every earlier round's work with the latest round's
    # commit — or, when the latest round commits nothing, with none at all.
    commit_sha: str = ""
    # The tree `line` was read in, carried so a later round's replies can tell
    # whether the anchor still points at the code the reviewer meant. Empty on
    # an outcome written before this was recorded, which reads as "cannot
    # anchor" rather than as "anchor is current".
    read_sha: str = ""

    @classmethod
    def from_entry(
        cls, entry, action: ThreadAction, reason_key: str = "reason",
    ) -> "ThreadOutcome":
        if hasattr(entry, "id"):
            return cls(
                id=entry.id,
                file=entry.file,
                line=entry.line,
                reviewer=entry.reviewer,
                summary=entry.summary,
                action=action,
                reason=getattr(entry, reason_key, ""),
                commit_sha=getattr(entry, "commit_sha", ""),
                read_sha=getattr(entry, "read_sha", ""),
            )
        return cls(
            id=entry.get("id", entry.get("thread_id", "")),
            file=entry.get("file", ""),
            line=entry.get("line", 0),
            reviewer=entry.get("reviewer", ""),
            summary=entry.get("summary", ""),
            action=action,
            reason=entry.get(reason_key, ""),
            commit_sha=entry.get("commit_sha", ""),
            read_sha=entry.get("read_sha", ""),
        )

    @classmethod
    def _from_raw(cls, raw) -> "ThreadOutcome":
        """Rebuild an outcome from an instance or a dict, renaming a legacy key.

        `serde` hands the whole field over here rather than assuming the
        current key names: an outcome written before the field was renamed
        carries `thread_id` where the dataclass now declares `id`. Copying
        rather than popping leaves the caller's dict alone — `apply_state_update`
        is handed a payload it does not expect this function to rewrite.
        """
        if isinstance(raw, cls):
            return raw
        data = dict(raw)
        if "thread_id" in data and "id" not in data:
            data["id"] = data.pop("thread_id")
        return _serde_from_dict(cls, data)

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """What `_from_raw` accepts, for the schema `pr --tool-schema` publishes.

        Reachable from PRState through `FixSummary.threads`, so this is a live
        contract, not a latent one: without the legacy alias the published
        schema calls a document invalid that `_from_raw` reads without
        complaint. Same key, same type — `id` under the name it used to have.
        """
        properties = object_schema["properties"]
        return {
            **object_schema,
            "properties": {**properties, "thread_id": properties["id"]},
        }


class CommitStatus(StrEnum):
    """How the fix pass left the commit — the state everything downstream reads.

    A `StrEnum`, so the persisted values and the JSON payload are the same
    strings they have always been: a state file written before this existed
    still loads, and its plain strings still compare equal to these members.
    The enum is for the code: two of these values are easily confused for one
    another, which is the argument for naming them in one place.
    """

    # Committed and on the remote.
    PUSHED = "pushed"
    # Nothing to commit: the fix pass changed no files.
    NO_CHANGES = "no_changes"
    # A commit was attempted and refused — a hook, or a dirty tree left behind.
    COMMIT_FAILED = "commit_failed"
    # Committed locally; the push was attempted and failed.
    PUSH_FAILED = "push_failed"
    # Committed locally; the push was withheld.
    PUSH_HELD = "push_held"
    # Render-time only, never persisted: HEAD has moved past the snapshot, but
    # the commit that moved it is not one a reviewer can open, so the summary
    # says the work was handled without naming a SHA for it.
    RECONCILED = "reconciled"


@dataclass
class FixSummary(Domain):
    """Snapshot written by comment fix pass."""
    threads: list[ThreadOutcome] = field(default_factory=list)
    commit_sha: str = ""
    # Loaded from JSON as a plain string, written as one, and compared against
    # `CommitStatus` members — which are strings, so both directions work.
    commit_status: str = ""
    # The HEAD this snapshot describes. --finish compares it against current
    # HEAD: outcomes recorded against a commit that is no longer checked out
    # describe work that may since have been done, undone, or superseded by
    # hand, and must be reconciled before anything is published.
    head_sha: str = ""
    replies_posted: int = 0
    # The fix pass produced per-thread replies but did not deliver them — the
    # push failed, or the run was a draft. --finish drains the queue. Covers
    # the already-addressed and dismissed replies too, not just the fixed ones:
    # those are sent during triage, so a pass with nothing fixable still owes
    # them and has no fixed entry to carry them back.
    replies_pending: bool = False
    # The fix pass drafted a new PR description but the gate was shut, so the
    # draft is sitting in the worktree waiting for --finish --post. Cycle-scoped
    # in merge_into for the same reason the summary is: a later round that did
    # not touch the description says nothing about it rather than clearing it,
    # and clearing it would strand the draft with nothing left to deliver it.
    pr_body_pending: bool = False
    summary_url: str = ""
    summary_deferred: bool = False
    deferred_issue_id: str = ""
    deferred_issue_url: str = ""
    # A tracking issue was owed for the threads filed this cycle and did not
    # get created — no tracker configured, a provider that cannot create
    # issues, or a creation that failed. The deferred comments have no home
    # until one exists, so `pr_comments.closeout_debt` counts it. A draft run
    # is not this: the publishing gate declining the write owes nothing.
    deferred_issue_pending: bool = False
    has_comment_items: bool = False

    def merge_into(self, prior: "FixSummary") -> "FixSummary":
        """Merge this fix pass into the accumulated summary.

        Four things are cycle-scoped rather than per-round, for the same reason:
        a review cycle spans several rounds, and a round that did not touch one
        of them says nothing about it rather than clearing it.

        Thread outcomes accumulate across rounds, keyed by thread id — a later
        pass supersedes an earlier outcome for the same thread, but never drops
        threads it did not touch.  A review cycle spans several rounds and the
        summary comment must account for all of them, not just the most recent
        pass.

        The deferred tracking issue is likewise cycle-scoped: it is created once
        and updated on later rounds.  A fix pass builds its FixSummary before
        knowing about it, so an empty id/url means "not set this round", not
        "cleared" — dropping it would make the next deferred round open a
        duplicate issue.  A pending one is owed for the same span: only the
        --finish phase that files the issue can settle the debt, so a fix pass
        that says nothing about it must not clear it either.

        The summary comment is cycle-scoped for the same reason.  A round that
        posts nothing — no fixables, nothing dismissed, no discussion pending —
        carries an empty summary_url meaning "not posted this round", so
        overwriting with it would leave state claiming a summary that is live on
        the PR was never posted, and summary_deferred false leaves --finish with
        nothing to re-render.  A round that does post replaces the url, which is
        not always the same comment: a summary a reviewer has answered below is
        reposted rather than edited, so the url names the live summary rather
        than the first one the cycle wrote.

        An undelivered PR description is cycle-scoped too.  The draft lives in
        the worktree across rounds, so a later round that rewrote nothing
        carries pr_body_pending false meaning "not drafted this round" — letting
        that overwrite a true would leave a draft on disk that --finish no
        longer knows to send.  --finish clears the flag once the write lands.

        Every other field is per-round and comes from this pass.
        """
        merged = {t.id: t for t in prior.threads if t.id}
        no_id: list[ThreadOutcome] = [t for t in prior.threads if not t.id]
        for outcome in self.threads:
            if outcome.id:
                merged[outcome.id] = outcome
            else:
                # Entries without an id cannot be de-duplicated; append rather than
                # colliding every one onto the "" key and losing all but the last.
                # ceiling: this list only grows across rounds. No-id outcomes are
                # rare and a cycle's rounds are bounded, so the growth is bounded in
                # practice — de-dup on content if a cycle ever accumulates enough to
                # bloat the state file or the summary comment.
                no_id.append(outcome)
        return dataclass_replace(
            self,
            threads=list(merged.values()) + no_id,
            deferred_issue_id=self.deferred_issue_id or prior.deferred_issue_id,
            deferred_issue_url=self.deferred_issue_url or prior.deferred_issue_url,
            deferred_issue_pending=(
                self.deferred_issue_pending or prior.deferred_issue_pending
            ),
            summary_url=self.summary_url or prior.summary_url,
            pr_body_pending=self.pr_body_pending or prior.pr_body_pending,
        )
