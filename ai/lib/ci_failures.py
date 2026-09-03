#!/usr/bin/env python3
"""CI failure lifecycle tracking.

Handles failure classification, progression tracking, and rendering for the
ci-failures skill. State persistence is delegated to pr_domains.CIDomain.
"""

# doc-group: pr-state

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import git_client

if TYPE_CHECKING:
    from pr_domains import CIDomain


# ── Enums ──────────────────────────────────────────────────────────────────

class FailureKind(Enum):
    LINT = "lint"
    TEST = "test"
    BUILD = "build"
    INFRA = "infra"
    FLAKY = "flaky"  # user-override only; not auto-detected by classify_job


class Outcome(Enum):
    NEW = "new"
    PERSISTING = "persisting"
    REGRESSED = "regressed"
    RESOLVED = "resolved"
    FIXED = "fixed"


# ── Constants ──────────────────────────────────────────────────────────────

_MAX_CONTEXT_CHARS = 4000
_CONTEXT_LINES = 80

JOB_PATTERNS: list[tuple[re.Pattern, FailureKind]] = [
    (re.compile(r"shellcheck|eslint|flake8|pylint|yamllint|lint|stylelint|rubocop", re.IGNORECASE), FailureKind.LINT),
    (re.compile(r"pytest|bats|jest|mocha|rspec|test|spec|vitest|unittest", re.IGNORECASE), FailureKind.TEST),
    (re.compile(r"build|compile|docker|bundle|webpack|vite|gradle|maven|cargo", re.IGNORECASE), FailureKind.BUILD),
]

INFRA_SIGNATURES: list[str] = [
    "rate limit",
    "timeout",
    "timed out",
    "connection refused",
    "network error",
    "oom",
    "out of memory",
    "no space left on device",
    "resource temporarily unavailable",
    "could not resolve host",
    "socket hang up",
    "econnreset",
    "429 too many requests",
]


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FailureItem:
    id: str
    annotation: str
    file: str | None
    line: int | None
    diagnosis: str | None
    fix_sha: str | None
    outcome: Outcome | None
    headline: str | None = None
    source_run_id: int | None = None
    context: str | None = None


@dataclass(frozen=True)
class FailureGroup:
    job: str
    kind: FailureKind
    items: tuple[FailureItem, ...]
    failed_step: str | None = None


@dataclass
class RunState:
    run_id: int
    run_number: int
    head_sha: str
    status: str
    conclusion: str
    fetched_at: str
    failures: dict[str, FailureGroup]


# ── Classification ─────────────────────────────────────────────────────────

def classify_job(job_name: str, annotations: list[str]) -> FailureKind:
    """Classify a CI job by name pattern, with infra override from annotations."""
    for annotation in annotations:
        if any(sig in annotation.lower() for sig in INFRA_SIGNATURES):
            return FailureKind.INFRA

    for pattern, kind in JOB_PATTERNS:
        if pattern.search(job_name):
            return kind

    return FailureKind.BUILD


# ── Log extraction ────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
_JOB_STEP_PREFIX_RE = re.compile(r"^[^\t]*\t[^\t]*\t(?=\d{4}-\d{2}-\d{2}T)")

_MAX_HEADLINE_LEN = 200


@dataclass(frozen=True)
class LogMarker:
    """Structural marker for finding error sections in CI logs.

    Matches output formats (file:line:col:, ##[error], --- FAIL:), not
    specific error messages. Extensible by adding entries to LOG_MARKERS.
    """
    name: str
    pattern: re.Pattern
    kind: FailureKind
    before: int = 10
    after: int = 30


# The TAP section below parses the very lines this marker finds, so both use
# one pattern — two spellings of "is this a failure line" drift apart, and a
# line only one of them accepts is a failure that reaches the window but never
# becomes an item. The description is optional because TAP allows `not ok 5`
# with nothing after the number.
_TAP_FAIL_RE = re.compile(r"^not ok (\d+)(.*)$")

LOG_MARKERS: list[LogMarker] = [
    LogMarker("tap-fail", _TAP_FAIL_RE, FailureKind.TEST, before=0, after=10),
    LogMarker("go-test-fail", re.compile(r"--- FAIL:"), FailureKind.TEST),
    LogMarker("go-pkg-fail", re.compile(r"^FAIL\s+"), FailureKind.TEST),
    LogMarker("go-testsum-fail", re.compile(r"=== FAIL"), FailureKind.TEST),
    LogMarker("assertion-error", re.compile(r"AssertionError|AssertError|assert .* ==", re.IGNORECASE), FailureKind.TEST),
    LogMarker("go-compiler", re.compile(r"\S+\.go:\d+:\d+:"), FailureKind.BUILD, before=2, after=30),
    LogMarker("gha-error", re.compile(r"##\[error\]"), FailureKind.BUILD, before=2, after=10),
    LogMarker("python-traceback", re.compile(r"^Traceback \(most recent call last\)"), FailureKind.TEST, before=0, after=30),
    LogMarker("ts-error", re.compile(r"error TS\d+:"), FailureKind.BUILD, before=2, after=20),
    LogMarker("error-prefix", re.compile(r"^error:", re.IGNORECASE), FailureKind.BUILD),
    LogMarker("fatal-prefix", re.compile(r"^fatal:", re.IGNORECASE), FailureKind.BUILD),
    LogMarker("test-failed", re.compile(r"FAILED", re.IGNORECASE), FailureKind.TEST),
    LogMarker("codegen-drift", re.compile(r"locally and commit"), FailureKind.BUILD, before=5, after=20),
    LogMarker("diff-stat-summary", re.compile(r"\d+ files? changed"), FailureKind.BUILD, before=30, after=5),
    LogMarker("service-error", re.compile(r"\[ERROR\]|\bERROR:"), FailureKind.TEST, before=5, after=20),
]

_HEADLINE_EXTRA: list[re.Pattern] = [
    re.compile(r"(?:Error|FAILED|panic):", re.IGNORECASE),
]

_HEADLINE_INDICATORS: list[re.Pattern] = [
    m.pattern for m in LOG_MARKERS
] + _HEADLINE_EXTRA


def _match_headline(line: str) -> bool:
    return any(indicator.search(line) for indicator in _HEADLINE_INDICATORS)


def extract_headline(context: str | None, max_len: int = _MAX_HEADLINE_LEN) -> str | None:
    """Find the most informative error line from extracted log context.

    Scans line-by-line for structural indicators (compiler output, error
    prefixes, test failure markers). Returns the raw line — no rewriting.
    """
    if not context:
        return None
    for line in context.splitlines():
        stripped = line.strip()
        if not stripped or not _match_headline(stripped):
            continue
        if stripped.startswith("##[error]"):
            stripped = stripped[len("##[error]"):]
        return stripped[:max_len]
    return None


def _strip_timestamps(text: str) -> str:
    """Remove GitHub Actions line prefixes and ANSI escapes from log lines.

    `gh run view --log-failed` prefixes every line with `<job>\\t<step>\\t`
    ahead of the timestamp the jobs API emits alone. Both forms reach the
    extractors here, and every marker below anchors on `^`, so the prefixed
    form matches none of them unless it is normalised to the other. The
    lookahead means only a prefix followed by a timestamp is taken — a log line
    that merely contains tabs, such as Go's `FAIL\\tpkg\\t0.1s`, is left alone.
    """
    return "\n".join(
        _ANSI_RE.sub("", _TIMESTAMP_RE.sub("", _JOB_STEP_PREFIX_RE.sub("", line)))
        for line in text.splitlines()
    )


# ── TAP ────────────────────────────────────────────────────────────────────

_TAP_DIAGNOSTIC_RE = re.compile(r"^#")
_TAP_TEST_FILE_RE = re.compile(r"\bin test file (.+?), line (\d+)")
_TAP_TIMING_RE = re.compile(r" in \d+m?s$")


@dataclass(frozen=True)
class SourceLocation:
    """Where in the source a failure happened, as the log itself reported it."""
    file: str
    line: int


@dataclass(frozen=True)
class TestFailure:
    """One failing test: its name, where it failed, and the log block saying so."""
    name: str
    location: SourceLocation | None
    context: str


def _tap_failures(lines: list[str]) -> list[TestFailure]:
    """Every `not ok` in already-cleaned TAP output, with its diagnostic block.

    A failure's block is the `not ok` line plus the `#` continuation lines
    under it, which is where bats writes the location and the assertion that
    failed. The location is the *last* `in test file` in the block: a failure
    inside a helper reports the helper first and the test file second, and it
    is the test file the reader has to open. A failure TAP left undescribed
    falls back to its number, so it still gets an item of its own rather than
    being dropped from a run whose other failures were named.
    """
    failures: list[TestFailure] = []
    for i, line in enumerate(lines):
        match = _TAP_FAIL_RE.match(line)
        if not match:
            continue
        number, description = match.groups()
        end = i + 1
        while end < len(lines) and _TAP_DIAGNOSTIC_RE.match(lines[end]):
            end += 1
        located = _TAP_TEST_FILE_RE.findall("\n".join(lines[i + 1:end]))
        failures.append(TestFailure(
            name=_TAP_TIMING_RE.sub("", description.strip()) or f"test {number}",
            location=SourceLocation(located[-1][0], int(located[-1][1])) if located else None,
            context="\n".join(lines[i:end]),
        ))
    return failures


# ── pytest ─────────────────────────────────────────────────────────────────

_PYTEST_SUMMARY_HEADER_RE = re.compile(r"^=+ short test summary info =+$")
_PYTEST_FAILED_RE = re.compile(r"^FAILED ([^\s:]+)::(\S+)(?: - (.*))?$")
# The title must carry a character that is neither a space nor an underscore,
# which is what tells a heading apart from the `_ _ _ _` rule pytest draws
# between the frames *inside* one block. A pattern that accepts the rule as a
# heading ends the block at the outermost frame, and the failure is then
# reported at the line that made the call rather than the line that raised.
_PYTEST_SECTION_HEAD_RE = re.compile(r"^_+ (\S*[^\s_]\S*) _+$")
_PYTEST_RULE_RE = re.compile(r"^=+ .* =+$")
# A frame that is not the innermost carries nothing after its colon, so what
# follows the line number is a space or the end of the line. Requiring the space
# alone drops those frames from any log whose trailing whitespace was stripped
# in transit, and the innermost frame is then the only one a block can offer.
_PYTEST_FRAME_RE = re.compile(r"^(\S+):(\d+):(?:\s|$)")
_PYTEST_CAPTURED_RE = re.compile(r"^-+ Captured \w+ \w+ -+$")


@dataclass(frozen=True)
class _SummaryEntry:
    """One `FAILED <path>::<test>` line of pytest's short test summary."""
    path: str
    tail: str
    reason: str


def _pytest_summary(lines: list[str]) -> list[_SummaryEntry]:
    """The `FAILED` lines of the short test summary, which names every failure.

    The summary is the authoritative list rather than the traceback sections
    below: pytest prints a line here for every failing test, including one
    whose failure produced no section of its own, so a run's item count is
    taken from what it said failed.
    """
    entries: list[_SummaryEntry] = []
    in_summary = False
    for line in lines:
        if _PYTEST_SUMMARY_HEADER_RE.match(line):
            in_summary = True
            continue
        if not in_summary:
            continue
        if _PYTEST_RULE_RE.match(line):
            break
        match = _PYTEST_FAILED_RE.match(line)
        if match:
            path, tail, reason = match.groups()
            entries.append(_SummaryEntry(path, tail, reason or ""))
    return entries


def _pytest_sections(lines: list[str]) -> dict[str, list[str]]:
    """Each per-test block of pytest's FAILURES report, keyed by its heading.

    A block runs from its underscore-ruled heading to the next heading or the
    next `=` rule, and stops at the first `Captured stdout/stderr` separator.
    The traceback above that separator is what locates the failure, and a run
    of ten failures whose captured output is carried in full truncates at
    `_MAX_CONTEXT_CHARS` before the later failures are reached at all.
    """
    sections: dict[str, list[str]] = {}
    body: list[str] | None = None
    for line in lines:
        head = _PYTEST_SECTION_HEAD_RE.match(line)
        if head:
            body = sections.setdefault(head.group(1), [])
        elif body is None:
            continue
        elif _PYTEST_RULE_RE.match(line) or _PYTEST_CAPTURED_RE.match(line):
            body = None
        else:
            body.append(line)
    return sections


def _pytest_failures(lines: list[str]) -> list[TestFailure]:
    """Every failure the short test summary names, located by its own traceback.

    The path comes from the summary's node id and the line from the last
    traceback frame in that test's block *citing that same path*: a failure
    raised inside a helper or a fixture reports the helper's frame too, and it
    is the test file the reader has to open. Same rule `_tap_failures` applies
    to a bats failure reported through a helper, which is why neither takes
    simply the last location in the block.

    A test whose block carries no frame in its own file keeps `location=None`
    rather than borrowing another file's, and the caller then keys the item on
    the test name — what it already does for an unlocated TAP failure.

    Each context leads with the summary line, because a pytest block opens on
    the failing statement and names the test only in the heading above it. A
    reader — and `extract_headline`, which takes the first line — otherwise gets
    the exception with nothing saying which test raised it. TAP needs no such
    line: its `not ok` marker carries the test name already.
    """
    summary = _pytest_summary(lines)
    if not summary:
        return []
    sections = _pytest_sections(lines)
    failures: list[TestFailure] = []
    for entry in summary:
        body = sections.get(entry.tail.replace("::", "."), [])
        frames = [
            match for match in (_PYTEST_FRAME_RE.match(line) for line in body)
            if match and match.group(1) == entry.path
        ]
        header = f"FAILED {entry.path}::{entry.tail}"
        failures.append(TestFailure(
            name=entry.tail,
            location=SourceLocation(entry.path, int(frames[-1].group(2))) if frames else None,
            context="\n".join([f"{header} - {entry.reason}" if entry.reason else header, *body]).strip(),
        ))
    return failures


# ── Test log formats ───────────────────────────────────────────────────────

# Every parser reads the same cleaned lines and answers with the same type, so
# a caller asks once and never learns which suite wrote the log. A third format
# joins by being added here rather than by a second call site learning of it.
_TEST_LOG_FORMATS = (_tap_failures, _pytest_failures)


def extract_test_failures(log_text: str) -> tuple[TestFailure, ...]:
    """The failing tests a log reports, in the order it reported them.

    Each known test-output format is asked in turn and the first to answer
    wins. Empty for a log in none of them, which is what lets a caller ask
    every test job and act only on the ones that answer.
    """
    lines = _strip_timestamps(log_text).splitlines()
    for parse in _TEST_LOG_FORMATS:
        failures = parse(lines)
        if failures:
            return tuple(failures)
    return ()


def extract_failure_context(log_text: str, kind: FailureKind) -> str:
    """Extract the relevant failure section from raw job logs.

    Returns a truncated string with the failure context, or the last
    _CONTEXT_LINES lines if no markers are found.
    """
    if not log_text:
        return ""

    clean = _strip_timestamps(log_text)
    lines = clean.splitlines()

    if kind == FailureKind.TEST:
        context = _extract_test_context(lines)
        if context:
            return context[:_MAX_CONTEXT_CHARS]

    for marker in LOG_MARKERS:
        context = _extract_around_marker(lines, marker.pattern, marker.before, marker.after)
        if context:
            return context[:_MAX_CONTEXT_CHARS]

    tail = "\n".join(lines[-_CONTEXT_LINES:])
    return tail[:_MAX_CONTEXT_CHARS]


def _extract_test_context(lines: list[str]) -> str:
    """Extract test failure output — captures from first failure marker to summary.

    A log in a format `_TEST_LOG_FORMATS` parses is handled first and per
    failure. The window below spans the first marker to the last, so a suite
    whose failures are thousands of lines apart truncates at
    `_MAX_CONTEXT_CHARS` and loses every failure after the first — joining the
    blocks instead keeps all of them.
    """
    for parse in _TEST_LOG_FORMATS:
        parsed = parse(lines)
        if parsed:
            return "\n\n".join(failure.context for failure in parsed)

    fail_indices = []
    for i, line in enumerate(lines):
        if re.match(r"--- FAIL:", line) or re.match(r"FAIL\s+", line) or re.match(r"=== FAIL", line):
            fail_indices.append(i)
        elif "FAILED" in line.upper() and ("assert" in line.lower() or "error" in line.lower()):
            fail_indices.append(i)

    if not fail_indices:
        return ""

    start = max(0, fail_indices[0] - 5)
    end = min(len(lines), fail_indices[-1] + 20)
    return "\n".join(lines[start:end])


def _extract_around_marker(lines: list[str], marker: re.Pattern, before: int = 10, after: int = 30) -> str:
    """Extract context around the first line matching marker."""
    for i, line in enumerate(lines):
        if marker.search(line):
            start = max(0, i - before)
            end = min(len(lines), i + after)
            return "\n".join(lines[start:end])
    return ""


# ── Progression ────────────────────────────────────────────────────────────

def collect_item_ids(failures: dict[str, FailureGroup]) -> dict[str, FailureItem]:
    """Collect all failure items indexed by ID."""
    result: dict[str, FailureItem] = {}
    for group in failures.values():
        for item in group.items:
            result[item.id] = item
    return result


def compute_progression(
    current_failures: dict[str, FailureGroup],
    prior_failures: dict[str, FailureGroup],
) -> dict[str, Outcome]:
    """Compare current vs prior failures and assign outcomes.

    Returns a mapping of current item IDs to their progression outcome.
    Resolved items (in prior but not current) are not included.
    """
    current_items = collect_item_ids(current_failures)
    prior_items = collect_item_ids(prior_failures)

    result: dict[str, Outcome] = {}
    for item_id in current_items:
        prior_item = prior_items.get(item_id)
        if prior_item is None:
            result[item_id] = Outcome.NEW
        elif prior_item.outcome == Outcome.FIXED:
            result[item_id] = Outcome.REGRESSED
        else:
            result[item_id] = Outcome.PERSISTING

    return result



# ── State sync ─────────────────────────────────────────────────────────────

def _carry_forward_item(
    item: FailureItem, prior_items: dict[str, FailureItem],
) -> FailureItem:
    """Carry forward diagnosis/fix_sha from a prior run's matching item."""
    prior = prior_items.get(item.id)
    if not prior or not (prior.diagnosis or prior.fix_sha):
        return item
    return FailureItem(
        id=item.id,
        annotation=item.annotation,
        file=item.file,
        line=item.line,
        diagnosis=prior.diagnosis if item.diagnosis is None else item.diagnosis,
        fix_sha=prior.fix_sha if item.fix_sha is None else item.fix_sha,
        outcome=item.outcome,
        headline=item.headline,
        source_run_id=item.source_run_id,
        context=item.context,
    )


def sync_ci_domain(domain, run: RunState):
    """Merge a new run into a CIDomain, preserving prior diagnosis and fix history.

    Accepts a pr_domains.CIDomain and returns the updated CIDomain.
    If a failure item existed in the prior run with a diagnosis or fix_sha,
    those values carry forward to the new run's matching item.
    """
    prior_run = domain.runs.get(domain.latest_run_id) if domain.latest_run_id is not None else None
    prior_items = collect_item_ids(prior_run.failures) if prior_run else {}

    synced_failures: dict[str, FailureGroup] = {}
    for group_key, group in run.failures.items():
        synced_items = [_carry_forward_item(item, prior_items) for item in group.items]
        synced_failures[group_key] = FailureGroup(
            job=group.job, kind=group.kind,
            items=tuple(synced_items),
            failed_step=group.failed_step,
        )

    synced_run = RunState(
        run_id=run.run_id, run_number=run.run_number,
        head_sha=run.head_sha, status=run.status,
        conclusion=run.conclusion, fetched_at=run.fetched_at,
        failures=synced_failures,
    )

    domain.runs[run.run_id] = synced_run
    domain.latest_run_id = run.run_id

    # Prune old runs to bound state file size
    _MAX_RUNS = 10
    if len(domain.runs) > _MAX_RUNS:
        oldest_ids = sorted(domain.runs)[:len(domain.runs) - _MAX_RUNS]
        for old_id in oldest_ids:
            del domain.runs[old_id]

    return domain


# ── Dashboard ──────────────────────────────────────────────────────────────

_MAX_DASHBOARD_HEADLINES = 5
_MAX_DASHBOARD_ANNOTATION = 120


def render_dashboard(
    run: RunState,
    progression: dict[str, Outcome],
    run_ids: list[int] | None = None,
    show_status: bool = False,
) -> str:
    """Render a human-readable dashboard string for stderr output."""
    header = f"## CI Run #{run.run_number} ({git_client.abbrev(run.head_sha)})"
    if show_status:
        suffix = "in progress" if run.status != "completed" else "complete"
        header += f" — {suffix}"
    lines = [header, ""]

    if run_ids and len(run_ids) > 1:
        lines.append(f"Workflow runs: {', '.join(str(r) for r in run_ids)}")
        lines.append("")

    if not run.failures:
        if run.status != "completed":
            lines.append("Checks still running — results incomplete.")
        else:
            lines.append("All checks passed.")
        return "\n".join(lines)

    kind_counts: dict[FailureKind, int] = {}
    for group in run.failures.values():
        kind_counts[group.kind] = kind_counts.get(group.kind, 0) + len(group.items)

    total = sum(kind_counts.values())
    lines.append(f"Failures: {total} total")
    for kind in FailureKind:
        count = kind_counts.get(kind, 0)
        if count:
            lines.append(f"  {kind.value}: {count}")
    lines.append("")

    headline_count = 0
    overflow = 0
    for group in run.failures.values():
        group_headlines: dict[str, int] = {}
        for item in group.items:
            text = item.headline or item.annotation[:_MAX_DASHBOARD_ANNOTATION]
            group_headlines[text] = group_headlines.get(text, 0) + 1

        if not group_headlines:
            continue

        remaining = _MAX_DASHBOARD_HEADLINES - headline_count
        if remaining <= 0:
            overflow += len(group_headlines)
            continue

        job_label = f"{group.job} → {group.failed_step}" if group.failed_step else group.job
        lines.append(f"  {job_label}:")
        for text, count in list(group_headlines.items())[:remaining]:
            suffix = f" (×{count})" if count > 1 else ""
            lines.append(f"    ▸ {text}{suffix}")
            headline_count += 1
        leftover = len(group_headlines) - remaining
        if leftover > 0:
            overflow += leftover
        lines.append("")

    if overflow > 0:
        lines.append(f"  … and {overflow} more")
        lines.append("")

    outcome_counts: dict[Outcome, int] = {}
    for outcome in progression.values():
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    if outcome_counts:
        parts = [
            f"{outcome_counts[o]} {o.value}"
            for o in Outcome if outcome_counts.get(o, 0)
        ]
        lines.append("Progression: " + ", ".join(parts))
        lines.append("")

    return "\n".join(lines)
