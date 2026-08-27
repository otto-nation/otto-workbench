"""Fix pass for claude-review.

Runs after a review is written and `--fix` is set. `fix_engine` owns the
pipeline — the batching, the agent, the retry, the commit — and what stays here
is the three things only a review can answer: which findings are still open,
which files the pass is allowed to commit, and how the review document reads
once the agent has answered.

What the agent changed is a snapshot difference: the worktree's dirty set is
recorded before the agent runs and again after, and only the paths that appear
in the second and not the first are attributed to it. Without that first
snapshot the pass cannot tell its own work from whatever was already sitting in
the worktree, and it both commits and takes credit for the difference.

A snapshot git could not take stops the pass rather than reading as an empty
one. Everything outside the difference goes uncommitted, so an unreadable
worktree spelled the same way as an unchanged one is how a pass reports success
having left the agent's fixes behind.

The agent answers on a tracking file, not on the review document. That document
is the deliverable — a reviewer reads it and a re-review reconciles against it —
and letting the agent edit it in place made the pass's evidence about itself the
same text it was editing: a box nobody ticked read as a skip, an annotation
phrased loosely read as no annotation at all, and the pass had to guess which
findings its own agent had touched. `record` re-renders the document from the
outcomes instead, so what it says is what the pass decided.

The commit always happens; the push waits for `--post`. `land` owns both, and
the split is its: a local commit asserts nothing to anybody, while a push puts
the pass's work on a branch somebody else is reading.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.
"""

# doc-group: findings

from __future__ import annotations

import sys
from pathlib import Path

import fix_engine
import fix_types
import git_client
import log
import proc
from agent_types import Phase
from pr_fix import FixOutcome, ItemOutcome
from review_paths import phase_log_path
from review_document import FINDING_ID_RE, ReviewDocument
from review_findings import match_skip
from review_retry import _has_output
from review_types import Finding, ReviewJob, severity_by_key
from trail import Trail

# The two outcomes that leave a finding open. A deferral is a finding the agent
# never reached and a `needs a person` is one it read and handed on, and the
# review document says the same thing about both: still unchecked, still there
# for the next round.
_STILL_OPEN = (FixOutcome.DEFERRED, FixOutcome.NEEDS_HUMAN)


def _changed_source_files(wt_path: str) -> set[str] | None:
    """The changed files (staged, unstaged, and untracked), or None when git
    could not list them.

    Called on both sides of the fix agent's run. `--exclude-standard` keeps
    gitignored paths out of either snapshot, so they cannot reach the
    difference and cannot be staged from it.

    `run` rather than `lines`, which returns `[]` on a non-zero exit: a path
    missing from a snapshot is a path the pass never commits, so a read that
    failed must not be spelled the same way as a worktree with nothing in it.
    One failed half is enough to return None — a partial snapshot is the same
    silent omission in a smaller size.
    """
    changed: set[str] = set()
    # Untracked files count: a fix that only adds a test file still fixed the
    # finding, and diff-only detection would report it as skipped.
    for args in (("diff", "HEAD", "--name-only"),
                 ("ls-files", "--others", "--exclude-standard")):
        r = git_client.run(*args, cwd=wt_path)
        if not r.ok:
            log.warn(proc.failure_message(
                f"Could not list what changed in {wt_path}", r,
            ))
            return None
        changed.update(line for line in r.stdout.splitlines() if line)
    return changed


def _agent_changed(wt_path: str, before: set[str]) -> set[str] | None:
    """What the agent added to the worktree's dirty set, or None when the
    second snapshot could not be taken.

    None is not an empty delta. An empty one says the agent changed nothing and
    there is nothing to commit; None says the pass cannot name what the agent
    changed, which is the case where committing nothing loses work.
    """
    after = _changed_source_files(wt_path)
    return None if after is None else after - before


def _report_unattributable(wt_path: str) -> None:
    """Report a fix pass whose work could not be attributed, and where it is.

    Staging everything is not the fallback: the pass stages by name so that a
    build artifact or unrelated work in progress never rides along in a commit
    it then pushes, and a snapshot that failed is exactly when that list is
    unavailable. The edits are still in the worktree, so the honest end of this
    path is to say so and commit nothing.
    """
    log.error(
        f"could not read what the fix pass changed in {wt_path} — nothing was "
        f"committed or pushed. Any fixes it made are still in the worktree:\n"
        f"  git -C '{wt_path}' status"
    )


def _summary(outcomes: list[ItemOutcome], findings: dict[str, Finding]) -> str:
    """What the pass did, for the commit message and the operator's terminal.

    Three blocks, because the three answers are worth telling apart: a fix is
    work done, a skip is work the next round should pick up, and a decline is
    work nobody is going to do. `findings` is what the ids were rendered from —
    the tracking file records no description of its own, so the one line a fix
    is reported under comes from the finding it answered.
    """
    lines: list[str] = []
    _block(lines, "Fixed:", [
        (o.id, _describe(findings.get(o.id), o))
        for o in outcomes if o.outcome is FixOutcome.FIXED
    ])
    _block(lines, "Skipped:", [
        (o.id, o.reason or "no auto-fix")
        for o in outcomes if o.outcome in _STILL_OPEN
    ])
    _block(lines, "Declined:", [
        (o.id, o.reason or "adjudicated, not a defect")
        for o in outcomes if o.outcome is FixOutcome.DECLINED
    ])
    return "\n".join(lines)


def _block(lines: list[str], heading: str, entries: list[tuple[str, str]]) -> None:
    """Append one heading and its entries, or nothing when there are none."""
    if not entries:
        return
    lines.append(heading)
    lines.extend(f"  - [{item_id}] {text}" for item_id, text in entries)


def _describe(finding: Finding | None, outcome: ItemOutcome) -> str:
    """The one line a fixed finding is reported under.

    Its first body line, truncated, and its path when the body is empty. An id
    the review no longer holds has no description to report, so the line names
    the location the tracking file recorded for it — or, failing that, nothing
    but the id.
    """
    if finding is None:
        return outcome.file or outcome.id
    if finding.body:
        return finding.body.split("\n", 1)[0][:80]
    return finding.path


def _annotation(outcome: ItemOutcome) -> str:
    """How the review document spells this outcome, or "" when it spells nothing.

    The tracking file's three boxes and the review's two annotations are the
    same vocabulary written twice. A decline and a `needs a person` both leave
    the finding unchecked and both say why, in the words the review's own
    parsers already read back; a fix is a ticked box and carries no annotation,
    and a deferral is a finding nobody answered, which is the document exactly
    as it stands.
    """
    if outcome.outcome is FixOutcome.DECLINED:
        word = "declined"
    elif outcome.outcome is FixOutcome.NEEDS_HUMAN:
        word = "skipped"
    else:
        return ""
    return f"*({word} — {outcome.reason})*" if outcome.reason else f"*({word})*"


def _apply_outcomes(text: str, outcomes: list[ItemOutcome]) -> str:
    """The review document with each finding's line rewritten to its outcome.

    A fix ticks the box, a decline or a `needs a person` appends the annotation,
    and anything else leaves the line alone — which is what hands a finding the
    pass never answered to the next round unchanged.

    A finding the review had already checked, declined or skipped keeps what it
    has: those verdicts were reached before the agent ran and outrank it, and
    appending a second annotation to a line that carries one leaves the document
    saying two things about one finding.
    """
    prior = {f.id: f for f in ReviewDocument.parse(text).findings}
    by_id = {o.id: o for o in outcomes}
    written: set[str] = set()
    lines = text.split("\n")
    for n, line in enumerate(lines):
        match = FINDING_ID_RE.match(line.strip())
        if not match:
            continue
        finding_id = f"{match.group(2)}{match.group(3)}"
        finding = prior.get(finding_id)
        outcome = by_id.get(finding_id)
        if finding is None or outcome is None or finding_id in written:
            continue
        written.add(finding_id)
        if finding.checked or finding.declined or match_skip(finding):
            continue
        if outcome.outcome is FixOutcome.FIXED:
            lines[n] = line.replace("- [ ]", "- [x]", 1)
            continue
        note = _annotation(outcome)
        if note:
            lines[n] = f"{line.rstrip()} {note}"
    return "\n".join(lines)


class ReviewFixAdapter(fix_engine.FixAdapter):
    """The findings pass, in the terms `fix_engine` runs one in.

    The open findings are the work; everything the review already settled — a
    checked box, a decline it reached itself — never reaches the agent and never
    reaches the turn budget those items would have bought.

    Two things this adapter carries that the engine does not ask for. `before`
    is the worktree's dirty set from before the agent ran, taken by the caller
    because it has to be taken before the pass starts. `changed` is the
    difference `landing` works out, held so `record` reports on the same set the
    commit was scoped to rather than reading the worktree a third time.
    """

    phase = Phase.FIX
    title = "Review Fix Tracking"
    action = "applying review findings"
    item_noun = "finding"

    def __init__(
        self, job: ReviewJob, findings: list[Finding], before: set[str],
    ) -> None:
        self.job = job
        self.workdir = Path(job.wt_path)
        self.artifacts = Path(job.artifact_dir)
        self.branch = job.pr.head
        self.repo = job.repo
        self.pr = job.pr_number
        self.config = job.config
        self.effort = job.effort
        self.model = job.model
        self.findings = {f.id: f for f in findings}
        self.before = before
        self.changed: set[str] | None = None
        self.summary = ""

    @property
    def session_log(self) -> Path:
        """Where the pass streams its session: the review's own fix log.

        Named from the phase registry rather than by the engine's default,
        because a review directory's sweep finds its leavings by asking the
        registry what each phase writes. A log under any other name survives the
        run that wrote it.
        """
        return Path(phase_log_path(self.job.review_file, self.phase))

    def add_dirs(self) -> list[Path]:
        """The worktree, and the review directory the tracking file sits in."""
        return [self.workdir, self.artifacts]

    def items(self) -> list[fix_types.FixItem]:
        return [
            fix_types.FixItem(
                id=f.id, file=f.path, line=f.line or 0,
                label=severity_by_key(f.severity).section, body=f.body,
            )
            for f in self.findings.values()
        ]

    def template_vars(self) -> dict[str, str]:
        """Nothing — `fix-findings.md` asks for no substitution the engine withholds."""
        return {}

    def landing(self, outcomes: list[ItemOutcome]) -> fix_engine.LandSpec:
        """Commit the files the agent touched, and only those.

        The second snapshot is taken here because this is the one point between
        the agent finishing and the commit being made: earlier and it misses the
        agent's work, later and the commit has already happened. A snapshot that
        failed lands an empty scope, which commits nothing — `record` is what
        then says where the work was left.
        """
        self.changed = _agent_changed(str(self.workdir), self.before)
        self.summary = _summary(outcomes, self.findings)
        fixed = sum(1 for o in outcomes if o.outcome is FixOutcome.FIXED)
        skipped = sum(1 for o in outcomes if o.outcome in _STILL_OPEN)
        message = "fix: self-review findings"
        if fixed:
            message += f"\n\n{fixed} fixed, {skipped} skipped"
        if self.summary:
            message += f"\n\n{self.summary}"
        return fix_engine.LandSpec(
            message=message, paths=self.changed if self.changed else set(),
        )

    def record(self, run: fix_engine.FixRun) -> None:
        """Report the pass, and write its answers into the review document.

        A pass whose work could not be attributed re-renders nothing. The
        document still describing every finding as open is what sends the next
        round back over them — which is the right outcome, because the commit
        that would have made them done never happened.
        """
        if self.changed is None:
            _report_unattributable(str(self.workdir))
            return
        if self.summary:
            log.info("Fix summary:")
            for line in self.summary.splitlines():
                print(f"  {line}", file=sys.stderr)
        review_file = Path(self.job.review_file)
        review_file.write_text(
            _apply_outcomes(review_file.read_text(), run.outcomes),
        )


def run_fix_pass(job: ReviewJob, trail: Trail | None = None) -> None:
    """Apply what an agent can of the findings in `job`'s review, and commit it.

    Returns without running an agent when there is no review to work from or
    nothing in it still open.
    """
    doc = ReviewDocument.read(job.review_file) if _has_output(job.review_file) else None
    if doc is None:
        log.warn("No review file to fix — skipping fix pass")
        return

    # A declined finding is not work: it was considered and rejected, so it is
    # out of the work set and out of the turn budget it would otherwise buy.
    findings = [f for f in doc.open_findings if not f.declined]
    if not findings:
        log.info("No findings left to fix — skipping fix pass")
        return

    # ceiling: attribution is by path, so a file already dirty when the pass
    # starts is never credited to the agent — edits it makes to that file are
    # neither staged nor committed. Upgrade to comparing each path's content
    # hash across the snapshot once fix passes routinely run against trees that
    # are dirty in the very files the review has findings on.
    before = _changed_source_files(job.wt_path)
    if before is None:
        # Refused before the agent runs, so nothing is lost by refusing: with no
        # baseline the pass cannot tell its own work from what was already here,
        # and it would either commit the worktree wholesale or commit none of it.
        log.error(f"could not read the state of {job.wt_path} — skipping fix pass")
        return

    fix_engine.run(ReviewFixAdapter(job, findings, before), trail=trail)
