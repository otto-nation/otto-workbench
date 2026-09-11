"""CI's half of a fix pass: the failures, the commit, and the state write.

`fix.engine` runs the pass; this says what CI hands it. The other side of that
boundary is `fix.types.FixItem`, and the translation into one happens here so
that what the engine sees is the same for every domain and what CI reasons
about stays `pr.ci_failures`' own types.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

from agent import retry as agent_retry
from fix import engine as fix_engine
from fix import types as fix_types
from pr import ci_failures as ci
from pr import ci_report
from pr import state as pr_state
from core.phases import Phase
from pr.fix import FixOutcome, FixRecord, ItemOutcome

# Nothing the agent could edit would clear either of these.
_SKIP_KINDS = frozenset((ci.FailureKind.INFRA, ci.FailureKind.FLAKY))


@dataclass(frozen=True)
class CIFailure:
    """One failed item, together with the job it failed in.

    A `FailureItem` says what went wrong and a `FailureGroup` says where; the
    fix pass needs both on every failure it reports, and the grouping the run
    arrived in is not what it iterates over.
    """

    item: ci.FailureItem
    group: ci.FailureGroup
    outcome: ci.Outcome

    @property
    def id(self) -> str:
        return self.item.id


def flatten(
    failures: dict[str, ci.FailureGroup], progression: dict[str, ci.Outcome],
) -> list[CIFailure]:
    """Pair each item in a run with its group and its progression outcome."""
    return [
        CIFailure(item=item, group=group, outcome=progression.get(item.id, ci.Outcome.NEW))
        for group in failures.values()
        for item in group.items
    ]


def _failure_body(failure: CIFailure) -> str:
    """The diagnosis this domain puts under one failure's heading.

    The heading, the id marker and the outcome boxes belong to `fix_tracking`,
    which is also what reads them back, so the two halves of the format cannot
    drift apart.
    """
    item = failure.item
    body = f"**Kind:** {failure.group.kind.value}\n"
    if item.headline:
        body += f"**Error:** {item.headline}\n"
    if item.annotation and item.annotation != item.headline:
        body += f"**Details:** {item.annotation}\n"
    if item.context:
        body += f"**Context:** {item.context}\n"
    if failure.outcome is not ci.Outcome.NEW:
        body += f"**Progression:** {failure.outcome.value}\n"
    return body


class CIFixAdapter(fix_engine.FixAdapter):
    """CI's half of a fix pass: the failures, the commit, and the state write.

    Infra and flaky failures never reach the agent — nothing it could edit would
    clear them — so they are held back here and recorded as SKIPPED rather than
    handed over and declined. `fixable` is what the pass is actually asked
    about, and an empty one is a run with nothing to do.
    """

    phase = Phase.CI_FIX
    action = "fixing CI failures"
    item_noun = "failure"
    # The shared hint is written for review findings. This one names the same
    # mechanism in CI's terms; both point the agent at the three boxes below.
    fix_hint = agent_retry.CI_FIX_RETRY_HINT

    def __init__(self, report: ci_report.CIReport, ctx, state) -> None:
        self.workdir = ctx.require_worktree()
        # Under the run's own target directory, not inside the worktree. The
        # tracking file and the session log are this pass's bookkeeping, not
        # the repo's, and a target repo whose `.gitignore` says nothing about
        # `ignore/` had them swept into the commit the pass then pushed.
        # `ctx.target_dir` already keys per repo and branch, which is the same
        # identity `state.json` is filed under.
        self.artifacts = ctx.target_dir / "ci-failures"
        self.title = f"CI Fix Tracking — Run #{report.run_number}"
        self.branch = ctx.branch
        self.repo = ctx.repo
        self.pr = str(ctx.pr_number) if ctx.pr_number else ""
        self.ctx = ctx
        self.state = state
        failures = flatten(report.failures, report.progression)
        self.skipped = [f for f in failures if f.group.kind in _SKIP_KINDS]
        self.fixable = [f for f in failures if f.group.kind not in _SKIP_KINDS]

    def items(self) -> list[fix_types.FixItem]:
        return [
            fix_types.FixItem(
                id=f.item.id, file=f.item.file or "", line=f.item.line or 0,
                label=f.group.job, body=_failure_body(f),
            )
            for f in self.fixable
        ]

    def template_vars(self) -> dict[str, str]:
        """Nothing — `fix-ci.md` asks for no substitution the engine withholds."""
        return {}

    def landing(
        self, outcomes: list[ItemOutcome], changed: set[str] | None,
    ) -> fix_engine.LandSpec:
        """Commit what the agent touched, and only that.

        Not the whole tree: the pass edits a branch worktree it does not own,
        and anything else dirty there — an unrelated edit in progress, a build
        artifact — would be swept into a commit the pass then offers to push.

        A snapshot that failed arrives as None and lands an empty scope, which
        commits nothing and leaves the fixes in the worktree. That is the right
        answer for a pass that cannot say which files are its own.

        The push is gated, so a run without `--post` commits the fixes and
        drafts the push instead of making it.
        """
        fixed = sum(1 for o in outcomes if o.outcome.counts_as_fixed)
        msg = "fix: address CI failures"
        if fixed:
            msg += f"\n\n{fixed} fixed, {len(outcomes) - fixed} skipped"
        return fix_engine.LandSpec(
            message=msg, regen="chore: regenerate after CI fixes",
            paths=changed if changed else set(),
        )

    def record(self, run: fix_engine.FixRun) -> None:
        """Fold the pass into `state.ci.fix` and save it.

        The held-back failures go in alongside the agent's answers: a run that
        left three infra failures standing has accounted for them, and a record
        holding only what the agent saw reads as if they were never seen.
        """
        skipped = [
            ItemOutcome(
                id=f.item.id, outcome=FixOutcome.SKIPPED, summary=f.group.job,
                reason=f"{f.group.kind.value} failure — no code change would clear it",
                file=f.item.file or "", line=f.item.line or 0,
                read_sha=run.head_before,
            )
            for f in self.skipped
        ]
        landed = run.landed
        fresh = FixRecord(
            items=run.outcomes + skipped,
            commit_sha=landed.sha if landed else "",
            commit_status=landed.status if landed else None,
            head_sha=(landed.sha if landed else "") or run.head_before,
            updated_at=pr_state.now_iso(),
        )
        self.state.ci.fix = fresh.merge_into(self.state.ci.fix)
        pr_state.save_state(self.ctx.target_dir, self.state)
