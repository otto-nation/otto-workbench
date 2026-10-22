"""Deciding what became of each finding the last review reported.

Given a prior review and this round's merged output, works out for every prior
finding whether it was fixed, still stands, was declined, or was passed over
with nobody accounting for it — and where that answer came from.

The merge that produces this round's output is `review.merge`'s; the identity
two findings are matched on is `review.grammar`'s. This module only decides.

A re-review ends with a `## Prior findings` ledger: one line per finding the
previous review reported, saying whether the change fixed it, left it open, or
declined it. The ledger is bookkeeping — it is stripped before the review is
published — and it is written by the agent that has just spent its attention
on the review itself, so it is the first thing to come up short.

Coming up short used to mean a line on stderr and nothing else. `reconcile`
replaces that with a disposition for every prior finding and a record of them
that outlives the run. A finding the ledger passes over is not automatically
unaccounted for: whether the file it names is still in the tree, and whether
the code it quotes is still in that file, are questions the worktree answers
without asking an agent anything. Only what neither the review nor the tree
settles is reported as undecided, and every record says which of the two
settled it, so an inference is never read back as a statement.

Reporting is not the only outcome. `passed_over` asks the same question one
phase early, against the merged group findings, and hands back the findings
neither the groups nor the tree settled — while there is still an agent that
can decide them, rather than after the document is written and the only thing
left to do is warn.

The record is a sidecar in the review directory rather than a section of the
review. Reconciliation walks the prior round's record, so a reconciliation
written into the review would come back to the next round looking like a
fresh set of prior findings.

The ledger this module reads is not the only account of what became of a
prior finding. Every finding posted inline opened a review thread,
independent of that ledger, and what the author did with that thread is the
other one — `review.reply_threads` reads it.
"""

# doc-group: findings

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import git.client
import core.log
import core.serde
from review.grammar import SID_MARKER_RE
from review.paths import FILENAME_PRIOR_FINDINGS, review_artifact_path
from review.record import Disposition, LedgerLine, ReviewRecord
from review.render import finding_line
from review.store import StoredReview
from review.types import FindingRef, PriorDisposition, PriorFinding, ReviewMeta
from core.text import plural

# ── Reading the prior review ─────────────────────────────────────────────────

def _record_stable_ids(record: ReviewRecord) -> set[str]:
    """Every prior-finding identity the review carries.

    A finding's own `sid` (copied from the prior finding it carries forward) and
    its stable ID, plus any `<!-- sid: -->` marker in the review's prose. A
    finding restated only in words is not carried: the agent that carried it
    was asked for its sid.
    """
    ids = {f.sid for f in record.findings if f.sid} | {f.stable_id for f in record.findings if f.stable_id}
    for text in (record.summary, record.verdict, *(e.body for e in record.extra),
                 *(f.body for f in record.findings)):
        ids |= set(SID_MARKER_RE.findall(text))
    return ids


def _record_ledger(record: ReviewRecord) -> list[LedgerLine]:
    return list(record.ledger)


def _prior_findings(record: ReviewRecord) -> list[PriorFinding]:
    """Every finding the prior round still held open, with the text that reported it."""
    out = []
    for f in record.visible:
        if f.disposition is Disposition.FIXED:
            continue
        line = finding_line(f)
        out.append(PriorFinding(
            ref=FindingRef(f.id, f.path),
            stable_id=f.stable_id,
            text="\n".join(raw.strip() for raw in line.split("\n")).strip(),
            declined=f.disposition is Disposition.DECLINED,
        ))
    return out


# ── Reconciliation against the prior review ──────────────────────────────────

# The bucket a finding lands in when nothing settled it. Not a
# `PriorDisposition` member: those are verdicts a review may state, and "no
# verdict" is the absence of one rather than a fourth thing to state.
UNDECIDED_LABEL = "Undecided"

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")

# Below this a backtick span is a token rather than a quotation — `ok`, `nil`,
# a flag name — and it leaves a file for reasons that have nothing to do with
# the finding that mentioned it.
_MIN_QUOTE_CHARS = 12

# How much of a quoted span a basis line reproduces before it stops being a
# log line and starts being the code.
_BASIS_QUOTE_CHARS = 60


class DispositionSource(StrEnum):
    """What settled a prior finding's disposition.

    Recorded next to the disposition rather than folded into it: a verdict the
    pipeline worked out from the tree carries less authority than one the
    review stated, and the two must never be read as the same claim.
    """

    LEDGER = "ledger"
    CARRIED = "carried"
    # The prior round declined it, and the file it names has not changed since.
    # A verdict a review stated, carried rather than re-stated.
    PRIOR = "prior"
    TREE = "tree"
    NONE = "none"

    @property
    def stated(self) -> bool:
        """Whether the review said this, rather than the pipeline inferring it."""
        return self in (DispositionSource.LEDGER, DispositionSource.CARRIED,
                        DispositionSource.PRIOR)


class UndecidedReason(StrEnum):
    """Why a finding reached no verdict.

    Undecided is only a useful warning while it means "the review passed this
    by". Two of the three members below are something else — a location this
    pipeline could not read, a check it was in no position to run — and lumping
    them in with the omissions is what turns the warning into noise the reader
    learns to skip.

    Declaration order is the order `_report` prints the groups: the ones a
    reader can act on come first.
    """

    NO_LOCATION = "no location parsed"
    NOT_MENTIONED = "not accounted for"
    NOT_CHECKABLE = "not checkable"

    @property
    def heading(self) -> str:
        """How the group reads above the findings in it."""
        return _UNDECIDED_HEADINGS[self]


_UNDECIDED_HEADINGS = {
    UndecidedReason.NO_LOCATION: "these name no file this could read, so nothing could be checked",
    UndecidedReason.NOT_MENTIONED: "neither this review nor the tree accounts for these",
    UndecidedReason.NOT_CHECKABLE: "there was nothing to check these against",
}


@dataclass(frozen=True)
class PriorRecord:
    """One prior finding, what became of it, and how that was settled.

    `disposition` is None when nothing settled it, and `reason` then says which
    kind of nothing. An unreadable ledger line is dropped rather than recorded,
    so it never settles from the ledger; the finding falls to the tree check.
    """

    ref: FindingRef = field(default_factory=FindingRef)
    disposition: PriorDisposition | None = None
    source: DispositionSource = DispositionSource.NONE
    basis: str = ""
    reason: UndecidedReason | None = None

    @property
    def decided(self) -> bool:
        """Whether this finding reached a verdict."""
        return self.disposition is not None


@dataclass
class Reconciliation:
    """Every finding of the prior review, and what this review made of each."""

    prior_sha: str = ""
    prior_date: str = ""
    head_sha: str = ""
    records: list[PriorRecord] = field(default_factory=list)

    @property
    def undecided(self) -> list[PriorRecord]:
        """The findings neither the review nor the tree accounted for."""
        return [record for record in self.records if not record.decided]

    @property
    def unaccounted(self) -> list[str]:
        """How the undecided findings read in a log line."""
        return [record.ref.label for record in self.undecided]

    @property
    def undecided_groups(self) -> list[tuple[UndecidedReason, list[PriorRecord]]]:
        """The undecided findings by why they are, in the order they are reported."""
        return [
            (reason, [record for record in self.undecided if record.reason is reason])
            for reason in UndecidedReason
            if any(record.reason is reason for record in self.undecided)
        ]

    @property
    def inferred(self) -> int:
        """How many verdicts came from the tree rather than from the review."""
        return sum(1 for r in self.records if r.source is DispositionSource.TREE)

    @property
    def counts(self) -> dict[str, int]:
        """How many findings reached each verdict, the undecided among them."""
        tally = {d.value: 0 for d in PriorDisposition}
        tally[UNDECIDED_LABEL] = 0
        for record in self.records:
            key = record.disposition.value if record.decided else UNDECIDED_LABEL
            tally[key] += 1
        return tally

    @property
    def tally(self) -> str:
        """The counts as a log line, with the empty buckets left out."""
        parts = [f"{n} {label}" for label, n in self.counts.items() if n]
        if self.inferred:
            parts.append(f"{self.inferred} inferred from the tree")
        return ", ".join(parts)

    @property
    def range_label(self) -> str:
        """The pair of trees the reconciliation compared, as far as it knows them."""
        ends = [git.client.abbrev(sha) for sha in (self.prior_sha, self.head_sha) if sha]
        return " → ".join(ends) if len(ends) == 2 else (ends[0] if ends else "an unnamed commit")


@dataclass(frozen=True)
class _Inference:
    """What the tree says about a prior finding, and why it says it.

    `reason` is what the finding falls to when the tree settles nothing, and
    defaults to the review's own omission because that is what most of the
    tree's silences mean: it looked, and found the finding's subject intact.
    """

    disposition: PriorDisposition | None = None
    basis: str = ""
    reason: UndecidedReason = UndecidedReason.NOT_MENTIONED


@dataclass
class _Tree:
    """The two versions of the worktree a reconciliation compares.

    Blob reads are cached because findings cluster in files — a review reports
    four things about one module, and re-reading its prior text once per
    finding is four `git show` calls for one answer.
    """

    wt_path: str = ""
    prior_sha: str = ""
    _before: dict[str, str] = field(default_factory=dict)

    def infer(self, finding: PriorFinding) -> _Inference:
        """What the tree makes of `finding`, or why it makes nothing of it."""
        path = finding.ref.path
        if not self.wt_path:
            return _Inference(
                basis="there was no worktree to check it against",
                reason=UndecidedReason.NOT_CHECKABLE,
            )
        if not path:
            return _Inference(
                basis="it names no file",
                reason=UndecidedReason.NO_LOCATION,
            )
        if not self.prior_sha:
            return _Inference(
                basis="the prior review names no commit to compare against",
                reason=UndecidedReason.NOT_CHECKABLE,
            )
        if not (Path(self.wt_path) / path).is_file():
            return self._absent(path)
        return self._quotes(finding, path)

    def _absent(self, path: str) -> _Inference:
        """The verdict on a finding whose file is not in the current tree.

        A file the prior commit did not hold either was never this finding's
        subject — the path was misread — so its absence now settles nothing.
        """
        if not self._existed(path):
            return _Inference(basis=f"`{path}` is in neither tree")
        return _Inference(PriorDisposition.FIXED, f"`{path}` is no longer in the tree")

    def _existed(self, path: str) -> bool:
        return git.client.ok("cat-file", "-e", f"{self.prior_sha}:{path}", cwd=self.wt_path)

    def _before_text(self, path: str) -> str:
        if path not in self._before:
            blob = git.client.out("show", f"{self.prior_sha}:{path}", cwd=self.wt_path)
            self._before[path] = _norm(blob)
        return self._before[path]

    def _quoted(self, finding: PriorFinding, path: str) -> list[str]:
        """The spans the finding quotes that its file really held at `prior_sha`.

        Quoting is how a review points at code, but a review also quotes what
        it is merely naming: a symbol defined elsewhere, a phrase from the
        prompt, a paraphrase of what the code ought to say instead. Requiring
        the span to reproduce in the file's own prior text drops all three, and
        leaves the spans whose disappearance means something happened here.
        """
        before = self._before_text(path)
        spans = dict.fromkeys(m.group(1) for m in _CODE_SPAN_RE.finditer(finding.text))
        return [
            span for span in spans
            if len(span) >= _MIN_QUOTE_CHARS
            and not span.startswith(path)
            and _norm(span) in before
        ]

    def _quotes(self, finding: PriorFinding, path: str) -> _Inference:
        """The verdict on a finding by whether the code it quoted survived."""
        quoted = self._quoted(finding, path)
        if not quoted:
            return _Inference(
                basis=f"nothing it quotes was in `{path}` at {git.client.abbrev(self.prior_sha)}")
        after = _norm((Path(self.wt_path) / path).read_text(errors="replace"))
        gone = [span for span in quoted if _norm(span) not in after]
        if not gone:
            return _Inference(basis=f"the code it quotes is still in `{path}`")
        # ceiling: one vanished quotation is read as the finding's subject
        # having gone, which a large enough unrelated edit to the same file can
        # fake. The record names the span that decided it, so a wrong call is
        # auditable rather than silent — upgrade to matching the span against
        # the diff hunks if a review is ever recorded as fixed on a span the
        # change only moved.
        basis = f"`{_shorten(gone[0])}` is no longer in `{path}`"
        if len(gone) > 1:
            basis += f", nor {len(gone) - 1} other span{plural(len(gone) - 1)} it quotes"
        return _Inference(PriorDisposition.FIXED, basis)


def _norm(text: str) -> str:
    """Collapse whitespace, so a quotation matches code that has been rewrapped."""
    return " ".join(text.split())


def _shorten(span: str) -> str:
    if len(span) <= _BASIS_QUOTE_CHARS:
        return span
    return span[:_BASIS_QUOTE_CHARS] + "…"


def _settle(
    finding: PriorFinding,
    carried: set[str],
    ledger: list[LedgerLine],
    tree: _Tree,
    changed: frozenset[str] | None = None,
) -> PriorRecord:
    """The disposition of one prior finding, from the first source that has one.

    The ledger goes first because it is the only source that can say `Declined`
    — a judgement no amount of reading the tree recovers. A carry-forward comes
    next and means the finding is still open, whatever the tree looks like: a
    reviewer who just looked at the code outranks an inference about it.
    A prior decline whose file is outside `changed` comes third: it is the prior
    review's statement, outranked only by this review's own.
    """
    entry = next((e for e in ledger if e.covers(finding.ref)), None)
    if entry is not None:
        return PriorRecord(finding.ref, entry.disposition, DispositionSource.LEDGER,
                           "the ledger says so")
    if finding.stable_id in carried:
        return PriorRecord(finding.ref, PriorDisposition.STILL_OPEN,
                           DispositionSource.CARRIED, "this review restates it")
    if _declined_and_untouched(finding, changed):
        return PriorRecord(finding.ref, PriorDisposition.DECLINED, DispositionSource.PRIOR,
                           f"declined last round, and `{finding.ref.path}` has not changed since")
    inference = tree.infer(finding)
    if inference.disposition:
        return PriorRecord(finding.ref, inference.disposition, DispositionSource.TREE,
                           inference.basis)
    return PriorRecord(finding.ref, None, DispositionSource.NONE, inference.basis,
                       inference.reason)


def _declined_and_untouched(finding: PriorFinding, changed: frozenset[str] | None) -> bool:
    """Whether the prior round's decline still stands with nobody restating it.

    A decline is a judgement about code; while the file it names is outside the
    change set the code is the code that was judged, so asking an agent to say
    "Declined" again is the cost with no answer it could change. Unknown change
    set, or no file named, settles nothing.
    """
    path = finding.ref.path
    return finding.declined and bool(path) and changed is not None and path not in changed


def reconcile(
    prior: StoredReview,
    review: ReviewRecord,
    wt_path: str = "",
    head_sha: str = "",
    changed: frozenset[str] | None = None,
) -> Reconciliation:
    """What `review` made of every finding the prior review reported.

    `wt_path` is the worktree the review was written against. Without it
    nothing is inferred and the review's own account stands alone, which is
    what a caller reconciling two documents rather than a run wants.
    """
    return _reconcile_findings(
        _prior_findings(prior.record), prior.meta, review, wt_path, head_sha,
        changed,
    )


def _reconcile_findings(
    findings: list[PriorFinding],
    prior_meta: ReviewMeta,
    review: ReviewRecord,
    wt_path: str,
    head_sha: str,
    changed: frozenset[str] | None = None,
) -> Reconciliation:
    """`reconcile()`'s body, taking the prior findings already parsed.

    Split out so `passed_over()` can reuse the one walk of `prior.record`
    instead of paying for a second one just to get the findings back.
    """
    carried = _record_stable_ids(review)
    ledger = _record_ledger(review)
    tree = _Tree(wt_path, prior_meta.head_sha)
    return Reconciliation(
        prior_sha=tree.prior_sha,
        prior_date=prior_meta.date,
        head_sha=head_sha or (git.client.head_sha(wt_path) if wt_path else ""),
        records=[_settle(finding, carried, ledger, tree, changed) for finding in findings],
    )


def passed_over(
    prior: StoredReview | None, review: ReviewRecord, wt_path: str = "",
    head_sha: str = "",
    changed: frozenset[str] | None = None,
) -> list[PriorFinding]:
    """The prior findings `review` left out that the tree at `wt_path` still holds.

    Findings rather than records: a `PriorRecord` says what became of a
    finding, and a caller asking for a disposition needs the finding itself —
    the lines the prior review wrote, to put back in front of an agent.

    `head_sha` only reaches `reconcile()` to skip its own `git.client.head_sha`
    lookup — this function never reads `Reconciliation.head_sha` itself, so a
    caller that already has the value in hand should pass it.

    Only `NOT_MENTIONED` qualifies. It is the one undecided reason that is the
    review's own omission; the other two are this pipeline's failures — a
    location it could not read, a tree it had nothing to check against — and
    asking an agent again settles none of them.

    Records come back one per finding in parse order, so the two lists are
    zipped rather than matched on a label, which two findings in one file may
    share.
    """
    if prior is None:
        return []
    findings = _prior_findings(prior.record)
    reconciliation = _reconcile_findings(
        findings, prior.meta, review, wt_path, head_sha, changed,
    )
    return [
        finding
        for finding, record in zip(findings, reconciliation.records, strict=True)
        if not record.decided and record.reason is UndecidedReason.NOT_MENTIONED
    ]


def _write(review_file: str, reconciliation: Reconciliation) -> str:
    """Record `reconciliation` beside `review_file`, returning the sidecar's path."""
    path = review_artifact_path(review_file, FILENAME_PRIOR_FINDINGS)
    core.serde.write_json(Path(path), core.serde.to_dict(reconciliation))
    return path


def _report(reconciliation: Reconciliation, sidecar: str = "") -> None:
    """Say what was reconciled, against which review, and what is left undecided.

    An undecided finding is a gap in the bookkeeping, not a defect in the code,
    so the line says what was checked before it says what was missed — a
    warning that only ever names findings reads as a defect report and trains
    the reader to skip it.

    The undecided are then printed by `UndecidedReason` rather than as one
    list, because they are not one thing: a location this could not parse is a
    defect here, while a finding the review passed by is a defect there. Run
    together they read as the second. An unreadable ledger line is dropped
    rather than recorded, and the finding falls to the tree check.
    """
    if not reconciliation.records:
        return
    total = len(reconciliation.records)
    scope = (
        f"{total} prior finding{plural(total)} across {reconciliation.range_label}"
        f": {reconciliation.tally}"
    )
    undecided = reconciliation.undecided
    if not undecided:
        core.log.info(f"Reconciled {scope}")
        return
    core.log.warn(f"{len(undecided)} of {total} prior finding{plural(total)} undecided")
    core.log.dim(f"checked {scope}")
    for reason, records in reconciliation.undecided_groups:
        _report_group(reason, records)
    if sidecar:
        core.log.dim(f"recorded in {sidecar}")


def _report_group(reason: UndecidedReason, records: list[PriorRecord]) -> None:
    """Print one reason's findings under a heading saying what the reason means."""
    core.log.dim(f"{reason.heading} ({len(records)}):")
    for record in records:
        core.log.dim(f"  {record.ref.label} — {record.basis}")


def record_prior_findings(
    review_file: str,
    prior: StoredReview | None,
    review: ReviewRecord,
    wt_path: str = "",
    head_sha: str = "",
    changed: frozenset[str] | None = None,
) -> Reconciliation | None:
    """Reconcile `review` against `prior` and record it beside `review_file`.

    Runs before post-processing clears the ledger, which is the only window in
    which the review still says what it made of the prior findings. Returns
    None when there is nothing to reconcile — no prior review, or a prior
    review that reported no findings. `review_file` is the sidecar path only.
    `head_sha` is the commit the run pinned; the range is labelled with it
    rather than with a live HEAD that may have moved since.
    """
    if prior is None:
        return None
    reconciliation = reconcile(prior, review, wt_path, head_sha, changed=changed)
    if not reconciliation.records:
        return None
    _report(reconciliation, _write(review_file, reconciliation))
    return reconciliation
