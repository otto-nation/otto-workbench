"""Static analysis framework for the review pipeline.

Runs machine-checkable tools against changed files and formats violations
for inclusion in review output. Each checker is a plain function with the
signature: (changed_files: list[str], wt_path: str) -> CheckerResult | None.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
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


_WORKBENCH_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _WORKBENCH_LIB not in sys.path:
    sys.path.insert(0, _WORKBENCH_LIB)

try:
    from nesting import get_checker_for_extension, get_checker_for_shebang, get_all_extensions
    _NESTING_AVAILABLE = True
except ImportError:
    _NESTING_AVAILABLE = False


def _read_shebang(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.readline()
    except OSError:
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


def _check_file_nesting(relpath: str, wt_path: str) -> tuple[bool, list[StaticViolation]]:
    abspath = os.path.join(wt_path, relpath)
    _, ext = os.path.splitext(relpath)
    checker = _get_nesting_checker(abspath, ext)
    if not checker:
        return False, []
    try:
        with open(abspath) as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return False, []

    max_depth = checker.DEFAULT_MAX_DEPTH
    file_violations = checker.check_nesting(lines, max_depth)
    violations = []
    for v in file_violations:
        fn = v.function_name
        ctx = f"in {fn}" if fn.startswith("(") else f"in {fn}()"
        violations.append(StaticViolation(
            file=relpath,
            line=v.line_number,
            message=f"depth {v.depth} exceeds limit {max_depth}",
            context=ctx,
        ))
    return True, violations


def check_nesting_depth(changed_files: list[str], wt_path: str) -> CheckerResult | None:
    if not _NESTING_AVAILABLE:
        return None
    all_exts = get_all_extensions()
    candidates = [
        rp for rp in changed_files
        if os.path.splitext(rp)[1] in all_exts or not os.path.splitext(rp)[1]
    ]
    if not candidates:
        return None

    violations: list[StaticViolation] = []
    files_checked = 0
    for relpath in candidates:
        checked, file_viols = _check_file_nesting(relpath, wt_path)
        if checked:
            files_checked += 1
            violations.extend(file_viols)

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


def _plural(n: int) -> str:
    return "s" if n != 1 else ""


def _format_checker_violations(r: CheckerResult) -> list[str]:
    file_count = len({v.file for v in r.violations})
    lines = [
        "",
        f"### {r.name}",
        "",
        f"{len(r.violations)} violation{_plural(len(r.violations))} "
        f"in {file_count} of {r.files_checked} files checked",
        "",
    ]
    for v in r.violations:
        entry = f"- **`{v.file}:{v.line}`** — {v.message}"
        if v.context:
            entry += f" ({v.context})"
        lines.append(entry)
    return lines


def format_static_analysis(results: list[CheckerResult]) -> str:
    if not results:
        return ""

    violating = [r for r in results if r.violations]
    if not violating:
        return "## Static Analysis\n\nAll checks passed."

    total = sum(len(r.violations) for r in violating)
    # Collapsed by default: the violation list runs to hundreds of lines on
    # large diffs and would otherwise bury the findings above it.
    parts = [
        "## Static Analysis",
        "",
        "<details>",
        f"<summary>Static Analysis ({total} violation{_plural(total)})</summary>",
    ]
    for r in violating:
        parts.extend(_format_checker_violations(r))
    parts.extend(["", "</details>"])
    return "\n".join(parts)


def inject_static_analysis(review_text: str, section_text: str) -> str:
    if not section_text:
        return review_text
    m = _VERDICT_RE.search(review_text)
    if m:
        return review_text[:m.start()] + section_text + "\n\n" + review_text[m.start():]
    return review_text.rstrip() + "\n\n" + section_text
