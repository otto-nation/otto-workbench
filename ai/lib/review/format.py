"""Diff classification, inline comments, and permalinks.

Classifies findings as inline/file-level/skipped based on the PR diff
and formats inline review comments; the body is `review.render.comment_body`'s.

Reading the diff is part of that classification, so `parse_diff_hunks` lives
here: which lines a hunk covers is the same question as whether a finding can
be posted inline against them, and asking it twice is how a comment lands on a
line the diff never touched.
"""

# doc-group: findings

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

import pr.target
from review.grammar import posted_finding_tag, sid_marker
from review.types import Finding, severity_by_key


# ── Classification constants ────────────────────────────────────────────────

CLASS_INLINE = "inline"
CLASS_FILE_LEVEL = "file_level"
CLASS_SKIPPED = "skipped"

# Why a declined finding is held out of the actionable comments. Read by
# `review.render.comment_body` to gather them into their own block, so the
# classification and the rendering agree on which findings are the adjudicated ones.
SKIP_DECLINED = "declined"

DIFF_SIDE_RIGHT = "RIGHT"


# ── Diff hunk parsing ───────────────────────────────────────────────────────

_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class HunkRange:
    """The lines of the post-image one diff hunk covers, both ends inclusive."""

    start: int
    end: int

    def contains(self, line: int) -> bool:
        """Whether `line` falls inside the hunk."""
        return self.start <= line <= self.end


def parse_diff_hunks(diff_text: str) -> dict[str, list[HunkRange]]:
    """The post-image line ranges a unified diff touches, by file path.

    A file the diff names with no hunks under it maps to an empty list rather
    than being absent, so "the diff touches this file" and "the diff touches
    this line" stay separate questions — a rename or a mode change is the first
    without being the second.
    """
    hunks: dict[str, list[HunkRange]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            current_file = m.group(1)
            hunks.setdefault(current_file, [])
            continue
        m = _DIFF_HUNK_RE.match(line)
        if m and current_file is not None:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) is not None else 1
            hunks[current_file].append(HunkRange(start, start + length - 1))
    return hunks


# ── Diff classification ────────────────────────────────────────────────────

def resolve_path(path: str, hunks: Collection[str]) -> str | None:
    """Resolve a possibly-bare filename to the one known path it names.

    `hunks` is any collection of repo-relative paths: the diff's files when
    placing a comment, the tracked tree when the evidence gate looks for the
    file a quote came from. One rule for both; the gate tries the diff's files
    before the tree, as the poster only ever sees the diff's. None when
    nothing matches, or when more than one path does.
    """
    if path in hunks:
        return path

    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    matches = [p for p in hunks if p.endswith("/" + basename) or p == basename]
    if len(matches) == 1:
        return matches[0]

    if "/" in path:
        matches = [p for p in hunks if p.endswith("/" + path)]
        if len(matches) == 1:
            return matches[0]

    return None


def _hunk_end(line: int, hunks: list[HunkRange]) -> int | None:
    """Return the end of the hunk containing line, or None."""
    for hunk in hunks:
        if hunk.contains(line):
            return hunk.end
    return None


def classify_findings(
    findings: list[Finding], diff_text: str,
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Classify findings as inline, file_level, or skipped.

    A declined finding is skipped whatever the diff says about it. It was
    considered and rejected, so posting it as an inline or file-level comment
    asks for work the review already decided against — but it is still a
    judgement worth stating, which is why it is skipped rather than dropped:
    `review.render.comment_body` gathers the skipped declines into their own
    block. This is checked first so a retry through `_reclassify_and_retry`
    cannot promote one back to inline on a diff that happens to cover its line.
    """
    hunks = parse_diff_hunks(diff_text)
    inline, file_level, skipped = [], [], []

    for f in findings:
        if f.declined:
            f.classification = CLASS_SKIPPED
            f.skip_reason = SKIP_DECLINED
            skipped.append(f)
            continue

        if not f.path:
            f.classification = CLASS_FILE_LEVEL
            f.skip_reason = "general finding (no file reference)"
            file_level.append(f)
            continue

        resolved = resolve_path(f.path, hunks)

        if resolved is None:
            f.classification = CLASS_SKIPPED
            f.skip_reason = "path not in diff"
            skipped.append(f)
            continue

        f.full_path = resolved

        sev_config = severity_by_key(f.severity)
        if sev_config.posting == "body":
            f.classification = CLASS_FILE_LEVEL
            f.skip_reason = "body-only severity"
            file_level.append(f)
            continue

        if f.line is None:
            f.classification = CLASS_FILE_LEVEL
            f.skip_reason = "no line number"
            file_level.append(f)
            continue

        hunk_e = _hunk_end(f.line, hunks[resolved])
        if hunk_e is None:
            f.classification = CLASS_FILE_LEVEL
            f.skip_reason = f"line {f.line} not in any diff hunk"
            file_level.append(f)
            continue

        if f.end_line is not None and f.end_line > hunk_e:
            f.end_line = hunk_e

        f.classification = CLASS_INLINE
        inline.append(f)

    return inline, file_level, skipped


# ── Comment formatting ──────────────────────────────────────────────────────

def format_inline_comment(f: Finding) -> dict:
    """Format a finding as a GitHub inline review comment.

    The visible tag is the record ID (`M3`, not a diff-order recount). The
    comment also carries the finding's stable ID in an HTML comment, invisible
    to the reviewer and the handle a later round uses to match a reply thread:
    record IDs restart each round, so they do not survive on their own.
    """
    head = posted_finding_tag(f.id, f.severity)
    if f.stable_id:
        head += sid_marker(f.stable_id)
    body = f"{head} {f.body}"

    comment: dict = {
        "path": f.full_path,
        "side": DIFF_SIDE_RIGHT,
        "body": body,
    }

    if f.end_line is not None and f.line is not None and f.end_line > f.line:
        comment["start_line"] = f.line
        comment["line"] = f.end_line
        comment["start_side"] = DIFF_SIDE_RIGHT
    elif f.line is not None:
        comment["line"] = f.line

    return comment


def format_inline_comments(inline: list[Finding]) -> list[dict]:
    """GitHub inline comments in path-then-line order so chunk membership is path-stable.

    Record IDs are unchanged — this only orders the comments.
    """
    return [
        format_inline_comment(f)
        for f in sorted(inline, key=lambda f: (f.full_path or f.path, f.line or 0))
    ]


# ── Permalink resolution ────────────────────────────────────────────────────

_PERMALINK_REF_RE = re.compile(
    r"(?:see |at )"
    r"`?([a-zA-Z0-9_/().:-]+\.(?:go|ts|tsx|py|sql|yaml|yml|json|md))"
    r":(\d+)(?:[-–](\d+))?`?"
)


def _build_permalink(repo: str, ref: str, m: re.Match, host: str = "") -> str:
    """Build a permalink from a source reference match, on *host*'s forge.

    ``host`` is defaulted because the empty value is meaningful rather than
    missing: ``forge_base_url`` reads it as public GitHub, which is what a
    review whose sidecar predates the field correctly means.
    """
    path, start, end = m.group(1), m.group(2), m.group(3)
    url = f"{pr.target.forge_base_url(host)}/{repo}/blob/{ref}/{path}#L{start}"
    if end:
        url += f"-L{end}"
    display = f"`{path}:{start}{f'-{end}' if end else ''}`"
    return f"[{display}]({url})"


def resolve_permalinks(
    findings: list[Finding], repo: str, diff_text: str,
    head_ref: str, base_ref: str, host: str = "",
) -> list[Finding]:
    """Convert source references to permalink URLs on *host*'s forge.

    Uses base_ref for files unchanged by the PR and head_ref for
    files that are new or modified in the PR diff.

    ``host`` comes from the review's sidecar rather than from a resolved
    target: this runs in `review-post`, which is spawned with a review file and
    never reads a remote of its own.
    """
    if not head_ref or not base_ref:
        return findings

    hunks = parse_diff_hunks(diff_text)

    def replacer(m: re.Match) -> str:
        path = m.group(1)
        resolved = resolve_path(path, hunks)
        ref = head_ref if resolved else base_ref
        return _build_permalink(repo, ref, m, host)

    for f in findings:
        f.body = _PERMALINK_REF_RE.sub(replacer, f.body)

    return findings
