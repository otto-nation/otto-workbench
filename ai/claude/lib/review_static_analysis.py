"""Static analysis framework for the review pipeline.

Runs machine-checkable tools against changed files and formats violations
for inclusion in review output. Each checker is a plain function with the
signature: (changed_files: list[str], wt_path: str) -> CheckerResult | None.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
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


_WORKBENCH_LIB = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _WORKBENCH_LIB not in sys.path:
    sys.path.insert(0, _WORKBENCH_LIB)

from nesting import get_checker_for_extension, get_checker_for_shebang, get_all_extensions


def _read_shebang(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.readline()
    except (OSError, UnicodeDecodeError):
        return None


def _get_nesting_checker(filepath: str, ext: str):
    checker = get_checker_for_extension(ext)
    if checker:
        return checker
    if not ext:
        shebang = _read_shebang(filepath)
        if shebang:
            return get_checker_for_shebang(shebang)
    return None


def check_nesting_depth(changed_files: list[str], wt_path: str) -> CheckerResult | None:
    all_exts = get_all_extensions()
    candidates = []
    for relpath in changed_files:
        _, ext = os.path.splitext(relpath)
        if ext in all_exts or not ext:
            candidates.append(relpath)

    if not candidates:
        return None

    violations: list[StaticViolation] = []
    files_checked = 0

    for relpath in candidates:
        abspath = os.path.join(wt_path, relpath)
        _, ext = os.path.splitext(relpath)
        checker = _get_nesting_checker(abspath, ext)
        if not checker:
            continue
        try:
            with open(abspath) as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue

        files_checked += 1
        max_depth = checker.DEFAULT_MAX_DEPTH
        file_violations = checker.check_nesting(lines, max_depth)
        for v in file_violations:
            violations.append(StaticViolation(
                file=relpath,
                line=v.line_number,
                message=f"depth {v.depth} exceeds limit {max_depth}",
                context=f"in {v.function_name}()",
            ))

    return CheckerResult(name="Nesting depth", violations=violations, files_checked=files_checked)


_CHECKERS: list[Callable[[list[str], str], CheckerResult | None]] = [
    check_nesting_depth,
]

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
