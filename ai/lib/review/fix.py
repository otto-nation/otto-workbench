"""Fix pass for claude-review.

Runs after a review is written and `--fix` is set. `fix_engine` owns the
pipeline — the batching, the agent, the retry, the commit — and what stays here
is the three things only a review can answer: which findings are still open,
which files the pass is allowed to commit, and how the review document reads
once the agent has answered.

What the agent changed is a snapshot difference the engine takes on either side
of the run — see `fix.scope`. Only the paths that appear in the second snapshot
and not the first are attributed to the agent, so the pass neither commits nor
takes credit for whatever was already sitting in the worktree.

A snapshot git could not take reads as None rather than as an empty one.
Everything outside the difference goes uncommitted, so an unreadable worktree
spelled the same way as an unchanged one is how a pass reports success having
left the agent's fixes behind.

The agent answers on a tracking file, not on the review document. That document
is the deliverable — a reviewer reads it and a re-review reconciles against it —
and letting the agent edit it in place made the pass's evidence about itself the
same text it was editing: a box nobody ticked read as a skip, an annotation
phrased loosely read as no annotation at all, and the pass had to guess which
findings its own agent had touched. `record` re-renders the document from the
outcomes instead, so what it says is what the pass decided.

Every claimed fix goes to the verify gate before any of it is committed — see
`fix.verify`. A ticked `fixed` box is a claim that an edit was made, which is
not the claim the commit message and the re-rendered document then publish; the
gate is what tells the two apart, and a fix it falsifies lands as work still
owed rather than as done.

The commit always happens; the push waits for `--post`. `land` owns both, and
the split is its: a local commit asserts nothing to anybody, while a push puts
the pass's work on a branch somebody else is reading.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.
"""

# doc-group: findings

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

from fix import engine as fix_engine
from fix import types as fix_types
from fix import verify as fix_verify
from core import log
from core.phases import Phase
from pr.fix import UNVERIFIED_NOTE_INLINE, FixOutcome, ItemOutcome
from review.paths import phase_log_path, read_review_meta, write_review_meta
from review.document import ReviewDocument, is_skipped
from review.grammar import FINDING_ID_RE
from review.retry import _has_output
from review.types import Finding, ReviewJob, severity_by_key
from core.trail import Trail

# The two outcomes that leave a finding open. A deferral is a finding the agent
# never reached and a `needs a person` is one it read and handed on, and the
# review document says the same thing about both: still unchecked, still there
# for the next round.
_STILL_OPEN = (FixOutcome.DEFERRED, FixOutcome.NEEDS_HUMAN)


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
        (o.id, _fixed_entry(findings.get(o.id), o))
        for o in outcomes if o.outcome.counts_as_fixed
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


def _unverified_detail(outcome: ItemOutcome) -> str | None:
    """Why the gate could not stand behind this fix, or None when it stood.

    Three states collapse to two answers here. None is a pass that never asked
    the gate, True is one it answered, and both mean the surfaces say nothing:
    only `False` — the gate ran and could not establish the fix works — earns a
    caveat. The empty string is that case with no detail to give, which is still
    a caveat and is why this returns None rather than "" for the quiet one.
    """
    if outcome.verified is not False:
        return None
    return outcome.verify_detail


# What one summary line may run to. `lib/conventions.sh` sets
# COMMIT_BODY_MAX_LEN to 100 and these lines land in a commit body, where
# nothing on this path enforces it — a fix pass is not going to have its own
# commit rejected by a hook it never runs.
_SUMMARY_LINE_MAX = 100

# Where a description is clipped before the budget above is applied. Kept as a
# cap of its own so an unhedged line reads the way it always has: the whole-line
# budget only bites once a caveat is there to compete with it.
_DESCRIBE_MAX = 80


def _clip(text: str, limit: int) -> str:
    """`text` no longer than `limit`, ellipsised when it had to give.

    The marker is part of the budget rather than added to it: a clip that
    overran the limit it was called with would defeat the one caller that has
    one.

    A limit of zero or less returns nothing rather than slicing to it. `text[:n]`
    with a non-positive `n` counts from the end, so the clip would hand back
    most of the string — longest output where the budget was tightest, which is
    the opposite of what every caller is asking for.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "\u2026"


# The caveat a landed-but-unverifiable fix carries on the review document, in
# the shape the other two annotations use. Anchored to the tail for the reason
# `_SKIP` and `_DECLINED` are: matched anywhere, a finding whose prose quotes
# the annotation — the docs of this module do, verbatim — would read as already
# carrying one.
_UNVERIFIED_TAIL_RE = re.compile(r"\*\(unverified(?:\s*[—–-]+\s*.+?)?\)\*\s*$")


def _annotated(line: str, note: str) -> str:
    """`line` with `note` appended, and nothing on it left to misread.

    Every annotation this module writes goes through here, because the hazard
    belongs to the append and not to any one caller. The patterns that read an
    annotation back are anchored to the end of the line but not to its start,
    so they match from wherever a `*(` appears to whatever `)*` comes last. A
    line whose prose merely *quotes* an annotation — the docs and tests of this
    module do, verbatim — is safe until something is appended after it: the
    append supplies the close, and the pattern spans the distance between.

    So the quotation in the line is defused before ours is added, which costs a
    space in someone's prose and keeps the annotation readable. Refusing to
    append instead would be the cheaper answer for the advisory caveat, and the
    wrong one here: a decline or a skip is a verdict the next round reads back,
    and a line that silently did not get one is a finding whose outcome was
    dropped. One rule for both, because two rules is how the second append site
    came to have no rule at all.

    `note` arrives already escaped and is not touched here. It is the one thing
    on the finished line that is *meant* to read as an annotation, so running it
    through the same defusing would break the annotation being written — the
    value interpolated into it is each caller's to escape, and both do.

    Only the `*(` sequence is rewritten, never whitespace. `_escape_annotation`
    collapses runs of it, which is right for a value the agent wrote and wrong
    for a line the reviewer did: `FindingIdentity` hashes the first eighty
    characters of the body, so collapsing a double space there changes the
    stable id and the next round's carry-forward stops recognising the finding.
    It also flattens inline code, table alignment and indentation that are the
    author's, not ours. A line cannot carry a newline in any case — it was split
    out of the document on one.

    A line already ending in our own caveat is returned untouched. Defusing it
    would mangle what a synthesis pass carried forward and leave a second copy
    beside it, which is the outcome this function exists to prevent, arriving
    through the function itself.

    A verdict withheld that way is not the dropped outcome the paragraph above
    refuses. A skip or a decline is withheld only on a line carrying our own
    caveat, and such a line is still unchecked, still undeclined, and so still
    in the next round's work set — the finding is retried rather than lost. The
    case it refuses to overwrite is narrow and the annotation it would have
    written is recoverable; the one it protects is not.
    """
    if _UNVERIFIED_TAIL_RE.search(line):
        return line
    return f"{line.rstrip().replace('*(', '* (')} {note}"


def _escape_annotation(detail: str) -> str:
    """`detail` with any annotation it quotes defused.

    `verify_detail` is the gate agent's own prose, and the gate is routinely
    reasoning about this repo — about, on occasion, this very function. A detail
    quoting `*(declined — ...)*` otherwise makes the whole finding parse as
    adjudicated, because `grammar.py`'s decline pattern is unanchored at its
    head and finds the quotation inside the caveat. `grammar.py` documents that
    exact class of failure for the annotation it owns; interpolating agent prose
    unescaped reintroduces it through the back door.

    The opening `*(` is what is broken rather than the closing `)*`: the decline
    pattern spans whatever sits between the two, so a defused close still leaves
    a match once prose supplies its own. Breaking the open leaves no annotation
    for any of the three patterns to find, and costs a space in a line of prose
    nobody parses.

    Newlines go too. A value carrying one splits the finding line in two, and
    the remainder — agent prose, on a line of its own — is parsed as whatever it
    happens to look like: text shaped like a finding declaration becomes one.
    `fix.tracking` collapses whitespace on the engine's path, but an
    `ItemOutcome` built anywhere else does not pass through it.
    """
    return " ".join(detail.replace("*(", "* (").split())


def _fixed_line(line: str, outcome: ItemOutcome) -> str:
    """The finding line a landed fix leaves behind: ticked, and hedged if owed.

    The tick and the caveat are one decision rather than two, so they are made
    in one place — a caller that ticked the box and then asked separately
    whether to annotate it is a caller that can do the first and forget the
    second.

    Only a line whose box this call actually ticked may be annotated. A PR-mode
    template asks for a finding with no checkbox at all, so the tick is a no-op
    there and `finding.checked` stays false however many rounds run — the guard
    upstream that makes this idempotent never engages, and the caveat would
    compound once per round on a finding that also never leaves `open_findings`.
    An already-hedged line is left alone for the same reason, which is what a
    synthesis pass carrying the annotation forward needs.
    """
    # The box the declaration carries, not the first `- [ ]` anywhere on the
    # line: a finding quoting the empty box in its own prose — a review of a
    # template does — would otherwise have that quotation ticked instead, which
    # corrupts the prose and annotates a finding that stays open.
    box = FINDING_ID_RE.match(line.strip())
    if not (box and box.group(1) == " "):
        return line
    ticked = line.replace("- [ ]", "- [x]", 1)
    detail = _unverified_detail(outcome)
    if detail is None:
        return ticked
    caveat = f"unverified — {_escape_annotation(detail)}" if detail else "unverified"
    return _annotated(ticked, f"*({caveat})*")


def _fixed_entry(finding: Finding | None, outcome: ItemOutcome) -> str:
    """One `Fixed:` entry: what was fixed, and the caveat when one is owed.

    Both halves are clipped, because either can overrun the line on its own: a
    finding's first line runs to `_DESCRIBE_MAX`, and `verify_detail` is agent
    prose with no length contract at all.

    The description gives way first. A truncated description still names the
    finding — the id beside it is what a reader looks the finding up by — while a
    caveat cut short is a claim about verification that stops mid-sentence, and
    the caveat is the part that changes what the reader does next. So the detail
    is clipped only once the description has given up everything it can.
    """
    described = _describe(finding, outcome)
    detail = _unverified_detail(outcome)
    prefix = len(f"  - [{outcome.id}] ")
    if detail is None:
        return _clip(described, max(_SUMMARY_LINE_MAX - prefix, 0))

    scaffolding = len(f" ({UNVERIFIED_NOTE_INLINE} — )") if detail else len(f" ({UNVERIFIED_NOTE_INLINE})")
    room = max(_SUMMARY_LINE_MAX - prefix - scaffolding, 0)
    # The description keeps at most half the room, so a long one cannot starve
    # the caveat; anything it leaves unused goes to the detail.
    described = _clip(described, room // 2)
    detail = _clip(detail, max(room - len(described), 0))
    caveat = f" ({UNVERIFIED_NOTE_INLINE} — {detail})" if detail else f" ({UNVERIFIED_NOTE_INLINE})"
    return described + caveat


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
        return _clip(finding.body.split("\n", 1)[0], _DESCRIBE_MAX)
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
    # `reason` is the gate's own prose by way of `engine`'s verdict detail, so a
    # reason quoting an annotation would otherwise nest one inside this one and
    # hand the inner word to the parsers — a skip written as a decline.
    reason = _escape_annotation(outcome.reason)
    return f"*({word} — {reason})*" if reason else f"*({word})*"


def _apply_outcomes(text: str, outcomes: list[ItemOutcome]) -> str:
    """The review document with each finding's line rewritten to its outcome.

    A fix ticks the box, a decline or a `needs a person` appends the annotation,
    and anything else leaves the line alone — which is what hands a finding the
    pass never answered to the next round unchanged.

    A fix the gate could not stand behind ticks the box and says so beside it.
    The tick is honest — an edit was made, and the pass committed it — but the
    document is what a reviewer reads and what the next round reconciles
    against, and a bare tick there is the pass vouching for an edit nothing
    exercised. The annotation is not a verdict the parsers read back, so the
    finding stays fixed to every reader that matters and carries the caveat for
    the one who is deciding whether to trust it.

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
        if finding.checked or finding.declined or is_skipped(finding):
            continue
        if outcome.outcome.counts_as_fixed:
            lines[n] = _fixed_line(line, outcome)
            continue
        note = _annotation(outcome)
        if note:
            lines[n] = _annotated(line, note)
    return "\n".join(lines)


class ReviewFixAdapter(fix_engine.FixAdapter):
    """The findings pass, in the terms `fix_engine` runs one in.

    The open findings are the work; everything the review already settled — a
    checked box, a decline it reached itself — never reaches the agent and never
    reaches the turn budget those items would have bought.

    `changed` is the engine's snapshot difference, held from `landing` so
    `record` reports on the same set the commit was scoped to rather than
    reading the worktree a third time.
    """

    phase = Phase.FIX
    # The gate that checks the pass's own claims before anything is committed.
    # Declared for the reason the comments adapter declares one: without it the
    # engine falls back to `phase`, and the gate is prompted with
    # `fix-findings.md` — a template telling it to edit source, which the gate's
    # own rules forbid.
    verify_phase = Phase.FIX_VERIFY
    title = "Review Fix Tracking"
    action = "applying review findings"
    item_noun = "finding"

    def __init__(self, job: ReviewJob, findings: list[Finding]) -> None:
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

    @property
    def verify_session_log(self) -> Path:
        """The gate's session log, named by the registry for the same reason."""
        return Path(phase_log_path(self.job.review_file, self.verify_phase))

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

    def landing(
        self, outcomes: list[ItemOutcome], changed: set[str] | None,
    ) -> fix_engine.LandSpec:
        """Commit the files the agent touched, and only those.

        A snapshot that failed arrives as None and lands an empty scope, which
        commits nothing — `record` is what then says where the work was left.
        """
        self.changed = changed
        self.summary = _summary(outcomes, self.findings)
        fixed = sum(1 for o in outcomes if o.outcome.counts_as_fixed)
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
        that would have made them done never happened. The engine has already
        said so on the operator's terminal; what is decided here is only
        whether the deliverable gets rewritten.
        """
        if self.changed is None:
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

    The gate runs on every pass, with no flag to switch it off. A ticked `fixed`
    box is the agent saying it made an edit, and this pass commits on the
    strength of that box alone — two passes shipped a failing suite and an
    untested behaviour change that way, both truthful under the box's own
    contract. A pass cheap enough to be worth skipping the check is a pass whose
    commits nobody should be reading as fixed.
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

    run = fix_engine.run(
        ReviewFixAdapter(job, findings), trail=trail, verify=fix_verify.run,
    )
    _record_commit(job, run)


def _record_commit(job: ReviewJob, run: fix_engine.FixRun) -> None:
    """Record the fix pass's commit in the review's sidecar, and say what is owed.

    The push is gated and the commit is not, so the ordinary end of
    `--fix` without `--post` is a commit sitting on the branch that nothing has
    sent. Until this, the only trace was one `resume` line on a terminal the
    operator may already have closed: no status surface knew the commit
    existed, and a review directory that recorded the findings recorded nothing
    about the work done against them.

    Written for every landing, not only a held one. A pushed commit is the fact
    that answers "what did the last fix pass do" on the next run, and a reader
    that only ever saw the held ones could not tell a pass that published from
    one that never ran.

    A pass with no landing — no items, nothing to commit — records nothing and
    leaves any earlier round's record alone, which is the difference between a
    round that had nothing to say and one that retracted what the last said.
    """
    if run.landed is None:
        return
    sha, status = run.landed.sha, run.landed.status
    # Both must be the strings the sidecar's schema says they are. Every review
    # lookup on the machine walks these files, so a value that is not
    # serialisable takes the whole sidecar down — the attribution of a review
    # that was written correctly — rather than costing the one field it came in.
    if not isinstance(sha, str) or not isinstance(status, str):
        log.warn(f"Fix pass reported an unrecordable commit ({status!r}) — not stamped")
        return
    review_dir = Path(job.artifact_dir)
    write_review_meta(review_dir, replace(
        read_review_meta(review_dir),
        fix_commit_sha=sha,
        fix_commit_status=str(status),
    ))
    if run.landed.resume:
        log.info(f"Fix commit held locally — {run.landed.resume}")
