"""Fix pass for review.

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
the split is its: a push puts the pass's work on a branch somebody else is
reading, while a commit reaches only the worktree the pass was pointed at.

That split assumes the worktree has one writer. It does not hold for a tree an
operator is editing at the same time — a pass that committed into one moved HEAD
under them and captured work they had just reverted. Nothing here can tell that
tree from any other: a dirty worktree is the ordinary case for a self-review,
and no fix pass on this machine has a terminal to be asked. What the pass does
leave is evidence — `trail` records the pre-existing dirt at `dirty_baseline`,
and every record carries who started the run.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.
"""

# doc-group: findings

from __future__ import annotations

import re
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

from agent.diagnosis import Diagnosis, DiagnosisKind
import fix.engine
import fix.scope
import fix.suite
import fix.types
import fix.verify
import core.log
import core.publishing
import git.client
from core.phases import Phase
from pr.fix import UNVERIFIED_NOTE_INLINE, FixOutcome, ItemOutcome
from review.paths import phase_log_path, read_review_meta, write_review_meta
from review.document import (
    SECTION_STATIC_ANALYSIS, ReviewDocument, is_skipped, section_span,
)
from review.grammar import FINDING_ID_RE
from review.static_analysis import (
    STATIC_ID_RE, CheckerResult, StaticViolation, all_violations,
)
from review.retry import _has_output
from review.types import Finding, OpenFinding, ReviewJob, severity_by_key
from core.trail import Trail

# The two outcomes that leave a finding open. A deferral is a finding the agent
# never reached and a `needs a person` is one it read and handed on, and the
# review document says the same thing about both: still unchecked, still there
# for the next round.
_STILL_OPEN = (FixOutcome.DEFERRED, FixOutcome.NEEDS_HUMAN)

# A deferral with no reason on a truncated pass is work the agent never reached,
# not a decline of auto-fix. Named so a reader of the commit body can tell.
_NOT_REACHED = "not reached (turn limit)"

# How many static violations one pass will take. Without a cap, a section that
# runs to hundreds of lines on a large diff — nesting alone does, which is why
# it renders collapsed — would spend the whole turn budget on mechanical edits
# and starve the findings beside it.
#
# Above the phase's own turns-per-item arithmetic, deliberately. `Phase.FIX`
# scales five turns an item to a cap of eighty, so a chunk of sixteen is what
# gets the full rate and twenty does not. A violation is cheaper than a finding
# by roughly that margin: the fix is a known shape (flatten the control flow)
# against a located line, with no premise to disprove first and no reviewer
# claim to weigh — which is most of what the five turns buy a finding. Sixteen
# would be the number if these cost what findings cost.
#
# The ones past the cap stay in the section unticked and unannotated, which is
# what the document should say about work nothing answered. A later round picks
# them up: the section is regenerated each review, and by then the fixed ones
# are gone.
#
# ceiling: a fixed cap rather than a share of the remaining turn budget, and one
# whose margin over sixteen is reasoned rather than measured. Upgrade when a
# pass is regularly truncating with findings unread — that is the cap competing
# with the findings for turns, which is the thing it exists to prevent.
_MAX_STATIC_ITEMS = 20

# The trailing half of a static item's section heading in the tracking file,
# where a finding carries its severity. These have no severity — a violation is
# not a reviewer's claim about importance — so the label says what the item is
# instead.
_STATIC_LABEL = "Static analysis"


def _static_body(violation: StaticViolation) -> str:
    """What the agent is shown for one violation.

    The measurement, and then the thing the template cannot say generically:
    that this claim is not a reviewer's opinion to be disproved. The fix
    template spends a section telling the agent to try to falsify its item's
    premise, which is right for a finding and wrong here — the depth was
    counted, and an agent invited to argue with it produces a decline instead of
    an early return.
    """
    where = f"{violation.context} " if violation.context else ""
    return (
        f"{violation.message} {where}".strip() + ".\n\n"
        "Measured by the nesting checker, not claimed by a reviewer: the depth "
        "is counted from the code and is not in question. Fix it by flattening "
        "the control flow — an early return, a guard clause, or an extracted "
        "helper — rather than by arguing the count."
    )


# The footer a pass gets when it claims no fixes and commits changes anyway.
# Only that combination: a pass with a fix in it has already explained why the
# tree moved, and listing the files under every ordinary summary would train the
# reader to skip the block that matters.
_UNCLAIMED_EDITS = (
    "\nThis pass reports no fixes but is committing changes to:\n{files}\n"
    "Nothing above claims this work. Read the diff before trusting the lines "
    "that say it was skipped."
)


def _truncated(stop: Diagnosis | None) -> bool:
    return stop is not None and stop.kind is DiagnosisKind.MAX_TURNS


def _skip_reason(outcome: ItemOutcome, truncated: bool) -> str:
    if outcome.reason:
        return outcome.reason
    if truncated and outcome.outcome is FixOutcome.DEFERRED:
        return _NOT_REACHED
    return "no auto-fix"


def _summary(outcomes: list[ItemOutcome], described: dict[str, str],
             changed: set[str] | None = None, *,
             stop: Diagnosis | None = None,
             suite: fix.suite.SuiteResult | None = None) -> str:
    """What the pass did, for the commit message and the operator's terminal.

    Three blocks, because the three answers are worth telling apart: a fix is
    work done, a skip is work the next round should pick up, and a decline is
    work nobody is going to do. `described` is the one line each id is reported
    under, by id — the tracking file records no description of its own, and the
    pass has two streams of work whose descriptions are read off different
    types, so the caller resolves them and this takes the result.

    `changed` is what the pass is about to commit, and it is here because this
    text is the only account of the pass most people read. The blocks describe
    outcomes; the commit carries files; attribution between them is by path and
    misses an agent that fixed a finding by editing its caller or its test. The
    footer states the files rather than claiming anything about them, which is
    the most this can honestly do and strictly more than the silence it replaces.

    `stop` is why the last attempt ended. A MAX_TURNS pass is named as truncated
    here so the commit body differs from a finished one without the trail.

    `suite` is what the repo's own checks said about the tree being committed,
    and it goes first because it is the line that decides how much of the rest
    to believe. A red run puts its failing output in the body: the commit is
    the artifact a reader reaches for weeks later, and a verdict with no
    evidence under it sends them back to re-run what the pass already ran.
    """
    lines: list[str] = []
    if _truncated(stop):
        lines.append(f"Pass truncated: {stop.message}")
    _suite_block(lines, suite)
    _block(lines, "Fixed:", [
        (o.id, _fixed_entry(described.get(o.id, ""), o))
        for o in outcomes if o.outcome.counts_as_fixed
    ])
    _block(lines, "Skipped:", [
        (o.id, _skip_reason(o, _truncated(stop)))
        for o in outcomes if o.outcome in _STILL_OPEN
    ])
    _block(lines, "Declined:", [
        (o.id, o.reason or "adjudicated, not a defect")
        for o in outcomes if o.outcome is FixOutcome.DECLINED
    ])
    if lines and changed and not any(o.outcome.counts_as_fixed for o in outcomes):
        lines.append(_UNCLAIMED_EDITS.format(files="\n".join(
            f"  {path}" for path in sorted(changed))))
    return "\n".join(lines)


def _suite_block(lines: list[str], suite: fix.suite.SuiteResult | None) -> None:
    """Open the summary with what ran against this work, and what it said.

    Every reportable state gets a line, and the lines differ on purpose: a
    repo that declares no command says so, and a run that came back clean
    says that instead. Rendering those two the same is how a red suite once
    shipped under a body reading `4 fixed, 0 skipped`.

    The one silent state is a pass that had no reason to run the checks.
    `fix_suite.detail_lines` decides that, via `SuiteResult.reportable`; this
    forwards the decision rather than making a second one.
    """
    detail = fix.suite.detail_lines(suite) if suite else []
    if detail:
        lines.extend([*detail, ""])


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
# commit rejected by a hook it never runs. `_block` wraps to this width.
_SUMMARY_LINE_MAX = 100


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


def _fixed_line(
    line: str, outcome: ItemOutcome, pattern: re.Pattern[str] = FINDING_ID_RE,
) -> str:
    """The declaration a landed fix leaves behind: ticked, and hedged if owed.

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

    `pattern` is how the caller's own declaration is spelled, and its group(1)
    must be the box. A static violation is declared differently from a finding
    and would not match the default — the tick would silently be a no-op, which
    is the failure this parameter exists to prevent rather than a default worth
    falling back to.
    """
    # The box the declaration carries, not the first `- [ ]` anywhere on the
    # line: a finding quoting the empty box in its own prose — a review of a
    # template does — would otherwise have that quotation ticked instead, which
    # corrupts the prose and annotates a finding that stays open.
    box = pattern.match(line.strip())
    if not (box and box.group(1) == " "):
        return line
    ticked = line.replace("- [ ]", "- [x]", 1)
    detail = _unverified_detail(outcome)
    if detail is None:
        return ticked
    caveat = f"unverified — {_escape_annotation(detail)}" if detail else "unverified"
    return _annotated(ticked, f"*({caveat})*")


def _fixed_entry(described: str, outcome: ItemOutcome) -> str:
    """One `Fixed:` entry: what was fixed, and the caveat when one is owed.

    The description is left intact; `_block` wraps it to the commit-body limit.
    `verify_detail` is agent prose with no length contract, so a token longer
    than a continuation's budget is clipped here rather than left to `_block`:
    `textwrap.fill` would still split it across lines, but the clip is what
    keeps one unverifiable token from carrying more of the caveat's budget
    than a continuation line has room for.
    """
    described = described or outcome.file or outcome.id
    detail = _unverified_detail(outcome)
    if detail is None:
        return described
    if detail:
        prefix = len(f"  - [{outcome.id}] ")
        room = max(_SUMMARY_LINE_MAX - prefix, 0)
        longest = max((len(tok) for tok in detail.split()), default=0)
        if longest > room:
            detail = _clip(detail, room)
    caveat = (
        f" ({UNVERIFIED_NOTE_INLINE} — {detail})"
        if detail else f" ({UNVERIFIED_NOTE_INLINE})"
    )
    return described + caveat


def _block(lines: list[str], heading: str, entries: list[tuple[str, str]]) -> None:
    """Append one heading and its entries, or nothing when there are none.

    Each bullet wraps to `_SUMMARY_LINE_MAX` with a hanging indent matching
    the `  - [id] ` prefix, so a long description continues under the text
    rather than being clipped mid-word. Skipped and declined entries share
    this path, so they wrap the same way as a fix.
    """
    if not entries:
        return
    lines.append(heading)
    for item_id, text in entries:
        prefix = f"  - [{item_id}] "
        wrapped = textwrap.fill(
            text,
            width=_SUMMARY_LINE_MAX,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
            break_on_hyphens=False,
        )
        lines.extend(wrapped.splitlines())


def _location_of(finding: Finding | None) -> str:
    """Where a finding pointed, for a reader choosing what to file.

    The id it is stored beside is a position in one rendering, so the location
    is what still identifies the finding after the next review renumbers.
    """
    if finding is None or not finding.path:
        return ""
    return f"{finding.path}:{finding.line}" if finding.line else finding.path


def _describe_finding(finding: Finding) -> str:
    """The first body line a fixed finding is reported under.

    Its first body line, and its path when the body is empty. An id no
    description is built for — one the review no longer holds — falls back in
    `_fixed_entry` to the location the tracking file recorded, or to the id.
    Wrapping to the commit-body limit happens in `_block`.
    """
    if finding.body:
        return finding.body.split("\n", 1)[0]
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


def _apply_static_outcomes(text: str, outcomes: list[ItemOutcome]) -> str:
    """The `## Static Analysis` section with each violation rewritten to its outcome.

    The same three spellings `_apply_outcomes` writes on a finding, for the same
    reason: without this the section still describes the tree the review
    measured, and a reader opening the PR after a fix pass sees a violation
    reported live at a line where it no longer exists.

    Scoped to the section's own span, which is the whole of what makes the
    rewrite safe. A declaration is matched by its shape, and that shape appears
    outside the section too: a finding quoting an `SA` line — a review of this
    repo does, because the tests and the docstrings here contain them verbatim
    — is a line this would otherwise match. Unscoped, the first match in the
    document won: the quotation inside a finding's body was ticked and the
    violation it was quoting stayed open, so one pass corrupted a finding and
    reported its own fix as undone.

    A violation past `_MAX_STATIC_ITEMS` has no outcome and is left exactly as
    rendered, which is what the document should say about work nothing answered.

    An already-ticked line is skipped. Nothing re-runs a pass over one review
    file today, but the finding side guards the same case, and the failure if it
    ever happens is a second annotation on a line that already carries one.
    """
    span = section_span(text, SECTION_STATIC_ANALYSIS)
    if span is None:
        return text
    head, body, tail = text[:span.start], span.body_of(text), text[span.end:]
    by_id = {o.id: o for o in outcomes}
    written: set[str] = set()
    lines = body.split("\n")
    for n, line in enumerate(lines):
        match = STATIC_ID_RE.match(line.strip())
        if not match:
            continue
        violation_id = match.group(2)
        outcome = by_id.get(violation_id)
        if outcome is None or violation_id in written:
            continue
        written.add(violation_id)
        if match.group(1) == "x":
            continue
        if outcome.outcome.counts_as_fixed:
            lines[n] = _fixed_line(line, outcome, STATIC_ID_RE)
            continue
        note = _annotation(outcome)
        if note:
            lines[n] = _annotated(line, note)
    return head + "\n".join(lines) + tail


def _bullet_paths(paths: set[str]) -> str:
    """A prompt-ready list of paths, or `(none)` when the set is empty."""
    if not paths:
        return "(none)"
    return "\n".join(f"- {p}" for p in sorted(paths))


class ReviewFixAdapter(fix.engine.FixAdapter):
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

    def __init__(
        self, job: ReviewJob, findings: list[Finding],
        violations: list[StaticViolation] | None = None,
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
        violations = violations or []
        violation_ids = [v.id for v in violations]
        duplicate_ids = {
            vid for vid in violation_ids if violation_ids.count(vid) > 1
        }
        if duplicate_ids:
            # `run_static_analysis` renumbers globally on every call, so this
            # cannot happen through the documented path today. It is here for
            # the hand-built list this constructor does not otherwise refuse:
            # the dict below silently keeps the last of a duplicate id and drops
            # the rest, and a dropped violation is a fix the operator cannot
            # find unless something says so.
            #
            # The empty id counts. Two violations both carrying `""` collapse
            # exactly as two `SA1`s do, and an unaddressable violation is the
            # worse of the two to lose quietly — excluding it would have made
            # the guard silent on the case it is least able to explain.
            core.log.warn(
                f"Duplicate static violation ids {sorted(duplicate_ids)} — "
                "keeping the last of each, dropping the rest"
            )
        self.violations = {v.id: v for v in violations}
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

    def verify_session_log(self, chunk: int) -> Path:
        """The gate's session log, named by the registry, one file per chunk."""
        return Path(phase_log_path(self.job.review_file, self.verify_phase, chunk))

    def add_dirs(self) -> list[Path]:
        """The worktree, and the review directory the tracking file sits in.

        Both come from the base grant now; this override remains only as the
        place to say that a review's artifacts directory is the review file's
        own, not a scratch dir.
        """
        return super().add_dirs()

    def items(self) -> list[fix.types.FixItem]:
        """The findings and the static violations, as one work set.

        Findings first, because the template tells the agent to work in severity
        order and a violation has no severity to sort into. The two streams keep
        their own id spellings, which is what lets `record` tell them apart
        afterwards without carrying a flag through the engine.
        """
        items = [
            fix.types.FixItem(
                id=f.id, file=f.path, line=f.line or 0,
                label=severity_by_key(f.severity).section, body=f.body,
            )
            for f in self.findings.values()
        ]
        items.extend(
            fix.types.FixItem(
                id=v.id, file=v.file, line=v.line,
                label=_STATIC_LABEL, body=_static_body(v),
            )
            for v in self.violations.values()
        )
        return items

    def _branch_files(self) -> set[str]:
        """The files this branch changed against the PR or stack base.

        `job.pr.files` is already that set — collected against `pr.base`, not
        against main — so a stacked branch whose parent is not main still has
        the right list, and this does not shell out again.
        """
        return {f["path"] for f in self.job.pr.files if f.get("path")}

    def _anchor_files(self) -> set[str]:
        """The files the work points at, in scope even off the branch.

        The violations are computed from `job.pr.files`, so their paths are
        already in `_branch_files` and add nothing here in the ordinary case.
        They are included anyway rather than assumed: the assumption is a
        property of a call two modules away, and if it ever stops holding the
        symptom is an agent told its own work set is out of scope.
        """
        paths = {f.path for f in self.findings.values() if f.path}
        return paths | {v.file for v in self.violations.values() if v.file}

    def _allowed_paths(self) -> set[str]:
        return fix.scope.commit_allowed(self._branch_files(), self._anchor_files())

    def template_vars(self) -> dict[str, str]:
        """The branch file set and finding anchors `fix-findings.md` names.

        Without them the agent cannot tell an in-scope path from an
        out-of-scope one, and 'a change to files outside this branch' in the
        template is an instruction it cannot follow. Under Pi the fix agent
        never loads CLAUDE.md, so Scope Discipline has to arrive here.
        """
        return {
            "branch_base": self.job.pr.base or "HEAD",
            "branch_files": _bullet_paths(self._branch_files()),
            "finding_anchors": _bullet_paths(self._anchor_files()),
        }

    def landing(
        self, outcomes: list[ItemOutcome], changed: set[str] | None,
    ) -> fix.engine.LandSpec:
        """Commit the files the agent touched that belong on this branch.

        A snapshot that failed arrives as None and lands an empty scope, which
        commits nothing — `record` is what then says where the work was left.

        Paths outside the branch, the finding anchors, and their tests are
        dropped here — the same warn-and-leave contract as `_drop_scratch`,
        applied after attribution so a file already dirty when the pass
        started is still never credited.

        A rename is exempt, because dropping half of one is worse than
        committing the whole: the branch file set is fixed when the PR is
        collected and cannot contain a name the agent invents mid-fix, so the
        destination reads as out of branch while the source's deletion reads
        as in it. Committing only the deletion leaves a tree that does not
        build and content stranded in the worktree.
        """
        if changed is not None:
            sources = self._branch_files() | self._anchor_files()
            keep = fix.scope.drop_outside(
                changed, self._allowed_paths(), self.workdir, sources,
            )
            changed = keep | fix.scope.rename_partners(
                changed - keep, keep, self.workdir,
            )
        self.changed = changed
        self.summary = _summary(
            outcomes, self._descriptions(), changed,
            stop=self.stop, suite=self.suite,
        )
        fixed = sum(1 for o in outcomes if o.outcome.counts_as_fixed)
        skipped = sum(1 for o in outcomes if o.outcome in _STILL_OPEN)
        message = "fix: self-review findings"
        if fixed:
            message += "\n\n" + fix.suite.qualify_tally(
                f"{fixed} fixed, {skipped} skipped", self.suite)
        if self.summary:
            message += f"\n\n{self.summary}"
        return fix.engine.LandSpec(
            message=message, paths=self.changed if self.changed else set(),
        )

    def record(self, run: fix.engine.FixRun) -> None:
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
            core.log.info("Fix summary:")
            for line in self.summary.splitlines():
                print(f"  {line}", file=sys.stderr)
        review_file = Path(self.job.review_file)
        text = _apply_outcomes(review_file.read_text(), run.outcomes)
        review_file.write_text(_apply_static_outcomes(text, run.outcomes))
        self._persist_static_declines(run.outcomes)
        self._persist_open_findings(run.outcomes)

    def _persist_open_findings(self, outcomes: list[ItemOutcome]) -> None:
        """Record which findings this pass left open, so they can be filed later.

        The document cannot answer this. A deferral writes no annotation at all,
        so a finding the pass never reached reads exactly like one it never had
        — and `pr review --track` runs in a later process, after these outcomes
        have gone. Same reason `_persist_static_declines` exists one method up,
        for the same kind of verdict the deliverable cannot carry.

        Replaced rather than accumulated: a later pass reads the same findings
        again, and an entry kept from an earlier round would offer to file work
        that has since been fixed.
        """
        described = self._descriptions()
        still_open = tuple(
            OpenFinding(
                id=o.id,
                outcome=str(o.outcome),
                summary=described.get(o.id, ""),
                location=_location_of(self.findings.get(o.id)),
                reason=o.reason,
            )
            for o in outcomes
            if o.outcome in _STILL_OPEN and o.id in self.findings
        )
        review_dir = Path(self.job.artifact_dir)
        write_review_meta(
            review_dir, replace(read_review_meta(review_dir), open_findings=still_open),
        )

    def _persist_static_declines(self, outcomes: list[ItemOutcome]) -> None:
        """Keep this pass's static declines where the next round can read them.

        The annotation on the document does not survive: the section is a fresh
        render of a fresh scan every review. So an adjudication that lives only
        there is one the next round cannot see, and the violation goes back to
        the agent — every round, for the life of the branch, because nothing
        about the code changed to stop it being reported.

        Declines only. A skip is work still owed and belongs back in the next
        round's list; a fix removes the violation from the next scan by
        fixing it, so neither needs recording. What has to persist is the one
        verdict that says "this will keep being reported and that is correct".

        Accumulated rather than replaced, so a round that declines nothing does
        not retract what an earlier round decided.
        """
        settled = {
            self.violations[o.id].site: o.reason or "declined by an earlier pass"
            for o in outcomes
            if o.outcome is FixOutcome.DECLINED and o.id in self.violations
        }
        if not settled:
            return
        review_dir = Path(self.job.artifact_dir)
        meta = read_review_meta(review_dir)
        write_review_meta(review_dir, replace(
            meta, static_declined={**meta.static_declined, **settled},
        ))

    def _descriptions(self) -> dict[str, str]:
        """The one line each item is reported under, by id.

        Built from both streams here rather than read off either inside
        `_summary`, which would otherwise need to know there are two and which
        id belongs to which.
        """
        described = {
            f.id: _describe_finding(f) for f in self.findings.values()
        }
        described.update(
            (v.id, v.describe()) for v in self.violations.values()
        )
        return described


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
        core.log.warn("No review file to fix — skipping fix pass")
        return

    # A declined finding is not work: it was considered and rejected, so it is
    # out of the work set and out of the turn budget it would otherwise buy.
    findings = [f for f in doc.open_findings if not f.declined]
    review_dir = Path(job.artifact_dir)
    violations = _static_items(
        job.static_results, read_review_meta(review_dir).static_declined,
    )
    if not findings and not violations:
        core.log.info("No findings left to fix — skipping fix pass")
        _report_unpushed(job)
        return

    run = fix.engine.run(
        ReviewFixAdapter(job, findings, violations),
        trail=trail, verify=fix.verify.run,
    )
    _record_commit(job, run)


def _static_items(
    results: list[CheckerResult], declined: dict[str, str] | None = None,
) -> list[StaticViolation]:
    """The violations this pass will take, capped and in reading order.

    `declined` is what an earlier round adjudicated, by site — those are not
    work. The section is rebuilt from a fresh scan every review, so a decline
    written onto the document is gone by the next run; without reading it back
    from the sidecar, a violation declined against a `ceiling:` comment is
    re-presented to the agent every round for the life of the branch.

    Only from a result scoped to the lines the branch added. An unscoped result
    measured whole files, so its violations include depth the branch inherited
    — reporting that is fair, and handing it to an agent is instructing it to
    flatten code the branch never touched. `fix.scope` cannot refuse those
    edits, because the file they land in *is* a branch file, so the refusal has
    to be here. The section still reports them; nobody is asked to fix them.

    A violation with no id never went through `run_static_analysis` and so is
    not addressable — it renders without a checkbox, and an outcome against it
    would have no line to be written back to.
    """
    unscoped = [r for r in results if r.violations and not r.scoped_to_added]
    if unscoped:
        core.log.warn(
            "Static analysis: "
            f"{', '.join(r.name for r in unscoped)} measured whole files — "
            "reporting those violations but not fixing them, since the branch "
            "did not necessarily add the lines they sit on."
        )
    scoped = [r for r in results if r.scoped_to_added]
    settled = declined or {}
    addressable = _one_per_site(
        v for v in all_violations(scoped) if v.id and v.site not in settled
    )
    taken = addressable[:_MAX_STATIC_ITEMS]
    if len(addressable) > len(taken):
        core.log.info(
            f"Static analysis: taking {len(taken)} of {len(addressable)} "
            f"violations this pass — the rest stay open for the next review."
        )
    return taken


def _one_per_site(violations) -> list[StaticViolation]:
    """The first violation at each `(file, function)`, in reading order.

    A nesting checker reports every line past the limit, so one over-deep
    function arrives as four or five violations that one early return answers.
    Handed over whole they cost four items of a twenty-item cap, four boxes the
    agent must tick for one edit, and — worse — a guaranteed false
    contradiction at the gate: `fix.reconcile` attributes by path, so several
    items anchored in one file make every deferral among them look like a
    deferral with an edit behind it. `fix/reconcile.py` names that as its
    upgrade trigger, for a domain that batches several items in one file. This
    is that domain, so it does not batch them.

    The shallowest line at a site is the one kept, because it is where the
    flattening has to start; the deeper lines below it are the same defect seen
    further in. The rest stay in the section, unticked — honest, since nothing
    answered them individually, and the next review re-measures the file and
    finds them gone if the fix worked.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[StaticViolation] = []
    for violation in violations:
        site = (violation.file, violation.context)
        if site in seen:
            continue
        seen.add(site)
        kept.append(violation)
    return kept


def _report_unpushed(job: ReviewJob) -> None:
    """Say when a pass that published nothing leaves the branch ahead anyway.

    `--post` is a gate on what this pass publishes, and the only thing it ever
    publishes is its own fix commit — which `land` makes and pushes together,
    inside a pass that a review with nothing left to fix returns before
    reaching. So a clean review under `--post` pushes nothing, correctly, and
    says nothing about it either. That silence is the defect: the operator's own
    commits are the usual reason a branch is ahead here, and a run that reports
    success while the remote is behind reads as a branch that shipped.

    Reporting rather than pushing. This pass did not make those commits and does
    not know what they are for — pushing a branch it never touched is a larger
    claim than the flag makes, and a worse failure than the one it would fix.
    Naming the gap costs a line and leaves the decision where it belongs.

    Silent when publishing is off: a held gate is a run that was never going to
    push, so an unpushed branch is the outcome that was asked for.
    """
    if not core.publishing.enabled():
        return
    # No upstream is a branch that has never been pushed, which `commits_ahead`
    # reads as 0 — the same answer as "nothing to say", and the right one here:
    # a branch with no remote is not a branch whose remote is behind.
    ahead = git.client.commits_ahead(cwd=job.wt_path, target_ref="@{u}")
    if ahead:
        core.log.warn(
            f"This pass pushed nothing — it had no fixes to make — but the "
            f"branch is {ahead} commit{'s' if ahead != 1 else ''} ahead of its "
            f"remote. Push them yourself if they are meant to be published."
        )


def _record_commit(job: ReviewJob, run: fix.engine.FixRun) -> None:
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
    The same holds for a landing that ran but produced no new commit: `land`
    reports that as `NO_CHANGES` or `COMMIT_FAILED`, never as `None`, and both
    carry an empty `sha` — the tell that nothing here should overwrite an
    earlier round's real commit.
    """
    if run.landed is None or not run.landed.sha:
        return
    sha, status = run.landed.sha, run.landed.status
    # Both must be the strings the sidecar's schema says they are. Every review
    # lookup on the machine walks these files, so a value that is not
    # serialisable takes the whole sidecar down — the attribution of a review
    # that was written correctly — rather than costing the one field it came in.
    if not isinstance(sha, str) or not isinstance(status, str):
        core.log.warn(f"Fix pass reported an unrecordable commit ({status!r}) — not stamped")
        return
    review_dir = Path(job.artifact_dir)
    write_review_meta(review_dir, replace(
        read_review_meta(review_dir),
        fix_commit_sha=sha,
        fix_commit_status=str(status),
    ))
    if run.landed.resume:
        core.log.info(f"Fix commit held locally — {run.landed.resume}")
