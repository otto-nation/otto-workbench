"""Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<state_dir()>/pr/<repo-key>-<branch-slug>/state.json``, keyed on the
run's target — see ``pr_target.target_dir``, which owns that path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from enum import Enum, StrEnum
from functools import cache
from pathlib import Path
from typing import get_type_hints

# get_type_hints(CIDomain) resolves its `runs` annotation at runtime, so
# RunState must be bound in this module's namespace; ci_failures keeps its
# own pr_state import under TYPE_CHECKING, which is what keeps this acyclic.
from ci_failures import RunState

from serde import (
    from_dict as _serde_from_dict,
    load_file as _serde_load_file,
    to_dict as _serde_to_dict,
    write_json as _serde_write_json,
)



STATE_FILE = "state.json"
STATE_VERSION = 1


def now_iso() -> str:
    """UTC ISO timestamp for state updates."""
    return datetime.now(timezone.utc).isoformat()


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class PRIdentity:
    """Resolved once, shared by all subcommands."""
    repo: str
    branch: str
    pr_number: int | None
    head_sha: str
    worktree_root: str


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


class PostedAs(Enum):
    REVIEW = "review"
    COMMENT = "comment"


class PostEvent(Enum):
    COMMENT = "comment"
    APPROVE = "approve"
    REQUESTED_CHANGES = "requested_changes"
    DISMISSED = "dismissed"


class ReviewStatus(Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"


class RebaseStatus(Enum):
    COMPLETED = "completed"
    CONFLICTS = "conflicts"
    ABORTED = "aborted"
    ALREADY_LANDED = "already_landed"


@dataclass
class PostTracking:
    """Recorded in post.jsonl alongside each review."""
    posted_as: str
    status: str
    review_id: int = 0
    review_ids: list[int] = field(default_factory=list)
    commit_id: str = ""
    posted_at: str = ""
    inline_count: int = 0
    body_count: int = 0
    skipped_count: int = 0
    submitted: bool = False
    chunk_count: int = 1
    review_sha: str = ""
    head_sha_at_post: str = ""
    sha_drifted: bool = False
    verdict: str = ""

    def __post_init__(self):
        if not self.posted_at:
            self.posted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.review_ids and not self.review_id:
            self.review_id = self.review_ids[0]
        if self.review_id and not self.review_ids:
            self.review_ids = [self.review_id]


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


@dataclass
class PendingComment:
    """A PR comment deferred until a blocking condition clears."""
    body: str = ""
    source: str = ""
    updated_at: str = ""


@dataclass
class PRState:
    """Unified PR state — envelope over domain summaries."""
    identity: PRIdentity
    ci: CIDomain = field(default_factory=CIDomain)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    describe: DescribeSummary = field(default_factory=DescribeSummary)
    fix: FixSummary = field(default_factory=FixSummary)
    supersession: SupersessionDomain = field(default_factory=SupersessionDomain)
    pending_comments: list[PendingComment] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


# ── PR outcome ──────────────────────────────────────────────────────────────


class PRCloseState(Enum):
    """A PR's lifecycle state as ``gh`` reports it, with the field that dates it.

    ``ended_at_field`` is the ``gh pr view --json`` key holding when the PR
    reached this state. OPEN has none — an open PR has not ended — and that is
    what ``is_terminal`` reads, so no caller restates which states retire a
    review cycle.

    OPEN is a member rather than an absence because a sweep has to tell "gh
    said the PR is open" from "gh answered with a state this code does not
    know". The first is a real answer; the second is a renamed or added state,
    which would otherwise read as still-open forever and quietly retire the
    prune.
    """

    OPEN = ("OPEN", None)
    MERGED = ("MERGED", "mergedAt")
    CLOSED = ("CLOSED", "closedAt")

    def __new__(cls, value: str, ended_at_field: str | None) -> PRCloseState:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.ended_at_field = ended_at_field
        return obj

    @property
    def is_terminal(self) -> bool:
        """Whether this state ends the PR, and with it the cycle's artifacts."""
        return self.ended_at_field is not None

    @classmethod
    def parse(cls, raw: str | None) -> PRCloseState | None:
        """The state ``gh`` named, or None for one this code does not know."""
        try:
            return cls(raw)
        except ValueError:
            return None


# What ``gh pr view --json`` has to fetch to build a PRClosure: the state, plus
# the timestamp field every terminal state dates itself with. Derived from the
# enum so a state added there is asked for without also editing the query.
GH_STATE_JSON_FIELDS = ",".join(
    ["state", *(s.ended_at_field for s in PRCloseState if s.is_terminal)]
)


@dataclass(frozen=True)
class PRClosure:
    """How a PR ended, and when.

    Only ever built for a terminal state: "still open" and "we could not ask"
    are both the absence of a closure rather than a member of one, which is what
    lets a sweep decide on the closure's presence alone.

    ``ended_at`` is GitHub's timestamp, not the sweep's clock. It is empty when
    gh has not filled the field in yet — a missing timestamp, not a PR that has
    not ended — so it never stands in for the state.
    """

    state: PRCloseState
    ended_at: str = ""


# The action on the trail's terminal event. `pr_state` owns it because it owns
# the payload's shape; `review_gc` owns the emit.
TERMINAL_SUMMARY_ACTION = "pr_outcome"


def terminal_summary(state: PRState, closure: PRClosure) -> dict:
    """How a review cycle ended, for the trail's terminal event.

    Read straight off the domains rather than recomputed: this is the last
    reading of state that is about to be deleted, not a fresh measurement.

    The closure's ``ended_at`` comes from GitHub rather than from the clock —
    the scheduled maintenance sweep is what usually runs gc, so the event's own
    ``ts`` says when that sweep noticed, up to a cycle after the merge.
    """
    return {
        "outcome": closure.state.value,
        "ended_at": closure.ended_at,
        "cost_usd": state.review.cost_usd,
        "total_tokens": state.review.total_tokens,
        "verdict": state.review.verdict,
        "finding_counts": dict(state.review.finding_counts),
        "rebase_conflicts": state.rebase.conflicts_resolved,
    }


# ── Serialization ───────────────────────────────────────────────────────────


def state_to_dict(state: PRState) -> dict:
    d = _serde_to_dict(state)
    d["_version"] = STATE_VERSION
    return d


def state_from_dict(d: dict) -> PRState:
    # Strict: a field with no dataclass default must be present or serde raises.
    # Tolerance belongs at the file level, where discarding is a real recovery —
    # see load_state.
    return _serde_from_dict(PRState, d)


# ── I/O ─────────────────────────────────────────────────────────────────────


def load_state(target_dir: Path) -> PRState | None:
    """Load unified PR state, or None if there is no usable file.

    A missing file and an unreadable one both come back as None. Nothing stored
    here is authoritative — every field is rebuilt by the command that wrote it —
    so discarding a file that will not parse is always a correct recovery, and
    the next write replaces it. `pr gc` clears one on demand.

    A path outside a git worktree reads as "no state yet" for the same reason:
    the status line runs wherever the user's shell is, and a directory that
    cannot hold state has none.

    No caller needs to tell corrupt from missing; the warning serde emits is
    what supplies the part "no state yet" leaves out.
    """
    return _serde_load_file(PRState, target_dir / STATE_FILE)


def save_state(target_dir: Path, state: PRState) -> None:
    """Save unified PR state, creating directories as needed.

    ``serde.write_json`` owns the atomicity: the status line reads this file
    from whatever shell the user is in, concurrently with the write.
    """
    state.updated_at = datetime.now(timezone.utc).isoformat()
    _serde_write_json(target_dir / STATE_FILE, state_to_dict(state))


# ── Updaters ────────────────────────────────────────────────────────────────


def new_state(
    repo: str,
    branch: str,
    pr_number: int | None,
    head_sha: str,
    worktree_root: str,
) -> PRState:
    """Create a fresh PRState with identity populated."""
    now = datetime.now(timezone.utc).isoformat()
    return PRState(
        identity=PRIdentity(
            repo=repo,
            branch=branch,
            pr_number=pr_number,
            head_sha=head_sha,
            worktree_root=worktree_root,
        ),
        created_at=now,
        updated_at=now,
    )


def update_identity(
    state: PRState,
    head_sha: str,
    pr_number: int | None = None,
    worktree_root: str = "",
) -> None:
    """Refresh identity fields that change across invocations.

    ``worktree_root`` is one of them now that the file is keyed on the target
    rather than stored inside the checkout: consecutive runs against one target
    can come from different worktrees, and consumers read this field to find the
    checkout a fix was committed in.

    Both it and ``head_sha`` are overwritten only when the incoming value is
    non-empty. A run from a bare repo has neither a worktree to name nor a
    checked-out HEAD to read, and a run with nothing to say must not erase what
    an earlier run knew: the fix summary falls back to ``identity.head_sha`` when
    it has no commit of its own, and would otherwise post an empty SHA.
    """
    if head_sha:
        state.identity.head_sha = head_sha
    if pr_number is not None:
        state.identity.pr_number = pr_number
    if worktree_root:
        state.identity.worktree_root = worktree_root


@cache
def _domains() -> dict[str, type[Domain]]:
    """Domain field name → type, derived from PRState's own annotations.

    Cached because the answer cannot change after import: PRState's fields are
    fixed at class creation.
    """
    return {
        name: hint
        for name, hint in get_type_hints(PRState).items()
        if isinstance(hint, type) and issubclass(hint, Domain)
    }


@cache
def _domain_names() -> dict[type[Domain], str]:
    """The inverse of `_domains`, for routing an update by its type.

    A domain class held by two PRState fields has no single home to write to,
    so it fails here at first use rather than silently writing to whichever
    field happened to be annotated last.
    """
    names: dict[type[Domain], str] = {}
    for name, cls in _domains().items():
        if cls in names:
            raise TypeError(
                f"{cls.__name__} is the type of both PRState.{names[cls]} and "
                f"PRState.{name}; a domain needs exactly one field to own it"
            )
        names[cls] = name
    return names


def apply(state: PRState, domain: Domain) -> None:
    """Write a domain update into the field that owns it, honoring its merge policy."""
    name = _domain_names().get(type(domain))
    if name is None:
        raise ValueError(f"{type(domain).__name__} is not a PRState domain")
    setattr(state, name, domain.merge_into(getattr(state, name)))


def add_pending_comment(state: PRState, comment: PendingComment) -> None:
    """Add a deferred comment, replacing any existing entry with the same source."""
    state.pending_comments = [c for c in state.pending_comments if c.source != comment.source]
    state.pending_comments.append(comment)


def pop_pending_comments(
    state: PRState, source: str | None = None,
) -> list[PendingComment]:
    """Remove and return pending comments, optionally filtered by source."""
    if source is None:
        comments = state.pending_comments
        state.pending_comments = []
        return comments
    kept, popped = [], []
    for c in state.pending_comments:
        (popped if c.source == source else kept).append(c)
    state.pending_comments = kept
    return popped


# ── Convenience ────────────────────────────────────────────────────────────


def load_or_init(
    *,
    target_dir: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
    worktree_root: str = "",
) -> PRState:
    """Load existing state or create a fresh one, updating identity.

    ``target_dir`` is where the file lives; ``worktree_root`` is the checkout the
    write ran from, recorded in identity for consumers that want to find it.
    """
    state = load_state(target_dir)
    if state is not None:
        update_identity(state, head_sha, pr_number, worktree_root)
        return state
    return new_state(
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
        worktree_root=worktree_root,
    )


def apply_state_update(
    *,
    target_dir: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
    worktree_root: str = "",
    domain: str,
    data: dict,
) -> None:
    """Load-or-init state, apply a domain update from a dict, and save."""
    domain_cls = _domains().get(domain)
    if domain_cls is None:
        raise ValueError(f"Unknown state domain: {domain!r}")
    state = load_or_init(
        target_dir=target_dir,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
        worktree_root=worktree_root,
    )
    apply(state, _serde_from_dict(domain_cls, data))
    save_state(target_dir, state)
