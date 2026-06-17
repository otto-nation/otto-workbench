"""Diff classification, renumbering for posting, comment formatting, and permalinks.

Classifies findings as inline/file-level/skipped based on the PR diff,
renumbers them for posted order, and formats as GitHub review comments.
"""

from __future__ import annotations

import re

from review_common import SEVERITIES, severity_by_key
from review_findings import Finding, parse_diff_hunks

SEVERITY_LABELS = {s.key: s.label for s in SEVERITIES}


# ── Classification constants ────────────────────────────────────────────────

CLASS_INLINE = "inline"
CLASS_FILE_LEVEL = "file_level"
CLASS_SKIPPED = "skipped"

DIFF_SIDE_RIGHT = "RIGHT"


# ── Diff classification ────────────────────────────────────────────────────

def _resolve_path(path: str, hunks: dict[str, list]) -> str | None:
    """Resolve a possibly-bare filename to a full diff path."""
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


def _hunk_end(line: int, hunks: list[tuple[int, int]]) -> int | None:
    """Return the end of the hunk containing line, or None."""
    for s, e in hunks:
        if s <= line <= e:
            return e
    return None


def classify_findings(
    findings: list[Finding], diff_text: str,
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Classify findings as inline, file_level, or skipped."""
    hunks = parse_diff_hunks(diff_text)
    inline, file_level, skipped = [], [], []

    for f in findings:
        if not f.path:
            f.classification = CLASS_FILE_LEVEL
            f.skip_reason = "general finding (no file reference)"
            file_level.append(f)
            continue

        resolved = _resolve_path(f.path, hunks)

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


# ── Renumbering ─────────────────────────────────────────────────────────────

def renumber_for_posting(
    inline: list[Finding], body: list[Finding],
) -> tuple[list[Finding], list[Finding]]:
    """Renumber findings sequentially by posted location.

    Inline findings sorted by (path, line) get IDs first,
    body findings continue the sequence. Each severity prefix
    is numbered independently.
    """
    inline_sorted = sorted(inline, key=lambda f: (f.full_path or f.path, f.line or 0))

    counters = {s.key: 1 for s in SEVERITIES}
    for f in inline_sorted:
        f.posted_id = f"{f.severity}{counters[f.severity]}"
        counters[f.severity] += 1
    for f in body:
        f.posted_id = f"{f.severity}{counters[f.severity]}"
        counters[f.severity] += 1

    return inline_sorted, body


# ── Comment formatting ──────────────────────────────────────────────────────

def format_inline_comment(f: Finding) -> dict:
    """Format a finding as a GitHub inline review comment."""
    label = f"**[{f.posted_id}] [{severity_by_key(f.severity).label}]**"
    body = f"{label} {f.body}"

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


def _format_path_ref(f: Finding) -> str:
    """Format a finding's path as a backtick-wrapped reference."""
    ref = f.path
    if f.line is not None:
        ref += f":{f.line}"
        if f.end_line is not None:
            ref += f"-{f.end_line}"
    return f"`{ref}`"


def _format_finding_line(f: Finding) -> str:
    """Format a single body finding as a markdown list item."""
    label = f"**[{f.posted_id}] [{severity_by_key(f.severity).label}]**"
    if f.path:
        return f"- {label} {_format_path_ref(f)} — {f.body}"
    return f"- {label} {f.body}"


def format_body_text(
    body_findings: list[Finding],
    has_inline: bool,
    severity_filter: set[str],
) -> str:
    """Format the review body text with body/skipped findings."""
    labels = [s.label for s in SEVERITIES if s.key in severity_filter]

    if has_inline:
        parts = [f"Have some comments marked as {', '.join(labels)}."]
    else:
        parts = [f"Review findings ({', '.join(labels)}):"]

    if not body_findings:
        return "\n".join(parts)

    parts.append("")
    if has_inline:
        parts.append("**Findings not in the diff (file-level):**")
        parts.append("")

    by_sev: dict[str, list[Finding]] = {}
    by_file: dict[str, list[Finding]] = {}

    for f in body_findings:
        config = severity_by_key(f.severity)
        if config.body_group == "by_file":
            file_key = f.path or ""
            by_file.setdefault(file_key, []).append(f)
        else:
            by_sev.setdefault(f.severity, []).append(f)

    sev_order = [s.key for s in SEVERITIES if s.body_group == "by_severity"]
    active_sevs = [s for s in sev_order if s in by_sev]
    needs_sev_headers = len(active_sevs) > 1 or bool(by_file)

    for sev in active_sevs:
        if needs_sev_headers:
            parts.append(f"### {severity_by_key(sev).section}")
            parts.append("")
        for f in by_sev[sev]:
            parts.append(_format_finding_line(f))
        parts.append("")

    if by_file:
        pathless = by_file.pop("", [])
        for file_path in sorted(by_file.keys()):
            parts.append(f"### {file_path}")
            parts.append("")
            file_findings = sorted(by_file[file_path], key=lambda f: f.line or 0)
            for f in file_findings:
                parts.append(_format_finding_line(f))
            parts.append("")
        for f in pathless:
            parts.append(_format_finding_line(f))
        if pathless:
            parts.append("")

    return "\n".join(parts).rstrip("\n")


# ── Permalink resolution ────────────────────────────────────────────────────

_PERMALINK_REF_RE = re.compile(
    r"(?:see |at )"
    r"`?([a-zA-Z0-9_/().:-]+\.(?:go|ts|tsx|py|sql|yaml|yml|json|md))"
    r":(\d+)(?:[-–](\d+))?`?"
)


def _build_permalink(repo: str, ref: str, m: re.Match) -> str:
    """Build a GitHub permalink from a source reference match."""
    path, start, end = m.group(1), m.group(2), m.group(3)
    url = f"https://github.com/{repo}/blob/{ref}/{path}#L{start}"
    if end:
        url += f"-L{end}"
    display = f"`{path}:{start}{f'-{end}' if end else ''}`"
    return f"[{display}]({url})"


def resolve_permalinks(
    findings: list[Finding], repo: str, diff_text: str,
    head_ref: str, base_ref: str,
) -> list[Finding]:
    """Convert source references to GitHub permalink URLs.

    Uses base_ref for files unchanged by the PR and head_ref for
    files that are new or modified in the PR diff.
    """
    if not head_ref or not base_ref:
        return findings

    hunks = parse_diff_hunks(diff_text)

    def replacer(m: re.Match) -> str:
        path = m.group(1)
        resolved = _resolve_path(path, hunks)
        ref = head_ref if resolved else base_ref
        return _build_permalink(repo, ref, m)

    for f in findings:
        f.body = _PERMALINK_REF_RE.sub(replacer, f.body)

    return findings
