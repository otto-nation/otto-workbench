"""The comment fix pass's record, and the vocabulary it is written in.

This is the one fix pass that already records per-item outcomes as state, and it
records them in its own terms: a thread, a reviewer, an action from a set only
reviewer comments have. ``pr_fix`` holds the generic shape all three passes are
converging on; everything here is the comment-shaped instance of it, kept
whole because a state file in flight carries a cycle's accumulated thread
outcomes — a deferred issue url, an undelivered summary — that renaming the
field would silently drop.

The module is above ``pr_domains`` rather than inside it: ``FixSummary`` is a
:class:`~pr_domains.Domain` and needs the base class, while the generic record
that base class carries has to be declared below it. Splitting on that line
keeps the imports one-way and puts the transitional half in a file of its own,
which is what the shared fix engine deletes when the comment pass starts writing
``CommentsSummary.fix`` instead.
"""

# doc-group: pr-state

from dataclasses import dataclass, field, replace as dataclass_replace
from enum import StrEnum

from pr_domains import Domain, Readiness
from serde import from_dict as _serde_from_dict


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

        `serde` hands a `null` here too rather than dropping the field, so a
        list holding one keeps the outcomes beside it. An entry recording
        nothing reconstructs as the default — DEFERRED, which reads as work
        still owed rather than as a thread this pass claimed.
        """
        if isinstance(raw, cls):
            return raw
        data = dict(raw or {})
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


# The one command that drains the queue. Spelled once so the status line, the
# merge-readiness blocker, and the docs cannot drift from each other.
CLOSEOUT_COMMAND = "pr comments --finish --post"

# The three reply buckets --finish drains (`_post_pending_fix_replies` in
# review-threads). Threads with any other outcome owe no reply, so they must
# not inflate the count the operator is quoted.
_REPLY_ACTIONS = frozenset({
    ThreadAction.FIXED, ThreadAction.ALREADY_ADDRESSED, ThreadAction.DISMISSED,
})


@dataclass(frozen=True)
class CloseoutDebt:
    """What a fix pass rendered but never delivered to the PR.

    The queue's only symptom is the *absence* of comments on the PR, which is
    indistinguishable from a run that had nothing to say — so every surface
    that reports on a fix pass has to say the debt out loud.
    """

    summary: bool = False
    replies: bool = False
    # A tracking issue the fix pass owed the deferred threads and never filed.
    # Its absence is quieter still than the other two: the summary renders a
    # bare "Deferred" with no link, which reads exactly like a deferral nobody
    # asked to track.
    deferred_issue: bool = False
    # Recounted from the recorded outcomes rather than read off a stored number,
    # which makes it advisory: a queue whose outcomes were pruned still owes its
    # replies via `replies` while this reads 0. `replies` alone decides whether
    # anything is owed; the count only sharpens the wording.
    reply_count: int = 0
    # A PR description the fix pass rewrote but could not send. It is a GitHub
    # write like any other, so it is owed here rather than quietly sitting in
    # the worktree until someone notices the description never changed.
    description: bool = False

    @property
    def owed(self) -> bool:
        return self.summary or self.replies or self.deferred_issue or self.description

    def describe(self) -> str:
        """Name what is owed — 'summary', '15 replies', 'deferred tracking issue', 'PR description', or a mix."""
        parts = []
        if self.summary:
            parts.append("summary")
        if self.replies:
            # An uncounted queue reads as replies owed, never as zero of them.
            noun = "reply" if self.reply_count == 1 else "replies"
            parts.append(f"{self.reply_count} {noun}" if self.reply_count else "replies")
        if self.deferred_issue:
            parts.append("deferred tracking issue")
        if self.description:
            parts.append("PR description")
        return " + ".join(parts)

    @property
    def command(self) -> str:
        """The command that actually drains this debt.

        The bare CLOSEOUT_COMMAND drains a rendered-but-unsent summary or reply
        queue, but a deferred tracking issue is only ever filed for threads named
        by `--track`/`--track-all` — `--track` defaults to selecting nothing, so
        the bare command would hit that early return and leave the issue unfiled
        forever. Quote the flag that actually files it whenever that debt is owed.
        """
        if self.deferred_issue:
            return f"{CLOSEOUT_COMMAND} --track-all"
        return CLOSEOUT_COMMAND


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
    # until one exists, so `closeout_debt` counts it. A draft run
    # is not this: the publishing gate declining the write owes nothing.
    deferred_issue_pending: bool = False
    has_comment_items: bool = False

    def closeout_debt(self) -> CloseoutDebt:
        """The undelivered closeout this fix pass recorded.

        Reads only what the fix pass already recorded — no fetch, no new state.
        """
        return CloseoutDebt(
            summary=self.summary_deferred,
            replies=self.replies_pending,
            deferred_issue=self.deferred_issue_pending,
            reply_count=sum(1 for t in self.threads if t.action in _REPLY_ACTIONS),
            description=self.pr_body_pending,
        )

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Fix**: not run yet"]
        by_action: dict[str, int] = {}
        for t in self.threads:
            by_action[t.action] = by_action.get(t.action, 0) + 1
        labels = [
            (ThreadAction.FIXED, "**{n} fixed**"),
            (ThreadAction.DEFERRED, "{n} deferred"),
            (ThreadAction.NEEDS_HUMAN, "{n} need discussion"),
            (ThreadAction.DISMISSED, "{n} dismissed"),
            (ThreadAction.ALREADY_ADDRESSED, "{n} already addressed"),
        ]
        parts = [tmpl.format(n=by_action[action])
                 for action, tmpl in labels if by_action.get(action, 0)]
        summary = " · ".join(parts) if parts else "no threads"
        lines = [f"**Fix**: {summary}"]
        if self.commit_sha:
            lines[0] += f" (commit: {self.commit_sha}, {self.commit_status})"
        debt = self.closeout_debt()
        if debt.owed:
            lines.append(f"  ⚠ closeout owed: {debt.describe()} — run: {debt.command}")
        if self.deferred_issue_id:
            lines.append(f"  tracked in {self.deferred_issue_id}")
        return lines

    def readiness(self) -> Readiness:
        # A summary or reply queue the fix pass rendered and never sent. The PR
        # reads as un-reviewed until --finish drains it, so merging leaves
        # reviewers with no record of the work.
        debt = self.closeout_debt()
        if not debt.owed:
            return Readiness()
        return Readiness(blockers=(f"closeout not delivered (run: {debt.command})",))

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
            super().merge_into(prior),
            threads=list(merged.values()) + no_id,
            deferred_issue_id=self.deferred_issue_id or prior.deferred_issue_id,
            deferred_issue_url=self.deferred_issue_url or prior.deferred_issue_url,
            deferred_issue_pending=(
                self.deferred_issue_pending or prior.deferred_issue_pending
            ),
            summary_url=self.summary_url or prior.summary_url,
            pr_body_pending=self.pr_body_pending or prior.pr_body_pending,
        )
