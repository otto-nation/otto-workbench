"""Conflict classification, parsing, and git-level resolution.

Pure conflict-analysis functions — prompt construction, AI invocation,
and file-level dispatch live in ``resolve_ai``.
"""
# doc-group: platform

from __future__ import annotations

from pathlib import Path

from core import log
from core.trail import Trail
from git import client as git_client

from . import types as rebase_types

ConflictBlock = rebase_types.ConflictBlock
ConflictPlan = rebase_types.ConflictPlan
ConflictStrategy = rebase_types.ConflictStrategy
DeleteSide = rebase_types.DeleteSide
GeneratedSignal = rebase_types.GeneratedSignal
ParseFailure = rebase_types.ParseFailure


# ── Constants ──────────────────────────────────────────────────────────────

RESOLVE_BEGIN = "<<<RESOLVED>>>"
RESOLVE_END = "<<<END_RESOLVED>>>"

_CONFLICT_MARKER_PREFIXES = ("<<<<<<< ", "=======", ">>>>>>> ")

GENERATED_HEADER_PATTERNS = ("DO NOT EDIT", "@generated", "Code generated")

CONFLICT_CONTEXT_LINES = 30
CHUNKED_MIN_LINES = 200
CHUNKED_MAX_CONFLICT_RATIO = 0.5


# ── Generated-file detection ──────────────────────────────────────────────

def is_generated_file(
    filepath: str, full_path: Path, cwd: str,
) -> GeneratedSignal | None:
    """Detect generated files using repo-agnostic signals.

    Returns the signal that identified the file, or None if not generated.
    """
    r = git_client.run("check-attr", "linguist-generated", "--", filepath, cwd=cwd)
    if r.ok and ": true" in r.stdout:
        return GeneratedSignal.GITATTRIBUTES

    try:
        with open(full_path) as f:
            header = "".join(f.readline() for _ in range(5))
        if any(p in header for p in GENERATED_HEADER_PATTERNS):
            return GeneratedSignal.HEADER
    except OSError:
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
    r = git_client.run("ls-files", "-u", "--stage", "--", filepath, cwd=cwd)
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

    if not git_client.ok("rm", "--force", filepath, cwd=cwd):
        log.error(f"git rm failed for {filepath}")
        return False
    log.info(f"Accepted deletion: {filepath} ({reason})")
    return True


# ── Git content reads ─────────────────────────────────────────────────────

def get_ours_content(filepath: str, cwd: str) -> str | None:
    """Get the target-side (HEAD) version of a file from git index stage 2.

    During a rebase conflict, stage 2 holds the 'ours' side — the target branch
    state before the conflicting commit is applied. Returns None if unavailable
    (e.g. file is new on the branch).
    """
    r = git_client.run("show", f":2:{filepath}", cwd=cwd)
    return r.stdout if r.ok else None


def get_commit_diff(filepath: str, cwd: str) -> str | None:
    """Get the diff of REBASE_HEAD for a specific file.

    Shows what the commit being replayed intended to change, helping the AI
    understand the branch's intent separately from the conflict markers.
    """
    return git_client.out("diff", "REBASE_HEAD^", "REBASE_HEAD", "--", filepath, cwd=cwd) or None


def is_binary(path: Path) -> bool:
    """Check if a file is binary by looking for null bytes in the first 8KB."""
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except OSError:
        return False


# ── Conflict markers ─────────────────────────────────────────────────────

def has_conflict_markers(text: str) -> str | None:
    """Check for git conflict markers at the start of lines. Returns the marker found, or None."""
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

def extract_conflict_blocks(
    content: str, context_lines: int = CONFLICT_CONTEXT_LINES,
) -> list[ConflictBlock]:
    """Extract conflict blocks with surrounding context from file content."""
    lines = content.splitlines(keepends=True)
    blocks: list[ConflictBlock] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            i += 1
            continue
        start = i
        j = i + 1
        while j < len(lines) and not lines[j].startswith(">>>>>>> "):
            j += 1
        if j >= len(lines):
            break
        end = j
        ctx_start = max(0, start - context_lines)
        ctx_end = min(len(lines), end + 1 + context_lines)
        blocks.append(ConflictBlock(
            index=len(blocks) + 1,
            start=start,
            end=end,
            conflict="".join(lines[start:end + 1]),
            context_before="".join(lines[ctx_start:start]),
            context_after="".join(lines[end + 1:ctx_end]),
        ))
        i = end + 1
    return blocks


def should_chunk(content: str, blocks: list[ConflictBlock]) -> bool:
    """Decide whether to use chunked resolution based on file size and conflict ratio."""
    total = content.count("\n") + 1
    if total <= CHUNKED_MIN_LINES:
        return False
    conflict_lines = sum(b.line_count for b in blocks)
    return conflict_lines / total < CHUNKED_MAX_CONFLICT_RATIO


def parse_chunked_resolutions(
    stdout: str, num_blocks: int,
) -> tuple[list[str] | None, str]:
    """Extract per-block resolutions from AI output.

    Returns (list_of_resolutions, failure_reason). failure_reason is empty on success.
    """
    resolutions = []
    for i in range(1, num_blocks + 1):
        begin_marker = f"{RESOLVE_BEGIN}_{i}"
        end_marker = f"{RESOLVE_END}_{i}"
        begin = stdout.find(begin_marker)
        end = stdout.find(end_marker)
        if begin == -1 or end == -1 or end <= begin:
            return None, f"{ParseFailure.MISSING_BLOCK_MARKERS}_{i}"
        resolved = stdout[begin + len(begin_marker):end]
        if resolved.startswith("\n"):
            resolved = resolved[1:]
        if resolved.endswith("\n"):
            resolved = resolved[:-1]
        surviving = has_conflict_markers(resolved)
        if surviving:
            return None, (
                f"{ParseFailure.SURVIVING_CONFLICT_MARKER}_in_block_{i}"
                f":{surviving.strip()}"
            )
        resolutions.append(resolved + "\n")
    return resolutions, ""


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
    if git_client.ok("add", filepath, cwd=cwd):
        return True
    log.error(f"git add failed for {filepath}")
    return False


def accept_theirs_and_stage(filepath: str, parent: Path, cwd: str) -> bool:
    """Accept theirs for a generated file and stage it."""
    if not git_client.ok("checkout", "--theirs", filepath, cwd=cwd):
        log.error(f"git checkout --theirs failed for {filepath}")
        return False
    if not git_add(filepath, cwd):
        return False
    log.info(f"Accepted theirs: {filepath}")
    return True


# ── Classification ────────────────────────────────────────────────────────

def classify_conflict(
    filepath: str, full_path: Path, cwd: str,
    *, find_regenerator,
) -> ConflictPlan:
    """Determine the resolution strategy for a conflicted file.

    ``find_regenerator`` is a callable that takes a filename and returns
    a ``Regenerator`` or None — injected so this module stays at layer 6
    without pulling in config-dependent registry lookups.
    """
    delete_side = detect_delete_conflict(filepath, cwd)
    if delete_side is not None:
        return ConflictPlan(ConflictStrategy.DELETE, delete_side=delete_side)

    regenerator = find_regenerator(filepath)
    if regenerator is not None:
        return ConflictPlan(ConflictStrategy.REGENERATE, regenerator=regenerator)

    signal = is_generated_file(filepath, full_path, cwd)
    if signal is not None:
        return ConflictPlan(ConflictStrategy.ACCEPT_THEIRS, signal=signal)

    if is_binary(full_path):
        return ConflictPlan(ConflictStrategy.BINARY_ERROR)

    return ConflictPlan(ConflictStrategy.AI_MERGE)
