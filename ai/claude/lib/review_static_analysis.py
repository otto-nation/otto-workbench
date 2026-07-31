"""Static analysis framework for the review pipeline.

Runs machine-checkable tools against changed files and formats violations
for inclusion in review output. Each checker is a plain function with the
signature: (changed_files: list[str], wt_path: str) -> CheckerResult | None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class StaticViolation:
    file: str
    line: int
    message: str
    context: str = ""


@dataclass
class CheckerResult:
    name: str
    violations: list[StaticViolation]
    files_checked: int


_CHECKERS: list[Callable[[list[str], str], CheckerResult | None]] = []

_VERDICT_RE = re.compile(r"^## Verdict\b", re.MULTILINE)


def run_static_analysis(changed_files: list[str], wt_path: str) -> list[CheckerResult]:
    results = []
    for checker in _CHECKERS:
        result = checker(changed_files, wt_path)
        if result is not None:
            results.append(result)
    return results


def format_static_analysis(results: list[CheckerResult]) -> str:
    if not results:
        return ""

    has_violations = any(r.violations for r in results)
    if not has_violations:
        return "## Static Analysis\n\nAll checks passed."

    parts = ["## Static Analysis"]
    for r in results:
        if not r.violations:
            continue
        file_count = len({v.file for v in r.violations})
        parts.append(f"\n### {r.name}")
        parts.append(
            f"{len(r.violations)} violation{'s' if len(r.violations) != 1 else ''} "
            f"in {file_count} of {r.files_checked} files checked"
        )
        parts.append("")
        for v in r.violations:
            line = f"- **`{v.file}:{v.line}`** — {v.message}"
            if v.context:
                line += f" ({v.context})"
            parts.append(line)
    return "\n".join(parts)


def inject_static_analysis(review_text: str, section_text: str) -> str:
    if not section_text:
        return review_text
    m = _VERDICT_RE.search(review_text)
    if m:
        return review_text[:m.start()] + section_text + "\n\n" + review_text[m.start():]
    return review_text.rstrip() + "\n\n" + section_text
