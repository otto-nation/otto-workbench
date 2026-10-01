"""Conflict classification, parsing, and git-level resolution.

Pure conflict-analysis functions — prompt construction, AI invocation,
and file-level dispatch live in ``resolve_ai``.
"""
# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import core.log
from core.trail import Trail
import git.client
import git.regenerate

from . import types as rebase_types

ChunkedResolutions = rebase_types.ChunkedResolutions
ConflictBlock = rebase_types.ConflictBlock
ConflictPlan = rebase_types.ConflictPlan
ConflictStrategy = rebase_types.ConflictStrategy
ContextEcho = rebase_types.ContextEcho
DeleteSide = rebase_types.DeleteSide
EchoSide = rebase_types.EchoSide
GeneratedSignal = rebase_types.GeneratedSignal
ParseFailure = rebase_types.ParseFailure
Trim = rebase_types.Trim

MAX_ECHOED_CONTEXT_LINES = rebase_types.MAX_ECHOED_CONTEXT_LINES


# ── Constants ──────────────────────────────────────────────────────────────

RESOLVE_BEGIN = "<<<RESOLVED>>>"
RESOLVE_END = "<<<END_RESOLVED>>>"

# The two conflict markers that cannot be anything else, plus diff3's base
# marker. Each is seven of its character followed by a space and a label, a
# shape no language and no markup produces by accident.
#
# git's third marker, the bare `=======` separator, is deliberately absent. It
# is seven equals signs alone on a line, which is also how Markdown underlines
# a setext H1 — so a resolution of a `.md` conflict whose own content contains
# a seven-character heading was rejected as holding a surviving conflict
# marker. Nothing is lost by dropping it: git never writes a separator without
# an opener above it and a closer below, so any real surviving conflict is
# still caught by one of these three. A separator on its own is not evidence,
# and treating it as evidence costs a correct resolution.
_CONFLICT_MARKER_PREFIXES = ("<<<<<<< ", "||||||| ", ">>>>>>> ")

GENERATED_HEADER_PATTERNS = ("DO NOT EDIT", "@generated", "Code generated")

CONFLICT_CONTEXT_LINES = 30
CHUNKED_MIN_LINES = 200
CHUNKED_MAX_CONFLICT_RATIO = 0.5

# Lines that carry no evidence of an echo when they match. A run of blank lines
# or bare block-closers is filler both sides produce independently: a resolution
# ending in two blanks where the context also opens with two is a coincidence,
# not a model repeating what it was shown. Counting them made the guard reject
# correct resolutions in ordinary C-like and Go code, which costs a retry every
# time and trains no one to trust it.
_STRUCTURAL_LINES = frozenset({"", "}", "};", ")", ");", "]", "];", "{", "end"})


# ── Generated-file detection ──────────────────────────────────────────────

def is_generated_file(
    filepath: str, full_path: Path, cwd: str,
) -> GeneratedSignal | None:
    """Detect generated files using repo-agnostic signals.

    Returns the signal that identified the file, or None if not generated.
    """
    r = git.client.run("check-attr", "linguist-generated", "--", filepath, cwd=cwd)
    if r.ok and ": true" in r.stdout:
        return GeneratedSignal.GITATTRIBUTES

    try:
        with open(full_path) as f:
            header = "".join(f.readline() for _ in range(5))
        if any(p in header for p in GENERATED_HEADER_PATTERNS):
            return GeneratedSignal.HEADER
    except (OSError, UnicodeDecodeError):
        pass

    return None


# ── Delete conflicts ──────────────────────────────────────────────────────

def detect_delete_conflict(filepath: str, cwd: str) -> DeleteSide | None:
    """Detect modify/delete conflicts by checking git index stages.

    During a rebase, stage 2 = ours (target), stage 3 = theirs (branch commit).
    A missing stage means that side deleted the file.

    Returns:
        OURS_DELETED if target deleted the file, branch modifies it
        THEIRS_DELETED if branch commit deletes the file, target has it
        None if both stages present (normal content conflict)
    """
    r = git.client.run("ls-files", "-u", "--stage", "--", filepath, cwd=cwd)
    if not r.ok or not r.stdout.strip():
        return None

    stages = set()
    for line in r.stdout.strip().splitlines():
        # Format: <mode> <hash> <stage>\t<filepath>
        parts = line.split("\t")[0].split()
        if len(parts) >= 3 and parts[2].isdigit():
            stages.add(int(parts[2]))

    if not stages:
        return None

    if 2 not in stages:
        return DeleteSide.OURS_DELETED
    if 3 not in stages:
        return DeleteSide.THEIRS_DELETED
    return None


def resolve_delete_conflict(
    filepath: str, sha: str, cwd: str, delete_side: DeleteSide,
    *, trail: Trail | None = None,
) -> bool:
    """Resolve a modify/delete conflict by accepting the deletion."""
    if delete_side is DeleteSide.THEIRS_DELETED:
        reason = f"commit {sha} deletes this file"
    else:
        reason = f"deleted on target, commit {sha} modifies stale version"

    if trail:
        trail.decision(
            "delete_conflict",
            f"accepting deletion for {filepath}",
            reason=f"{reason} (modify/delete conflict)",
        )

    if not git.client.ok("rm", "--force", filepath, cwd=cwd):
        core.log.error(f"git rm failed for {filepath}")
        return False
    core.log.info(f"Accepted deletion: {filepath} ({reason})")
    return True


# ── Git content reads ─────────────────────────────────────────────────────

def get_ours_content(filepath: str, cwd: str) -> str | None:
    """Get the target-side (HEAD) version of a file from git index stage 2.

    During a rebase conflict, stage 2 holds the 'ours' side — the target branch
    state before the conflicting commit is applied. Returns None if unavailable
    (e.g. file is new on the branch).
    """
    r = git.client.run("show", f":2:{filepath}", cwd=cwd)
    return r.stdout if r.ok else None


def get_commit_diff(filepath: str, cwd: str) -> str | None:
    """Get the diff of REBASE_HEAD for a specific file.

    Shows what the commit being replayed intended to change, helping the AI
    understand the branch's intent separately from the conflict markers.
    """
    return git.client.out("diff", "REBASE_HEAD^", "REBASE_HEAD", "--", filepath, cwd=cwd) or None


def is_binary(path: Path) -> bool:
    """Check if a file is binary by looking for null bytes in the first 8KB."""
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except OSError:
        return False


# ── Conflict markers ─────────────────────────────────────────────────────

def has_conflict_markers(text: str) -> str | None:
    """The first unambiguous git conflict marker in *text*, or None.

    See ``_CONFLICT_MARKER_PREFIXES`` for which three are read as markers and
    why the bare ``=======`` separator is not one of them.
    """
    for line in text.splitlines():
        match = next((m for m in _CONFLICT_MARKER_PREFIXES if line.startswith(m)), None)
        if match:
            return match
    return None


def resolution_parses(stdout: str) -> bool:
    """Return True if full-file resolution output is usable."""
    return parse_resolved_content(stdout)[0] is not None


def parse_resolved_content(stdout: str) -> tuple[str | None, str]:
    """Extract resolved file content from AI output.

    Returns (resolved_content, failure_reason). failure_reason is empty on success.
    """
    begin = stdout.find(RESOLVE_BEGIN)
    end = stdout.find(RESOLVE_END)
    if begin == -1 and end == -1:
        return None, ParseFailure.MISSING_BOTH_MARKERS
    if begin == -1:
        return None, ParseFailure.MISSING_BEGIN_MARKER
    if end == -1:
        return None, ParseFailure.MISSING_END_MARKER
    if end <= begin:
        return None, ParseFailure.END_BEFORE_BEGIN

    resolved = stdout[begin + len(RESOLVE_BEGIN):end]
    if resolved.startswith("\n"):
        resolved = resolved[1:]
    if resolved.endswith("\n"):
        resolved = resolved[:-1]

    surviving = has_conflict_markers(resolved)
    if surviving:
        return None, f"{ParseFailure.SURVIVING_CONFLICT_MARKER}:{surviving.strip()}"

    return resolved + "\n", ""


# ── Chunked conflict extraction ──────────────────────────────────────────

@dataclass(frozen=True)
class _MarkerSpan:
    """The line range one conflict's markers occupy, opener through closer."""
    start: int
    end: int


def _marker_spans(lines: list[str]) -> list[_MarkerSpan]:
    """Every complete conflict region in *lines*, in file order.

    An opener with no closer after it is skipped rather than ending the scan:
    a stray ``<<<<<<< `` inside a string literal or a heredoc would otherwise
    hide every real conflict below it.
    """
    spans: list[_MarkerSpan] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            i += 1
            continue
        end = next(
            (j for j in range(i + 1, len(lines))
             if lines[j].startswith(">>>>>>> ")),
            None,
        )
        if end is None:
            i += 1
            continue
        spans.append(_MarkerSpan(start=i, end=end))
        i = end + 1
    return spans


def extract_conflict_blocks(
    content: str, context_lines: int = CONFLICT_CONTEXT_LINES,
) -> list[ConflictBlock]:
    """Extract conflict blocks with surrounding context from file content.

    Each block's context stops at its neighbours, never at a fixed offset that
    runs past one. Taking ``context_lines`` from the raw conflicted file put
    the *next* conflict — markers, both sides and all — inside block 1's
    ``context_after`` whenever two conflicts sat closer together than the
    context width, which in a file with seven hunks is every one of them. The
    chunked prompt then showed the model an envelope holding three conflicts
    under the instruction "output everything from ``<<<<<<<`` through
    ``>>>>>>>``", and got back either an answer for the whole envelope or three
    conflicts collapsed into one — the two failures that ended a rebase of this
    repo, neither of them the model's mistake.

    Clamping also makes the echo guard mean what it says: a context that cannot
    contain a conflict marker is a context whose overlap with a resolution is
    always about the resolution's boundaries, never about a second conflict the
    model was right to answer.
    """
    lines = content.splitlines(keepends=True)
    spans = _marker_spans(lines)
    blocks: list[ConflictBlock] = []
    for i, span in enumerate(spans):
        # The line after the previous conflict's closer, and the line holding
        # the next conflict's opener. Nothing outside that window belongs to
        # this block, however much context was asked for.
        floor = spans[i - 1].end + 1 if i else 0
        ceiling = spans[i + 1].start if i + 1 < len(spans) else len(lines)
        ctx_start = max(floor, span.start - context_lines)
        ctx_end = min(ceiling, span.end + 1 + context_lines)
        blocks.append(ConflictBlock(
            index=i + 1,
            start=span.start,
            end=span.end,
            conflict="".join(lines[span.start:span.end + 1]),
            context_before="".join(lines[ctx_start:span.start]),
            context_after="".join(lines[span.end + 1:ctx_end]),
        ))
    return blocks


def should_chunk(content: str, blocks: list[ConflictBlock]) -> bool:
    """Decide whether to use chunked resolution based on file size and conflict ratio."""
    total = content.count("\n") + 1
    if total <= CHUNKED_MIN_LINES:
        return False
    conflict_lines = sum(b.line_count for b in blocks)
    return conflict_lines / total < CHUNKED_MAX_CONFLICT_RATIO


def _overlap(first: list[str], second: list[str]) -> int:
    """Longest run ending *first* that also begins *second*.

    Note this is not a prefix walk from the boundary inward: the alignment
    shifts with the run's length, so the whole candidate run is compared at each
    length. Contexts are bounded at ``CONFLICT_CONTEXT_LINES``, so the quadratic
    shape costs nothing worth avoiding.
    """
    longest = 0
    for n in range(1, min(len(first), len(second)) + 1):
        if first[-n:] == second[:n]:
            longest = n
    return longest


def _substantive(lines: list[str], owned: set[str]) -> int:
    """How many of *lines* are evidence of an echo rather than coincidence.

    Discounts what the conflict region already held and what both sides produce
    independently — see ``_STRUCTURAL_LINES``.
    """
    return sum(
        1 for line in lines
        if line not in owned and line.strip() not in _STRUCTURAL_LINES
    )


def context_echo(resolution: str, block: ConflictBlock) -> ContextEcho:
    """How much of *block*'s context *resolution* repeated back, on each side.

    The chunked prompt sends each conflict wrapped in context and asks for only
    the conflict's replacement. A model that returns the context too is not
    caught by any marker check — the text parses, holds no conflict markers, and
    splices cleanly — but `splice_resolutions` replaces only the marker region,
    so every echoed line lands a second time beside the copy already in the
    file. One rebase of this repo duplicated a whole shell function that way,
    which bash resolves by silently taking the second definition.

    Lines the conflict region itself contains are not counted as evidence. A
    resolution ending with a line that was genuinely part of the conflict is
    doing its job, even when the following context happens to open with that
    same line.

    Neither are blank lines and bare block-closers, for the same reason one line
    of overlap is tolerated at all: they are filler that matches by coincidence.
    What is counted is substance — a line that says something, reproduced from
    the context on the other side of the boundary.

    The *run* each side reports is the whole overlap, discounted lines
    included, because that is what a repair has to remove: once a run is read
    as an echo, every line in it is duplicated in the spliced file, and leaving
    the coincidental ones behind leaves half a duplicate.
    """
    owned = set(block.conflict.splitlines())
    res = resolution.splitlines()
    after = block.context_after.splitlines()
    before = block.context_before.splitlines()

    # The resolution's tail against the following context's head, and the
    # preceding context's tail against the resolution's head.
    n_tail = _overlap(res, after)
    n_head = _overlap(before, res)

    return ContextEcho(
        # The *tail* of the preceding context, which is the part adjacent to the
        # conflict's start — mirroring `after`'s head being adjacent to its end.
        # Both are the lines that sit just outside what `splice_resolutions`
        # replaces, so both are the ones a resolution can duplicate.
        head=EchoSide(
            run=n_head,
            substantive=_substantive(before[len(before) - n_head:], owned),
        ),
        tail=EchoSide(run=n_tail, substantive=_substantive(after[:n_tail], owned)),
    )


def echoed_context_lines(resolution: str, block: ConflictBlock) -> int:
    """How many substantive lines of context *resolution* repeated back.

    The scalar reading of `context_echo`, kept as the thing a failure reason
    and a test assert against. Zero means nothing was echoed.
    """
    return context_echo(resolution, block).lines


def trim_echoed_context(resolution: str, block: ConflictBlock) -> Trim:
    """*resolution* with any echoed context removed from its ends.

    The repair the measurement above was already computing and throwing away.
    Rejecting an echo costs a retry with a fresh model call and, when the retry
    also echoes, the whole file; trimming costs nothing and is exact — the
    echoed run is a verbatim copy of lines that still sit beside the splice
    point, so deleting it reconstructs the answer the prompt asked for.

    Only a run read as an echo is removed. A one-line coincidence at the
    boundary is left alone, which is the same conservatism the measurement
    applies: with nothing substantive in the run there is no evidence a repair
    is warranted, and trimming a line the resolution meant to emit would break
    the code the same way the duplicate does.

    A resolution that trims to nothing is not repaired. Every line it held came
    from the context, so there is no resolution underneath the echo to recover
    — the model answered with the surroundings and nothing else, and that goes
    back to it rather than into the file.
    """
    echo = context_echo(resolution, block)
    if not echo.found:
        return Trim(text=resolution)

    head = echo.head.run if echo.head.echoed else 0
    tail = echo.tail.run if echo.tail.echoed else 0
    lines = resolution.splitlines(keepends=True)
    kept = lines[head:len(lines) - tail] if tail else lines[head:]
    if not kept:
        return Trim(text=resolution, head=head, tail=tail, ok=False)
    return Trim(text="".join(kept), head=head, tail=tail)


def _find_block_marker(stdout: str, marker: str) -> int:
    """Where *marker* appears as itself rather than as another marker's prefix.

    ``<<<RESOLVED>>>_1`` is a prefix of ``<<<RESOLVED>>>_11``, so a plain
    substring search for block 1 finds block 11 in any answer with ten or more
    blocks — and then reads block 11's resolution, or a span running backwards,
    into block 1. A match is only this marker when the character after it is
    not another digit.
    """
    start = 0
    while (found := stdout.find(marker, start)) != -1:
        after = found + len(marker)
        if after >= len(stdout) or not stdout[after].isdigit():
            return found
        start = found + 1
    return -1


def parse_chunked_resolutions(
    stdout: str, blocks: list[ConflictBlock],
) -> ChunkedResolutions:
    """Extract per-block resolutions from AI output, repairing echoed context.

    Takes the blocks rather than a count because each resolution is checked
    against the context its own block was sent with — see ``context_echo`` for
    what that catches and why no marker check reaches it, and
    ``trim_echoed_context`` for why the catch is now a repair.
    """
    resolutions = []
    repaired = 0
    for i in range(1, len(blocks) + 1):
        begin_marker = f"{RESOLVE_BEGIN}_{i}"
        end_marker = f"{RESOLVE_END}_{i}"
        begin = _find_block_marker(stdout, begin_marker)
        end = _find_block_marker(stdout, end_marker)
        if begin == -1 or end == -1 or end <= begin:
            return ChunkedResolutions(
                reason=f"{ParseFailure.MISSING_BLOCK_MARKERS}_{i}",
            )
        resolved = stdout[begin + len(begin_marker):end]
        if resolved.startswith("\n"):
            resolved = resolved[1:]
        if resolved.endswith("\n"):
            resolved = resolved[:-1]
        surviving = has_conflict_markers(resolved)
        if surviving:
            return ChunkedResolutions(reason=(
                f"{ParseFailure.SURVIVING_CONFLICT_MARKER}_in_block_{i}"
                f":{surviving.strip()}"
            ))
        trim = trim_echoed_context(resolved, blocks[i - 1])
        if not trim.ok:
            return ChunkedResolutions(
                reason=f"{ParseFailure.WHOLLY_ECHOED}_in_block_{i}",
            )
        if trim.trimmed:
            repaired += 1
        # Re-add the terminator stripped above, unless trimming already left
        # one: the trim works on whole lines and keeps their line endings, so
        # a repaired resolution comes back already terminated and appending
        # would splice a blank line in where the echo used to be.
        text = trim.text if trim.text.endswith("\n") else trim.text + "\n"
        resolutions.append(text)
    return ChunkedResolutions(resolutions=resolutions, repaired=repaired)


def splice_resolutions(
    content: str, blocks: list[ConflictBlock], resolutions: list[str],
) -> str:
    """Replace conflict markers in content with resolved text."""
    lines = content.splitlines(keepends=True)
    for block, resolution in reversed(list(zip(blocks, resolutions))):
        lines[block.start:block.end + 1] = resolution.splitlines(keepends=True)
    return "".join(lines)


# ── Git staging ───────────────────────────────────────────────────────────

def git_add(filepath: str, cwd: str) -> bool:
    """Stage a file and return True on success."""
    if git.client.ok("add", filepath, cwd=cwd):
        return True
    core.log.error(f"git add failed for {filepath}")
    return False


def accept_theirs_and_stage(filepath: str, cwd: str) -> bool:
    """Accept theirs for a generated file and stage it."""
    if not git.client.ok("checkout", "--theirs", filepath, cwd=cwd):
        core.log.error(f"git checkout --theirs failed for {filepath}")
        return False
    if not git_add(filepath, cwd):
        return False
    core.log.info(f"Accepted theirs: {filepath}")
    return True


# ── Classification ────────────────────────────────────────────────────────

def classify_conflict(filepath: str, full_path: Path, cwd: str) -> ConflictPlan:
    """Determine the resolution strategy for a conflicted file."""
    delete_side = detect_delete_conflict(filepath, cwd)
    if delete_side is not None:
        return ConflictPlan(ConflictStrategy.DELETE, delete_side=delete_side)

    regenerator = git.regenerate.find_regenerator(filepath)
    if regenerator is not None:
        return ConflictPlan(ConflictStrategy.REGENERATE, regenerator=regenerator)

    signal = is_generated_file(filepath, full_path, cwd)
    if signal is not None:
        return ConflictPlan(ConflictStrategy.ACCEPT_THEIRS, signal=signal)

    if is_binary(full_path):
        return ConflictPlan(ConflictStrategy.BINARY_ERROR)

    return ConflictPlan(ConflictStrategy.AI_MERGE)
