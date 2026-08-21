"""Disprove-it gate: adversarial falsification of review findings.

After synthesis, each Must-fix and Should-fix finding is challenged.
Findings that cannot survive scrutiny are dropped before posting.

Every must-fix and should-fix finding quotes the code it is about. After the
review is written, that quote is checked against the file: a finding whose
evidence does not match what is on disk is dropped, and the survivors are
renumbered. Roughly a quarter of reviews drop at least one finding this way.

The synthesis agent wrote the ``## Summary`` and the ``## Verdict`` before that
check ran, so both can describe findings that are no longer in the file.
Regenerating them would cost the agent's qualitative assessment, which is the
part of a review a reader cannot reconstruct from counts. So the prose stays and
the review says what left it:

* A blockquote at the end of ``## Summary`` names each dropped finding by
  severity and path — not by ID, since renumbering has already reassigned those
  — and why it was dropped.
* ``## Verdict`` is rewritten when the surviving counts no longer support the
  stated action. A drop can only remove findings, so this only ever lowers a
  verdict: ``Request changes`` → ``Needs discussion`` → ``Approve``. A verdict
  the remaining findings still support is left exactly as written, and
  ``Disapprove`` is never touched — it means the overall approach is wrong,
  which the counts do not derive, so no drop refutes it.

Both are idempotent — a review that already carries the note is left alone, so
re-running post-processing does not stack notes or re-lower a verdict.

This lowering rule only ever revises a verdict a drop leaves unsupported. How a
verdict is decided in the first place belongs to ``ReviewVerdict``.
"""

# doc-group: findings

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class DisproveResult:
    finding_id: str
    verdict: str  # "SURVIVES" or "FALSIFIED"
    reason: str


_RESULT_RE = re.compile(
    r"^- \[([A-Z]\d+)\]\s+(SURVIVES|FALSIFIED)\s*(?:—|--)\s*(.+)",
)


def parse_disprove_output(text: str) -> list[DisproveResult]:
    results: list[DisproveResult] = []
    for line in text.splitlines():
        m = _RESULT_RE.match(line.strip())
        if m:
            results.append(DisproveResult(
                finding_id=m.group(1),
                verdict=m.group(2),
                reason=m.group(3).strip(),
            ))
    return results


_FINDING_LINE_RE = re.compile(
    r"^(- (?:\[ \] )?\*\*\[([A-Z]\d+)\]\*\*)",
)


def apply_disprove_results(
    review_text: str, results: list[DisproveResult],
) -> tuple[str, dict]:
    falsified_ids = {
        r.finding_id for r in results if r.verdict == "FALSIFIED"
    }
    reason_map = {r.finding_id: r.reason for r in results if r.verdict == "FALSIFIED"}

    if not falsified_ids:
        return review_text, {
            "total_challenged": len(results),
            "survived": len(results),
            "falsified": 0,
            "falsified_ids": [],
            "reasons": {},
        }

    lines = review_text.split("\n")
    kept: list[str] = []
    dropping = False
    dropped: list[str] = []

    for line in lines:
        m = _FINDING_LINE_RE.match(line)
        if m:
            fid = m.group(2)
            dropping = fid in falsified_ids
            dropped.extend([fid] if dropping else [])
        elif dropping and not (line.startswith("- ") or line.startswith("## ")):
            continue
        elif dropping:
            dropping = False
        if not dropping:
            kept.append(line)

    survived = len(results) - len(falsified_ids)
    return "\n".join(kept), {
        "total_challenged": len(results),
        "survived": survived,
        "falsified": len(falsified_ids),
        "falsified_ids": sorted(falsified_ids),
        "reasons": reason_map,
    }
