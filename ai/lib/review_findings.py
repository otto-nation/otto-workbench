"""Skip annotations, diff hunks, and the mechanically merged review.

What is left to do once a review's findings exist: read the fix pass's skip
annotations off them, drop the sections that should not be posted, and assemble
the summary and verdict for a review no synthesis agent wrote. Shared between
review-orchestrate, which builds reviews, and review-post, which posts them.

Reading findings off a document is `review_document`'s job, checking them
against the tree is `review_verify`'s, what happens to them *across* reviews —
merging, renumbering, carry-forward — is `review_merge`'s, and the `Finding`
every side holds is `review_types`'.
"""

# doc-group: findings

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import log
from pr_domains import ReviewVerdict
from review_document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, ReviewDocument, counts_prose,
    verdict_from_counts,
)
from review_merge import renumber_findings, strip_stable_ids
from review_types import SEVERITIES, Finding
from review_verify import (
    reconcile_dropped_findings, strip_evidence_blocks, verify_findings,
)
from text import plural

# The fix pass's record of work it did not do. The reason is optional for the
# same reason it is on the decline annotation `review_document` owns: a skip
# written without one is still a skip, and reading it as an ordinary open
# finding is what lets a sibling finding's edit report it as fixed.
_SKIP = r"\*\(skipped(?:\s*[—–-]+\s*(.+?))?\)\*"

# Matched at the head or tail of the finding body, never in the middle: matched
# anywhere, a finding whose prose quotes the annotation — this file's own docs
# do, verbatim — would be misread as carrying it, and the fix pass would leave
# the line untouched forever rather than recording what its agent answered.
SKIP_HEAD_RE = re.compile(rf"^{_SKIP}")
SKIP_TAIL_RE = re.compile(rf"{_SKIP}\s*$")


def match_skip(finding: Finding) -> re.Match[str] | None:
    """The skip annotation a finding carries, if it carries one.

    The single owner of "did the fix pass skip this?". Everything that acts on
    a skip asks here, so the auto-check guard and the fix summary cannot come
    to different answers about the same finding — the way they did while the
    guard read only `Finding.declined`.

    A checked finding carries no skip: whatever its body says, the box says the
    work was done.
    """
    if finding.checked:
        return None
    return SKIP_HEAD_RE.match(finding.body) or SKIP_TAIL_RE.search(finding.body)


def extract_skip_reasons(findings: list[Finding]) -> None:
    """Extract skip reasons from finding body text (mutates in place)."""
    for f in findings:
        m = match_skip(f)
        if m:
            f.skip_reason = (m.group(1) or "").strip()


# ── Diff hunk parsing ────────────────────────────────────────────────────────

_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    hunks: dict[str, list[tuple[int, int]]] = {}
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
            hunks[current_file].append((start, start + length - 1))
    return hunks


# ── Section extraction ───────────────────────────────────────────────────────

_VALID_SECTION_HEADERS = (
    {s.section.lower() for s in SEVERITIES}
    | {SECTION_FILE_TRIAGE.lower(), SECTION_PRIOR_FINDINGS.lower()}
)


def _validate_group_output(output_path: str, group_name: str) -> bool:
    content = Path(output_path).read_text()
    if not content.strip():
        return True
    has_section = any(
        line.strip()[3:].strip().lower() in _VALID_SECTION_HEADERS
        for line in content.split("\n")
        if line.strip().startswith("## ")
    )
    if not has_section:
        log.warn(f"Group {group_name} output has no recognized sections — findings may be lost")
    return has_section


def strip_sections(text: str, headers: Iterable[str]) -> str:
    """Drop whole `## <header>` sections, headings included."""
    excluded = {h.lower() for h in headers}
    kept: list[str] = []
    dropping = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            dropping = stripped[3:].strip().lower() in excluded
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


# ── Mechanical verdict and review assembly ──────────────────────────────────

_MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def mechanical_verdict(counts: dict[str, int]) -> str:
    prose = counts_prose(counts)
    if not prose:
        return f"{ReviewVerdict.APPROVE.prose} — no findings {_MECHANICAL_NOTE}.\n"

    verdict = verdict_from_counts(counts)
    suffix = " only" if verdict is ReviewVerdict.APPROVE else ""
    return f"{verdict.prose} — {prose}{suffix} {_MECHANICAL_NOTE}.\n"


def post_process_findings(review_file: str, wt_path: str = "") -> dict | None:
    path = Path(review_file)
    # Guard covers all sub-steps (verify, strip, renumber, write) — callers
    # receive None rather than a partial result when the file is missing.
    if not path.exists():
        return None
    text = path.read_text()
    verification: dict | None = None
    if wt_path:
        text, verification = verify_findings(text, wt_path)
        dropped = verification["dropped"]
        if dropped:
            log.info(f"Dropped {len(dropped)} unverified findings: {', '.join(dropped)}")
    text = strip_evidence_blocks(text)
    text = strip_stable_ids(text)
    # Before renumbering: the ledger's IDs number the prior review, so leaving
    # them in would both mislead a reader and skew the renumbering of the
    # findings that are actually in this review.
    text = strip_sections(text, [SECTION_PRIOR_FINDINGS])
    text = renumber_findings(text)
    # After renumbering: the reconciliation reads the counts the finished file
    # reports, and must not describe findings by IDs renumbering has reassigned.
    if verification:
        text = reconcile_dropped_findings(text, verification)
    path.write_text(text)
    return verification


def build_mechanical_body(
    merged_content: str,
    *,
    group_count: int,
    summary_note: str,
    include_verdict: bool = True,
    file_count: int = 0,
) -> str:
    """A review body summarising `merged_content`, with no agent involved.

    The body only — the title and the metadata header above it belong to
    `review_document.ReviewDocument`, which is what every caller wraps this in.
    A caller with failures to report adds the Agent Failures section afterwards
    through `review_state.set_failures_section`, which is the same call the
    already-written review takes.
    """
    counts = ReviewDocument(body=merged_content).open_counts
    total = sum(counts.values())
    count_summary = f"{total} finding{plural(total)}" if total else "No findings"

    if file_count:
        scope = f"across {file_count} file{plural(file_count)} in {group_count} groups"
    else:
        scope = f"across {group_count} groups"

    body = (
        f"## Summary\n"
        f"{count_summary} {scope}. "
        f"{summary_note}\n\n"
        f"{merged_content}\n"
    )
    if not include_verdict:
        return body
    return f"{body}\n## Verdict\n{mechanical_verdict(counts)}"
