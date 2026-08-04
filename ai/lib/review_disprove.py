"""Disprove-it gate: adversarial falsification of review findings.

After synthesis, each Must-fix and Should-fix finding is challenged.
Findings that cannot survive scrutiny are dropped before posting.
"""

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
