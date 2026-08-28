"""The comment fix pass's domain, and the closeout it owes the PR.

What the pass did about each thread is a :class:`~pr_fix.FixRecord` on this
domain, in the :class:`~pr_fix.ItemOutcome` vocabulary all three fix passes
write — so a consumer asking "what became of this item" reads one shape
whichever pass produced it. What is left here is what only this pass has: a
reply queue, a summary comment, a PR description draft and a deferred-issue
trio, none of which the other domains have anything to say about.

``reviewers`` is here for the same reason. Which login opened a thread is the
item as GitHub handed it over, not a fact about what the pass did with it, so
it stays on the domain rather than widening the record every pass shares — the
line :class:`~pr_fix.ItemOutcome` draws for a CI job name and a finding's
severity too. It is keyed by outcome id, so the two accumulate together.

The module is above ``pr_domains`` rather than inside it: ``FixSummary`` is a
:class:`~pr_domains.Domain` and needs the base class, while the generic record
that base class carries has to be declared below it. Splitting on that line
keeps the imports one-way.

A state file written before the fold records its outcomes as ``threads``, in a
vocabulary of its own, beside a top-level ``commit_sha``/``commit_status``/
``head_sha``. :meth:`FixSummary._from_raw` reads that shape into the record, so
a review cycle in flight keeps the outcomes, the reply queue and the deferred
issue it had accumulated rather than resuming from an empty one.
"""

# doc-group: pr-state

from dataclasses import dataclass, field, replace as dataclass_replace

from pr_domains import Domain, Readiness
from pr_fix import FixOutcome
from serde import from_dict as _serde_from_dict


# The one command that drains the queue. Spelled once so the status line, the
# merge-readiness blocker, and the docs cannot drift from each other.
CLOSEOUT_COMMAND = "pr comments --finish --post"

# The three reply buckets --finish drains (`_post_pending_fix_replies` in
# review-threads). Threads with any other outcome owe no reply, so they must
# not inflate the count the operator is quoted.
_REPLY_OUTCOMES = frozenset({
    FixOutcome.FIXED, FixOutcome.ALREADY_ADDRESSED, FixOutcome.DISMISSED,
})

# The record fields a pre-fold state file wrote at the top level of this domain.
_LEGACY_RECORD_KEYS = ("commit_sha", "commit_status", "head_sha")

# How the status line spells each verdict, in the order it prints them. Every
# `FixOutcome` member has an entry — a verdict with none is silently dropped
# from the count, so the domain would report fewer threads than it holds.
_STATUS_LABELS: dict[FixOutcome, str] = {
    FixOutcome.FIXED: "**{n} fixed**",
    FixOutcome.DEFERRED: "{n} deferred",
    FixOutcome.NEEDS_HUMAN: "{n} need discussion",
    FixOutcome.DECLINED: "{n} declined",
    FixOutcome.DISMISSED: "{n} dismissed",
    FixOutcome.ALREADY_ADDRESSED: "{n} already addressed",
    FixOutcome.SETTLED_ELSEWHERE: "{n} settled elsewhere",
    FixOutcome.SKIPPED: "{n} skipped",
}


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

    # The login that opened each outcome, keyed by the outcome's id. Kept beside
    # the record rather than on it: an entry is written whenever GitHub named a
    # reviewer, and one it did not name has no key here rather than an empty
    # one, so a lookup misses instead of asserting an anonymous reviewer.
    reviewers: dict[str, str] = field(default_factory=dict)
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

    @classmethod
    def _from_raw(cls, raw) -> "FixSummary":
        """Rebuild the domain, folding a pre-fold snapshot into the record.

        A state file written before the fold holds this pass's outcomes as
        ``threads`` — `thread_id` or `id`, an `action` where the record says
        `outcome`, and a `reviewer` the record does not carry — beside a
        top-level `commit_sha`, `commit_status` and `head_sha`. Reading them
        here is what lets a review cycle already in flight keep the rounds it
        accumulated: dropping them would resume from an empty record and
        re-report a cycle's worth of settled threads as never having been
        looked at.

        The legacy `commit_status` is a plain string and an unrun pass wrote
        `""`, which is no `CommitStatus` member. Left in place it would raise
        out of `serde.load_file` and discard the whole state file — every
        domain's state lost to one empty string — so a falsy one is dropped and
        the record's own `None` stands for "no pass has run".

        What the stored record already says wins over the legacy fields. Only a
        file this hook has never seen carries both, but a merge that preferred
        the older half would be a downgrade rather than a migration.
        """
        if isinstance(raw, cls):
            return raw
        data = dict(raw or {})
        if "threads" not in data and not any(k in data for k in _LEGACY_RECORD_KEYS):
            return _serde_from_dict(cls, data)

        record = dict(data.pop("fix", None) or {})
        reviewers = dict(data.pop("reviewers", None) or {})
        items = []
        for entry in data.pop("threads", None) or []:
            thread = dict(entry or {})
            if "thread_id" in thread and "id" not in thread:
                thread["id"] = thread.pop("thread_id")
            reviewer = thread.pop("reviewer", "")
            if thread.get("id") and reviewer:
                reviewers.setdefault(thread["id"], reviewer)
            if "action" in thread and "outcome" not in thread:
                thread["outcome"] = thread.pop("action")
            items.append(thread)

        if items and not record.get("items"):
            record["items"] = items
        for key in _LEGACY_RECORD_KEYS:
            legacy = data.pop(key, "")
            if legacy and not record.get(key):
                record[key] = legacy
        if not record.get("updated_at"):
            record["updated_at"] = data.get("updated_at", "")
        data["fix"] = record
        data["reviewers"] = reviewers
        return _serde_from_dict(cls, data)

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """What `_from_raw` accepts, for the schema `pr --tool-schema` publishes.

        Reachable from PRState, so this is a live contract rather than a latent
        one: without the pre-fold form the published schema calls a state file
        invalid that `_from_raw` reads without complaint.
        """
        properties = object_schema["properties"]
        record = properties["fix"]["properties"]
        outcome = record["items"]["items"]
        outcome_properties = outcome["properties"]
        return {
            **object_schema,
            "properties": {
                **properties,
                "threads": {"type": "array", "items": {
                    **outcome,
                    "properties": {
                        **outcome_properties,
                        "thread_id": outcome_properties["id"],
                        "reviewer": {"type": "string"},
                        "action": outcome_properties["outcome"],
                    },
                }},
                "commit_sha": record["commit_sha"],
                # A plain string, not the enum the record holds: an unrun pass
                # wrote "" here and `_from_raw` drops it.
                "commit_status": {"type": "string"},
                "head_sha": record["head_sha"],
            },
        }

    def closeout_debt(self) -> CloseoutDebt:
        """The undelivered closeout this fix pass recorded.

        Reads only what the fix pass already recorded — no fetch, no new state.
        """
        return CloseoutDebt(
            summary=self.summary_deferred,
            replies=self.replies_pending,
            deferred_issue=self.deferred_issue_pending,
            reply_count=sum(
                1 for o in self.fix.items if o.outcome in _REPLY_OUTCOMES
            ),
            description=self.pr_body_pending,
        )

    def render_status(self) -> list[str]:
        if not self.updated_at:
            return ["**Fix**: not run yet"]
        by_outcome: dict[str, int] = {}
        for o in self.fix.items:
            by_outcome[o.outcome] = by_outcome.get(o.outcome, 0) + 1
        parts = [tmpl.format(n=by_outcome[outcome])
                 for outcome, tmpl in _STATUS_LABELS.items() if by_outcome.get(outcome, 0)]
        summary = " · ".join(parts) if parts else "no threads"
        lines = [f"**Fix**: {summary}"]
        if self.fix.commit_sha:
            lines[0] += f" (commit: {self.fix.commit_sha}, {self.fix.commit_status})"
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

        Outcomes accumulate in the record `Domain.merge_into` folds — keyed by
        id, so a later pass supersedes an earlier outcome for the same thread
        but never drops threads it did not touch. The reviewers travel with
        them: a round that says nothing about a thread must not lose the login
        an earlier round recorded for it, or the summary republishes that row
        with no reviewer against it.

        Four more things are cycle-scoped rather than per-round, for the same
        reason: a review cycle spans several rounds, and a round that did not
        touch one of them says nothing about it rather than clearing it.

        The deferred tracking issue is one of them: it is created once and
        updated on later rounds.  A fix pass builds its FixSummary before
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
        return dataclass_replace(
            super().merge_into(prior),
            reviewers={**prior.reviewers, **self.reviewers},
            deferred_issue_id=self.deferred_issue_id or prior.deferred_issue_id,
            deferred_issue_url=self.deferred_issue_url or prior.deferred_issue_url,
            deferred_issue_pending=(
                self.deferred_issue_pending or prior.deferred_issue_pending
            ),
            summary_url=self.summary_url or prior.summary_url,
            pr_body_pending=self.pr_body_pending or prior.pr_body_pending,
        )
