"""Static analysis framework for the review pipeline.

Runs machine-checkable tools against changed files and formats violations
for inclusion in review output. Each checker is a plain function with the
signature: (changed_files: list[str], wt_path: str) -> CheckerResult | None.

A violation is work, not just a note. Each one carries an `SA<n>` id and a
checkbox, which is what lets the fix pass take them as items and write back
what it did about each, in the same spellings a finding gets: a ticked box, a
`*(skipped — …)*`, a `*(declined — …)*`, or the line left alone for work the
pass never reached. `review.fix` owns that side; what lives here is the
spelling both ends read.
"""

# doc-group: pipeline

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from review.document import SECTION_STATIC_ANALYSIS
from core.text import plural

# The prefix a static violation's id carries. It cannot be read as a finding:
# `grammar.FINDING_ID_RE` wants digits straight after the severity key, and the
# `A` in `SA1` is not one — so `ReviewDocument.parse` walks past these lines and
# the two work streams never claim each other's ids. `test_static_analysis`
# holds that, because it is a property of a pattern in another module.
STATIC_ID_PREFIX = "SA"

# One violation's line, as the fix pass reads it back to record an outcome.
# Anchored at the head — the box and the id open the line — so prose quoting an
# id later in a line is not mistaken for a declaration of one.
STATIC_ID_RE = re.compile(rf"^- \[([ x])\] \*\*\[({STATIC_ID_PREFIX}\d+)\]\*\*")


@dataclass
class StaticViolation:
    file: str
    line: int
    message: str
    context: str = ""
    # Assigned by `run_static_analysis` once every checker has answered, so the
    # numbering runs over the whole section rather than restarting per checker.
    # Empty on a violation built by hand, which is the tell that it never went
    # through that call and so is not addressable by the fix pass.
    id: str = ""

    def describe(self) -> str:
        """The one line this violation is reported under, location included.

        What the fix pass's commit body prints for it. A static violation has no
        prose body to take a first line from — the message *is* the whole claim —
        so the location has to be in the line or the row names nothing findable.
        """
        detail = f"{self.message} ({self.context})" if self.context else self.message
        return f"{self.file}:{self.line} — {detail}"


@dataclass
class CheckerResult:
    name: str
    violations: list[StaticViolation]
    files_checked: int


_WORKBENCH_LIB = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
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


def all_violations(results: list[CheckerResult]) -> list[StaticViolation]:
    """Every violation across `results`, in the order the section lists them.

    Checker by checker, and by `(file, line)` within each — which is exactly
    how `format_static_analysis` lays the section out, because it renders one
    `###` block per checker. Numbering follows this order, so the ids a reader
    sees run 1, 2, 3 down the page.

    Sorting globally instead would number across the section while the display
    groups by checker, and the moment a second checker is registered one block
    would read `SA1, SA3, SA5` and the next `SA2, SA4`. The ids would each still
    be correct and addressable; they would simply look like a numbering bug to
    everyone who opened the review.

    The reading order being the id order is also what makes the fix pass's cap
    honest: it takes the first `_MAX_STATIC_ITEMS`, which are then the ones a
    reader sees first rather than an arbitrary slice.
    """
    return [
        v for r in results
        for v in sorted(r.violations, key=lambda v: (v.file, v.line))
    ]


def run_static_analysis(changed_files: list[str], wt_path: str) -> list[CheckerResult]:
    results = []
    for checker in _CHECKERS:
        result = checker(changed_files, wt_path)
        if result is not None:
            results.append(result)
    # Numbered here rather than inside each checker: the ids run over the whole
    # section, and a checker numbering its own would restart at 1 and hand two
    # violations the same id the moment a second checker is registered.
    # `all_violations` is the section's own reading order, so the numbering a
    # reader sees is contiguous down the page.
    for n, violation in enumerate(all_violations(results), start=1):
        violation.id = f"{STATIC_ID_PREFIX}{n}"
    return results


def _violation_line(v: StaticViolation) -> str:
    """One violation as the section declares it: a box, an id, and the claim.

    The box is what the fix pass ticks and the id is what it keys its outcome
    by, so a violation with no id renders without either — it is still reported,
    and it is honestly not addressable. That is the hand-built case; everything
    through `run_static_analysis` is numbered.
    """
    entry = f"**`{v.file}:{v.line}`** — {v.message}"
    if v.context:
        entry += f" ({v.context})"
    if not v.id:
        return f"- {entry}"
    return f"- [ ] **[{v.id}]** {entry}"


def _format_checker_violations(r: CheckerResult) -> list[str]:
    file_count = len({v.file for v in r.violations})
    lines = [
        "",
        f"### {r.name}",
        "",
        f"{len(r.violations)} violation{plural(len(r.violations))} "
        f"in {file_count} of {r.files_checked} files checked",
        "",
    ]
    lines.extend(_violation_line(v) for v in all_violations([r]))
    return lines


def format_static_analysis(results: list[CheckerResult]) -> str:
    """What the Static Analysis section says about `results`, heading excluded.

    Empty when no checker ran at all — there is nothing to report, rather than
    nothing to report *yet*. The heading and where the section sits belong to
    `review.document.set_section`.
    """
    if not results:
        return ""

    violating = [r for r in results if r.violations]
    if not violating:
        return "All checks passed."

    total = sum(len(r.violations) for r in violating)
    # Collapsed by default: the violation list runs to hundreds of lines on
    # large diffs and would otherwise bury the findings above it. The summary
    # repeats the section name because the posted comment renders no heading
    # for this section — the summary line is the only label a reader sees.
    parts = [
        "<details>",
        f"<summary>{SECTION_STATIC_ANALYSIS} ({total} violation{plural(total)})</summary>",
    ]
    for r in violating:
        parts.extend(_format_checker_violations(r))
    parts.extend(["", "</details>"])
    return "\n".join(parts)
