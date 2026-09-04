"""Deciding what became of each finding the last review reported.

Given a prior review and this round's merged output, works out for every prior
finding whether it was fixed, still stands, was declined, or was passed over
with nobody accounting for it — and where that answer came from.

The merge that produces this round's output is `review_merge`'s; the identity
two findings are matched on is `review_grammar`'s. This module only decides.

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
review. Reconciliation parses its input for finding-shaped lines, so a
reconciliation written into the review would come back to the next round
looking like a fresh set of prior findings.

The ledger this module reads is not the only account of what became of a
prior finding. Every finding posted inline opened a review thread,
independent of that ledger, and what the author did with that thread is the
other one — `review_reply_threads` reads it.
"""

# doc-group: findings

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from git import client as git_client
from core import log
from core import serde
from review.document import SECTION_PRIOR_FINDINGS, ReviewDocument, ReviewHeader
from review.grammar import (
    ANNOTATE_FINDING_RE, BOLD_FINDING_ID_RE, SID_MARKER_RE, FindingIdentity,
    parse_ledger_line,
)
from review.paths import FILENAME_PRIOR_FINDINGS, review_artifact_path
from review.spans import finding_spans
from review.types import (
    DISPOSITION_TAIL_PUNCTUATION, FindingRef, FindingSpan, LedgerEntry,
    PriorDisposition, PriorFinding,
)
from core.text import plural

# ── Carry-forward identity ───────────────────────────────────────────────────

def _stable_ids(text: str) -> set[str]:
    """Every prior-finding identity the text carries.

    Both the markers in carried-forward text and the ID each finding line
    hashes to on its own — a synthesis agent that retypes a carried finding
    drops the marker but keeps the path and the wording, so the recomputed ID
    is the part that survives it.
    """
    ids = set(SID_MARKER_RE.findall(text))
    for raw in text.split("\n"):
        identity = FindingIdentity.of(raw.strip())
        if identity:
            ids.add(identity.stable_id)
    return ids


# ── Reading the prior review ─────────────────────────────────────────────────

def _parse_ledger(review_text: str) -> list[LedgerEntry]:
    """The entries of the review's prior-findings ledger, in order."""
    section = ReviewDocument.parse(review_text).section(SECTION_PRIOR_FINDINGS)
    entries = (parse_ledger_line(raw) for raw in section.split("\n"))
    return [entry for entry in entries if entry]


def _prior_finding(span: FindingSpan, prior_text: str) -> PriorFinding:
    """One prior finding, read from its own line down to whatever ends it."""
    line = span.line
    identity = FindingIdentity.of(line)
    label_m = BOLD_FINDING_ID_RE.search(line)
    body = [raw.strip() for raw in span.text_of(prior_text).split("\n")]
    return PriorFinding(
        ref=FindingRef(
            label_m.group(1) if label_m else "",
            identity.path if identity else "",
        ),
        stable_id=identity.stable_id if identity else "",
        text="\n".join(body).strip(),
    )


def _parse_prior_findings(prior_text: str) -> list[PriorFinding]:
    """Every finding the prior review reported, each with the text reporting it.

    `ANNOTATE_FINDING_RE` chooses which declarations count — the shape the
    stable-ID annotator writes, which is what carry-forward matches on — and
    `finding_spans` says how far down each one's text runs.
    """
    return [
        _prior_finding(span, prior_text)
        for span in finding_spans(prior_text)
        if ANNOTATE_FINDING_RE.match(span.line)
    ]


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
    TREE = "tree"
    NONE = "none"

    @property
    def stated(self) -> bool:
        """Whether the review said this, rather than the pipeline inferring it."""
        return self in (DispositionSource.LEDGER, DispositionSource.CARRIED)


class UndecidedReason(StrEnum):
    """Why a finding reached no verdict.

    Undecided is only a useful warning while it means "the review passed this
    by". Three of the four members below are something else — two of them a
    line this pipeline could not read, one a check it was in no position to run
    — and lumping them in with the omissions is what turns the warning into
    noise the reader learns to skip.

    Declaration order is the order `_report` prints the groups: the ones a
    reader can act on come first.
    """

    UNREADABLE_VERDICT = "unreadable verdict"
    NO_LOCATION = "no location parsed"
    NOT_MENTIONED = "not accounted for"
    NOT_CHECKABLE = "not checkable"

    @property
    def heading(self) -> str:
        """How the group reads above the findings in it."""
        return _UNDECIDED_HEADINGS[self]


_UNDECIDED_HEADINGS = {
    UndecidedReason.UNREADABLE_VERDICT: "the ledger names these but states no verdict this could read",
    UndecidedReason.NO_LOCATION: "these name no file this could read, so nothing could be checked",
    UndecidedReason.NOT_MENTIONED: "neither this review nor the tree accounts for these",
    UndecidedReason.NOT_CHECKABLE: "there was nothing to check these against",
}


@dataclass(frozen=True)
class PriorRecord:
    """One prior finding, what became of it, and how that was settled.

    `disposition` is None when nothing settled it, and `reason` then says which
    kind of nothing — the ledger naming the finding without a verdict this can
    read is a different failure from the review never mentioning it, and only
    the second is the review's.
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
        ends = [git_client.abbrev(sha) for sha in (self.prior_sha, self.head_sha) if sha]
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
        return git_client.ok("cat-file", "-e", f"{self.prior_sha}:{path}", cwd=self.wt_path)

    def _before_text(self, path: str) -> str:
        if path not in self._before:
            blob = git_client.out("show", f"{self.prior_sha}:{path}", cwd=self.wt_path)
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
                basis=f"nothing it quotes was in `{path}` at {git_client.abbrev(self.prior_sha)}")
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
    ledger: list[LedgerEntry],
    tree: _Tree,
) -> PriorRecord:
    """The disposition of one prior finding, from the first source that has one.

    The ledger goes first because it is the only source that can say `Declined`
    — a judgement no amount of reading the tree recovers. A carry-forward comes
    next and means the finding is still open, whatever the tree looks like: a
    reviewer who just looked at the code outranks an inference about it.
    """
    entry = next((e for e in ledger if e.covers(finding.ref)), None)
    if entry and entry.disposition:
        return PriorRecord(finding.ref, entry.disposition, DispositionSource.LEDGER,
                           "the ledger says so")
    if finding.stable_id in carried:
        return PriorRecord(finding.ref, PriorDisposition.STILL_OPEN,
                           DispositionSource.CARRIED, "this review restates it")
    inference = tree.infer(finding)
    if inference.disposition:
        return PriorRecord(finding.ref, inference.disposition, DispositionSource.TREE,
                           inference.basis)
    if entry:
        return PriorRecord(finding.ref, None, DispositionSource.LEDGER,
                           f'the ledger line reads "{entry.text.strip()}"',
                           UndecidedReason.UNREADABLE_VERDICT)
    return PriorRecord(finding.ref, None, DispositionSource.NONE, inference.basis,
                       inference.reason)


def reconcile(
    prior_text: str,
    review_text: str,
    wt_path: str = "",
    head_sha: str = "",
) -> Reconciliation:
    """What `review_text` made of every finding the prior review reported.

    `wt_path` is the worktree the review was written against. Without it
    nothing is inferred and the review's own account stands alone, which is
    what a caller reconciling two documents rather than a run wants.
    """
    return _reconcile_findings(
        _parse_prior_findings(prior_text), prior_text, review_text, wt_path, head_sha,
    )


def _reconcile_findings(
    findings: list[PriorFinding],
    prior_text: str,
    review_text: str,
    wt_path: str,
    head_sha: str,
) -> Reconciliation:
    """`reconcile()`'s body, taking the prior findings already parsed.

    Split out so `passed_over()` can reuse the one parse of `prior_text`
    instead of paying for a second one just to get the findings back.
    """
    carried = _stable_ids(review_text)
    ledger = _parse_ledger(review_text)
    prior = ReviewHeader.parse(prior_text)
    tree = _Tree(wt_path, prior.head_sha)
    return Reconciliation(
        prior_sha=tree.prior_sha,
        prior_date=prior.date,
        head_sha=head_sha or (git_client.head_sha(wt_path) if wt_path else ""),
        records=[_settle(finding, carried, ledger, tree) for finding in findings],
    )


def passed_over(
    prior_text: str, review_text: str, wt_path: str = "", head_sha: str = "",
) -> list[PriorFinding]:
    """The prior findings `review_text` left out that the tree at `wt_path` still holds.

    Findings rather than records: a `PriorRecord` says what became of a
    finding, and a caller asking for a disposition needs the finding itself —
    the lines the prior review wrote, to put back in front of an agent.

    `head_sha` only reaches `reconcile()` to skip its own `git_client.head_sha`
    lookup — this function never reads `Reconciliation.head_sha` itself, so a
    caller that already has the value in hand should pass it.

    Only `NOT_MENTIONED` qualifies. It is the one undecided reason that is the
    review's own omission; the other three are this pipeline's failures — a
    verdict it could not parse, a location it could not read, a tree it had
    nothing to check against — and asking an agent again settles none of them.

    Records come back one per finding in parse order, so the two lists are
    zipped rather than matched on a label, which two findings in one file may
    share.
    """
    if not prior_text:
        return []
    findings = _parse_prior_findings(prior_text)
    reconciliation = _reconcile_findings(findings, prior_text, review_text, wt_path, head_sha)
    return [
        finding
        for finding, record in zip(findings, reconciliation.records, strict=True)
        if not record.decided and record.reason is UndecidedReason.NOT_MENTIONED
    ]


def _write(review_file: str, reconciliation: Reconciliation) -> str:
    """Record `reconciliation` beside `review_file`, returning the sidecar's path."""
    path = review_artifact_path(review_file, FILENAME_PRIOR_FINDINGS)
    serde.write_json(Path(path), serde.to_dict(reconciliation))
    return path


def _report(reconciliation: Reconciliation, sidecar: str = "") -> None:
    """Say what was reconciled, against which review, and what is left undecided.

    An undecided finding is a gap in the bookkeeping, not a defect in the code,
    so the line says what was checked before it says what was missed — a
    warning that only ever names findings reads as a defect report and trains
    the reader to skip it.

    The undecided are then printed by `UndecidedReason` rather than as one
    list, because they are not one thing: a line this could not read is a
    defect here and says what shape would have parsed, while a finding the
    review passed by is a defect there. Run together they read as the second,
    which is how six unreadable verdicts once reported as six omissions.
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
        log.info(f"Reconciled {scope}")
        return
    log.warn(f"{len(undecided)} of {total} prior finding{plural(total)} undecided")
    log.dim(f"checked {scope}")
    for reason, records in reconciliation.undecided_groups:
        _report_group(reason, records)
    if sidecar:
        log.dim(f"recorded in {sidecar}")


def _report_group(reason: UndecidedReason, records: list[PriorRecord]) -> None:
    """Print one reason's findings under a heading saying what the reason means."""
    log.dim(f"{reason.heading} ({len(records)}):")
    for record in records:
        log.dim(f"  {record.ref.label} — {record.basis}")
    if reason is UndecidedReason.UNREADABLE_VERDICT:
        log.dim(f"  {_VERDICT_SHAPE}")


# What to write instead, for the reader who has just been shown a line that did
# not parse. Both the words and the punctuation come from what the parser
# accepts, so this cannot describe a shape the parser would reject.
_VERDICT_SHAPE = (
    f"write one of {', '.join(d.value for d in PriorDisposition)} first, then "
    f"end the line or break with one of: {' '.join(DISPOSITION_TAIL_PUNCTUATION)}"
)


def record_prior_findings(
    review_file: str,
    prior_text: str,
    wt_path: str = "",
) -> Reconciliation | None:
    """Reconcile the review at `review_file` against `prior_text` and record it.

    Runs before post-processing strips the ledger, which is the only window in
    which the review still says what it made of the prior findings. Returns
    None when there is nothing to reconcile — no prior review, no review file,
    or a prior review that reported no findings.
    """
    path = Path(review_file)
    if not prior_text or not path.exists():
        return None
    reconciliation = reconcile(prior_text, path.read_text(), wt_path)
    if not reconciliation.records:
        return None
    _report(reconciliation, _write(review_file, reconciliation))
    return reconciliation
