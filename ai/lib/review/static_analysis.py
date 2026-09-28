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

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from review.document import SECTION_STATIC_ANALYSIS
from core.text import plural
from git import client as git_client

# Line numbers this branch added, per path. `None` anywhere this is accepted
# means the diff could not be read, which is not the same as a branch that
# added nothing — the two would otherwise both read as an empty set and silence
# every violation.
AddedLines = dict[str, frozenset[int]]

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

    @property
    def site(self) -> str:
        """What this violation is about, across rounds: a file and a function.

        `SA<n>` is a position in one rendering and means nothing in the next —
        fix the first of two violations and the survivor inherits `SA1`. So an
        outcome recorded against an id cannot be read back a round later, and
        anything that has to persist is keyed on this instead.

        Deliberately not the line number. A line moves whenever anything above
        it does, so keying on it would lose the identity on an unrelated edit,
        which is the failure mode this exists to avoid. A function is the unit
        the fix acts on anyway — flattening is per function — so two violations
        sharing a site are one piece of work and *should* share an identity.

        Hashed rather than stored whole so the value is a fixed width in a
        sidecar and carries no path for a reader to be tempted to parse.
        """
        return hashlib.sha256(
            f"{self.file}\x00{self.context}".encode()
        ).hexdigest()[:12]


@dataclass
class CheckerResult:
    name: str
    violations: list[StaticViolation]
    files_checked: int
    # Whether these violations are known to sit on lines the branch added.
    # False is "the whole file was measured", which is a fair report and not a
    # work list: `review.fix._static_items` takes nothing from an unscoped
    # result rather than asking an agent to flatten code the branch never
    # touched. Defaults False so a result built anywhere but `run_static_
    # analysis` — a test, a future caller — is treated as the unscoped kind.
    scoped_to_added: bool = False


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


def check_nesting_depth(
    changed_files: list[str], wt_path: str, added: AddedLines | None = None,
) -> CheckerResult | None:
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
            violations.extend(_only_added(file_viols, relpath, added))

    return CheckerResult(
        name="Nesting depth", violations=violations,
        files_checked=files_checked, scoped_to_added=added is not None,
    )


def _only_added(
    violations: list[StaticViolation], relpath: str, added: AddedLines | None,
) -> list[StaticViolation]:
    """`violations` narrowed to lines this branch added, when that is known.

    A review answers for the lines it adds, not for the state of the repository
    it lands in — the same rule `git/hooks/pre-push` states for the gate, and
    for a stronger reason here. Reporting pre-existing depth was a note a reader
    could weigh; handing it to the fix pass is instructing an agent to flatten
    code the branch never touched, and `fix.scope` cannot refuse it because the
    file *is* a branch file. That is debt the branch did not create and cannot
    be asked to clear, arriving as a commit.

    `None` is "the diff could not be read", which falls back to the whole file.
    That is the conservative direction for a *report* and the wrong one for
    work, so `review.fix` refuses to take items from an unfiltered result — see
    `_static_items`. Here the violations are still produced, because a reader
    losing the section entirely is worse than a reader seeing more of it.
    """
    if added is None:
        return violations
    lines = added.get(relpath, frozenset())
    return [v for v in violations if v.line in lines]


_CHECKERS: list[Callable[..., CheckerResult | None]] = [
    check_nesting_depth,
]

# One `@@` hunk header's new-file start and length.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))?")


def added_lines(wt_path: str, base: str) -> AddedLines | None:
    """The line numbers `base...HEAD` adds, per path, or None if unreadable.

    Three dots: the branch's own commits, measured from the merge base, so a
    base that has moved underneath does not read as the branch's work. The same
    range `gh.pr_reads` counts its numstat over.

    None rather than an empty mapping when git cannot answer — an unresolvable
    base, a read that failed. A caller that cannot tell the difference would
    silence every violation on the branch and call the result clean.
    """
    result = git_client.run(
        "diff", "--unified=0", f"{base}...HEAD", cwd=wt_path,
    )
    if result.returncode != 0:
        return None
    return _parse_added(result.stdout)


def _parse_added(diff: str) -> AddedLines:
    """Added line numbers per path, read off a unified diff.

    `--unified=0` means every `+` line inside a hunk is an addition and the
    hunk header's start is that run's first line, so no context has to be
    counted past. A deletion carries no new-file line and is skipped.
    """
    found: dict[str, set[int]] = {}
    path, line_no = "", 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("+++ /dev/null"):
            path = ""
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        # `+++` is matched above, so a `+` here is a content line.
        if path and line.startswith("+"):
            found.setdefault(path, set()).add(line_no)
            line_no += 1
    return {p: frozenset(ns) for p, ns in found.items()}


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


def run_static_analysis(
    changed_files: list[str], wt_path: str, added: AddedLines | None = None,
) -> list[CheckerResult]:
    """Every checker's verdict on `changed_files`, numbered for the fix pass.

    `added` narrows each checker to the lines the branch added; omitted, the
    whole of every changed file is measured and the result carries violations
    the branch did not cause. `review.fix` will not take work from such a
    result — the report is still worth writing, the commit is not.
    """
    results = []
    for checker in _CHECKERS:
        result = checker(changed_files, wt_path, added)
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


def _violation_line(v: StaticViolation, declined: dict[str, str] | None = None) -> str:
    """One violation as the section declares it: a box, an id, and the claim.

    The box is what the fix pass ticks and the id is what it keys its outcome
    by, so a violation with no id renders without either — it is still reported,
    and it is honestly not addressable. That is the hand-built case; everything
    through `run_static_analysis` is numbered.

    A site an earlier round adjudicated carries that verdict rather than
    presenting as fresh work. The scan cannot stop reporting it — the depth is
    still there, which is the point of a decline — so without this the reader
    sees an untouched violation every round and no sign anyone decided
    anything.
    """
    entry = f"**`{v.file}:{v.line}`** — {v.message}"
    if v.context:
        entry += f" ({v.context})"
    if not v.id:
        return f"- {entry}"
    reason = (declined or {}).get(v.site)
    if reason:
        return f"- [ ] **[{v.id}]** {entry} *(declined — {reason})*"
    return f"- [ ] **[{v.id}]** {entry}"


def _format_checker_violations(
    r: CheckerResult, declined: dict[str, str] | None = None,
) -> list[str]:
    file_count = len({v.file for v in r.violations})
    lines = [
        "",
        f"### {r.name}",
        "",
        f"{len(r.violations)} violation{plural(len(r.violations))} "
        f"in {file_count} of {r.files_checked} files checked",
        "",
    ]
    lines.extend(_violation_line(v, declined) for v in all_violations([r]))
    return lines


def format_static_analysis(
    results: list[CheckerResult], declined: dict[str, str] | None = None,
) -> str:
    """What the Static Analysis section says about `results`, heading excluded.

    Empty when no checker ran at all — there is nothing to report, rather than
    nothing to report *yet*. The heading and where the section sits belong to
    `review.document.set_section`.

    `declined` is what earlier rounds adjudicated, by site, so a violation
    somebody already ruled on says so instead of reading as new.
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
        parts.extend(_format_checker_violations(r, declined))
    parts.extend(["", "</details>"])
    return "\n".join(parts)
