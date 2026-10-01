"""Whether both sides of a replayed change survived the resolution of one file.

A conflict resolution can be wrong in two ways. It can merge badly, which is a
judgement call no tool here makes. Or it can *discard* one side outright —
`git checkout --ours` during a rebase replaces the file with the target's copy
and throws away every change the replayed commit made to it, including the
hunks git had already merged cleanly. Nothing reports that: the file has no
markers, `git add` accepts it, and `rebase --continue` either commits the rest
of the change or, when nothing is left, drops the commit without a word.

This module answers the second question only, from four texts — the merge
base, the target, the replayed commit's version, and the resolution — and
reports at two strengths:

* **Blocking.** A change one side made *cleanly* — no overlap with the other
  side, so git merged it unaided — reads in the resolution exactly as the base
  had it. That is what a whole-file checkout leaves behind and almost never
  what a person meant; the incident behind this check lost hand edits to it
  across two rebases with nothing noticing. When the resolution is the other
  side verbatim, the losses are reported as one whole-file verdict, which is
  the clearer account of what happened.
* **Advisory.** A region both sides changed resolves to exactly one side's
  text. That is often right — the target rewrote the code the commit touched —
  so it is reported, not refused.

Line-based and pure: it reads no repository. `replay_audit` supplies the texts
for a commit mid-rebase, and `resolve_ai` for an answer before it is written.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

# How many lines of each side's surroundings a change is looked for with. Two
# is enough to pin a line that repeats elsewhere in the file without reaching
# into a neighbouring change the other side made.
_CONTEXT_LINES = 2

# How many of a lost change's lines a report quotes. Enough to recognise the
# change by; the full hunk is one `git diff` away.
_EXCERPT_LINES = 3

# ceiling: SequenceMatcher is quadratic in the worst case, so a file past this
# many lines across its four versions is not audited at all rather than
# stalling a commit hook. Generated files — the usual giants — are exempt
# before this is reached. Upgrade trigger: if a hand-written file this large
# ever loses a change unreported, align with `git diff --patience` output
# instead of difflib.
MAX_AUDITED_LINES = 40_000


class Side(StrEnum):
    """Whose change a loss is about."""
    # The commit being replayed — REBASE_HEAD or CHERRY_PICK_HEAD.
    REPLAYED = "replayed"
    # The tip it is replayed onto — HEAD, which during a rebase is `--ours`.
    TARGET = "target"

    @property
    def label(self) -> str:
        return "the replayed commit" if self is Side.REPLAYED else "the target branch"


class LossKind(StrEnum):
    """How a side's change went missing."""
    # A change git merged cleanly reads as the base again.
    HUNK_REVERTED = "hunk_reverted"
    # The file is the other side verbatim, and that discarded at least one
    # change of this side's that git had merged cleanly.
    FILE_TAKEN_WHOLE = "file_taken_whole"
    # One side added, deleted, or kept the file and the resolution undid it.
    FILE_PRESENCE = "file_presence"
    # A region both sides changed resolves to exactly one of them.
    REGION_ONE_SIDED = "region_one_sided"


_BLOCKING_KINDS = frozenset({
    LossKind.HUNK_REVERTED, LossKind.FILE_TAKEN_WHOLE, LossKind.FILE_PRESENCE,
})


@dataclass(frozen=True)
class Loss:
    """One side's change that the resolution does not contain."""
    kind: LossKind
    side: Side
    # 1-based, in the losing side's own copy of the file. Zero when the loss is
    # about the file as a whole.
    line: int = 0
    excerpt: tuple[str, ...] = ()
    # What a side did to the file as a whole, for FILE_PRESENCE: "deleted the
    # file", "added the file", "changed the file".
    note: str = ""
    # Whether this loss refuses the commit. Set by the auditor rather than
    # derived from the kind alone: a deletion resolved against a side that also
    # edited the file is FILE_PRESENCE and still only advisory.
    blocking: bool = True

    def describe(self) -> str:
        """One readable sentence, followed by the quoted lines."""
        where = f" at line {self.line}" if self.line else ""
        what = {
            LossKind.HUNK_REVERTED: f"{self.side.label}'s change{where} reads as the base "
                                    "again, though git had merged it cleanly",
            LossKind.FILE_TAKEN_WHOLE: f"the file is the other side verbatim; every change "
                                       f"{self.side.label} made to it is gone",
            LossKind.FILE_PRESENCE: f"{self.side.label} {self.note}, and the resolution "
                                    "undid it",
            LossKind.REGION_ONE_SIDED: f"a region both sides changed{where} keeps only the "
                                       f"other side; {self.side.label}'s version is gone",
        }[self.kind]
        quoted = "".join(f"\n      {line}" for line in self.excerpt)
        return what + quoted


@dataclass(frozen=True)
class FileAudit:
    """Everything one file's resolution lost, or nothing."""
    path: str
    losses: tuple[Loss, ...] = ()
    # Why the file was not audited, when it was not. Empty when it was.
    skipped: str = ""

    @property
    def blocking(self) -> tuple[Loss, ...]:
        return tuple(loss for loss in self.losses if loss.blocking)

    @property
    def advisory(self) -> tuple[Loss, ...]:
        return tuple(loss for loss in self.losses if not loss.blocking)


@dataclass(frozen=True)
class _Change:
    """A non-equal run of one side against the base, in base coordinates."""
    side: Side
    start: int
    end: int
    lines: tuple[str, ...]
    # Where the run begins in the side's own text.
    at: int


@dataclass(frozen=True)
class _Span:
    """A half-open run of base lines."""
    start: int
    end: int


@dataclass(frozen=True)
class _Versions:
    """The four texts as lines, and each side's and the resolution's alignment to base."""
    b: list[str]
    u: list[str]
    t: list[str]
    r: list[str]
    u_ops: list
    t_ops: list
    r_ops: list

    def side(self, side: Side) -> list[str]:
        return self.u if side is Side.TARGET else self.t


@dataclass(frozen=True)
class _Group:
    """Changes that touch one another, across both sides."""
    changes: tuple[_Change, ...]

    @property
    def start(self) -> int:
        return min(c.start for c in self.changes)

    @property
    def end(self) -> int:
        return max(c.end for c in self.changes)

    @property
    def sides(self) -> frozenset[Side]:
        return frozenset(c.side for c in self.changes)


def audit(
    path: str, *, base: str | None, target: str | None,
    replayed: str | None, resolved: str | None,
) -> FileAudit:
    """What the resolution of *path* lost from either side.

    ``None`` for any version means the file does not exist there. *base* is the
    replayed commit's parent: the side every change is measured from.
    """
    presence = _presence_losses(base, target, replayed, resolved)
    if presence:
        return FileAudit(path, presence)
    if resolved is None:
        return FileAudit(path)

    b, u, t, r = (_lines(text) for text in (base, target, replayed, resolved))
    if len(b) + len(u) + len(t) + len(r) > MAX_AUDITED_LINES:
        return FileAudit(path, skipped=f"over {MAX_AUDITED_LINES} lines across its versions")

    v = _Versions(b, u, t, r, *(
        SequenceMatcher(None, b, text, autojunk=False).get_opcodes() for text in (u, t, r)
    ))
    groups = _groups(_changes(v.u_ops, u, Side.TARGET) + _changes(v.t_ops, t, Side.REPLAYED))
    losses = [loss for loss in (_group_loss(group, v) for group in groups) if loss is not None]
    whole = _taken_whole(u, t, r, losses)
    return FileAudit(path, (whole,) if whole is not None else tuple(losses))


# ── Whole-file verdicts ─────────────────────────────────────────────────────

def _presence_losses(
    base: str | None, target: str | None, replayed: str | None, resolved: str | None,
) -> tuple[Loss, ...]:
    """Losses decided by which versions exist at all.

    A side that deleted the file, or added it, made a change the line audit
    cannot express. When the other side left the file alone that change was
    clean, and undoing it blocks; when the other side edited the file the
    two collided, and either outcome is a judgement call.

    Empty when every side's presence was honoured — the line audit then reads
    whatever content there is, a file added on one side included.
    """
    losses: list[Loss] = []
    for side, mine, theirs in (
        (Side.REPLAYED, replayed, target), (Side.TARGET, target, replayed),
    ):
        if mine == base:
            continue
        kept = resolved is not None
        if (mine is None) == (not kept):
            continue
        verb = "deleted the file" if mine is None else (
            "added the file" if base is None else "changed the file"
        )
        losses.append(Loss(
            LossKind.FILE_PRESENCE, side, note=verb, blocking=theirs == base,
        ))
    return tuple(losses)


def _taken_whole(
    u: list[str], t: list[str], r: list[str], losses: list[Loss],
) -> Loss | None:
    """One verdict standing for every loss, when *r* is the other side verbatim.

    Only when that side also lost a clean change. A file whose every change
    collided with the other side's, resolved wholly to the other side, is a
    run of one-sided regions — each a judgement call, and reported as such —
    not a discarded file. Requiring a reverted clean change is what tells a
    `checkout --ours` apart from a deliberate "the target's rewrite wins".
    """
    if u == t:
        return None
    for side, other in ((Side.REPLAYED, u), (Side.TARGET, t)):
        if r == other and any(
            loss.kind is LossKind.HUNK_REVERTED and loss.side is side for loss in losses
        ):
            return Loss(LossKind.FILE_TAKEN_WHOLE, side)
    return None


# ── Change grouping ─────────────────────────────────────────────────────────

def _changes(ops: list[tuple[str, int, int, int, int]], other: list[str], side: Side) -> list[_Change]:
    return [
        _Change(side, i1, i2, tuple(other[j1:j2]), j1)
        for tag, i1, i2, j1, j2 in ops if tag != "equal"
    ]


def _touches(start: int, end: int, other_start: int, other_end: int) -> bool:
    """Whether two base ranges overlap or abut — git's own test for a conflict."""
    return start <= other_end and other_start <= end


def _groups(changes: list[_Change]) -> list[_Group]:
    """*changes* partitioned into runs that touch, in base order."""
    groups: list[list[_Change]] = []
    end = -1
    for change in sorted(changes, key=lambda c: (c.start, c.end)):
        if groups and change.start <= end:
            groups[-1].append(change)
            end = max(end, change.end)
        else:
            groups.append([change])
            end = change.end
    return [_Group(tuple(g)) for g in groups]


def _clean_change(group: _Group) -> _Change | None:
    """The single change a group stands for when git would merge it unaided.

    One side alone is clean. So are both sides making the identical change,
    which git takes once — and losing it then loses the replayed commit's copy,
    so that is the side reported.
    """
    if len(group.sides) == 1 and len(group.changes) == 1:
        return group.changes[0]
    if len(group.changes) == 2:
        first, second = group.changes
        if (first.start, first.end, first.lines) == (second.start, second.end, second.lines):
            return first if first.side is Side.REPLAYED else second
    return None


def _group_loss(group: _Group, v: _Versions) -> Loss | None:
    """What the resolution lost of one group: a reverted clean change, a
    one-sided collision, or nothing."""
    clean = _clean_change(group)
    if clean is None:
        return _one_sided(group, v)
    if not _reverted(clean, v.side(clean.side), v.r, v.r_ops):
        return None
    return Loss(LossKind.HUNK_REVERTED, clean.side, clean.at + 1, _excerpt(clean, v.b))


# ── Clean changes ───────────────────────────────────────────────────────────

def _reverted(
    change: _Change, side_text: list[str], r: list[str],
    r_ops: list[tuple[str, int, int, int, int]],
) -> bool:
    """Whether *r* holds the base text where *change* was, and not the change.

    The alignment says the resolution did nothing at that spot. A line that
    repeats can make the alignment place an edit one copy over, so the verdict
    also needs the side's version of the spot — its lines with their
    surroundings — to be absent from *r*. Either test alone would misfire on
    repeated lines; together they only agree on a genuine revert.
    """
    if any(
        tag != "equal" and _touches(change.start, change.end, i1, i2)
        for tag, i1, i2, _, _ in r_ops
    ):
        return False
    before = side_text[max(0, change.at - _CONTEXT_LINES):change.at]
    after_at = change.at + len(change.lines)
    after = side_text[after_at:after_at + _CONTEXT_LINES]
    window = [*before, *change.lines, *after]
    return not window or not _contains(r, window)


def _contains(haystack: list[str], needle: list[str]) -> bool:
    """Whether *needle* occurs in *haystack* as a contiguous run of whole lines."""
    joined = "\n" + "\n".join(haystack) + "\n"
    return "\n" + "\n".join(needle) + "\n" in joined


def _excerpt(change: _Change, b: list[str]) -> tuple[str, ...]:
    """The lines a lost change added, or the ones it removed when it added none."""
    if change.lines:
        return tuple(f"+ {line}" for line in change.lines[:_EXCERPT_LINES])
    return tuple(f"- {line}" for line in b[change.start:change.end][:_EXCERPT_LINES])


# ── Colliding changes ───────────────────────────────────────────────────────

def _one_sided(group: _Group, v: _Versions) -> Loss | None:
    """An advisory loss when a colliding region resolves to one side verbatim."""
    n = len(v.b)
    span = _anchored(_Span(group.start, group.end), n, (v.u_ops, v.t_ops, v.r_ops))
    start, end = span.start, span.end
    base_text = v.b[start:end]
    mine = _project(v.t_ops, v.t, start, end, n)
    theirs = _project(v.u_ops, v.u, start, end, n)
    result = _project(v.r_ops, v.r, start, end, n)
    if mine is None or theirs is None or result is None:
        return None
    replayed_line = next(c.at for c in group.changes if c.side is Side.REPLAYED) + 1
    if result == theirs and mine != base_text:
        return Loss(LossKind.REGION_ONE_SIDED, Side.REPLAYED, replayed_line,
                    tuple(f"+ {line}" for line in mine[:_EXCERPT_LINES]), blocking=False)
    if result == mine and theirs != base_text:
        target_line = next(c.at for c in group.changes if c.side is Side.TARGET) + 1
        return Loss(LossKind.REGION_ONE_SIDED, Side.TARGET, target_line,
                    tuple(f"+ {line}" for line in theirs[:_EXCERPT_LINES]), blocking=False)
    return None


def _equal_at(ops: list, index: int) -> int | None:
    """Where base line *index* sits in the other text, when the two agree on it."""
    for tag, i1, i2, j1, _ in ops:
        if tag == "equal" and i1 <= index < i2:
            return j1 + (index - i1)
    return None


def _op_containing(ops: list, index: int) -> _Span:
    for tag, i1, i2, _, _ in ops:
        if tag != "equal" and i1 <= index < i2:
            return _Span(i1, i2)
    return _Span(index, index + 1)


def _anchored(span: _Span, n: int, all_ops: tuple[list, ...]) -> _Span:
    """*span* widened until the lines either side agree in every version.

    A region can only be cut out of each text at a line all four share, or the
    pieces compared would not be the same region.
    """
    while True:
        widened = span
        for ops in all_ops:
            widened = _widen_once(widened, n, ops)
        if widened == span:
            return span
        span = widened


def _widen_once(span: _Span, n: int, ops: list) -> _Span:
    """*span* grown, at each end, to cover the change *ops* has straddling it."""
    start, end = span.start, span.end
    if start > 0 and _equal_at(ops, start - 1) is None:
        start = _op_containing(ops, start - 1).start
    if end < n and _equal_at(ops, end) is None:
        end = _op_containing(ops, end).end
    return _Span(start, end)


def _project(ops: list, other: list[str], start: int, end: int, n: int) -> list[str] | None:
    """The run of *other* standing where base lines ``[start, end)`` were."""
    lo = 0 if start == 0 else _equal_at(ops, start - 1)
    hi = len(other) if end >= n else _equal_at(ops, end)
    if lo is None or hi is None:
        return None
    lo = lo if start == 0 else lo + 1
    return other[lo:hi] if lo <= hi else None


def _lines(text: str | None) -> list[str]:
    return [] if text is None else text.splitlines()
