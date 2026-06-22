#!/usr/bin/env python3
"""CI failure lifecycle tracking.

Handles failure classification, progression tracking, and local state
persistence for the ci-failures skill.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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

STATE_DIR = "ignore/ci-failures"
STATE_FILE = "state.json"
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


@dataclass(frozen=True)
class FailureGroup:
    job: str
    kind: FailureKind
    items: tuple[FailureItem, ...]


@dataclass
class RunState:
    run_id: int
    run_number: int
    head_sha: str
    status: str
    conclusion: str
    fetched_at: str
    failures: dict[str, FailureGroup]


@dataclass
class CIState:
    repo: str
    pr_number: int | None
    branch: str
    runs: dict[int, RunState]
    latest_run_id: int | None


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

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")

_FAILURE_MARKERS: list[tuple[re.Pattern, FailureKind | None]] = [
    (re.compile(r"^--- FAIL:", re.MULTILINE), FailureKind.TEST),
    (re.compile(r"^FAIL\t", re.MULTILINE), FailureKind.TEST),
    (re.compile(r"FAILED", re.IGNORECASE), FailureKind.TEST),
    (re.compile(r"AssertionError|AssertError|assert .* ==", re.IGNORECASE), FailureKind.TEST),
    (re.compile(r"^error:", re.MULTILINE | re.IGNORECASE), FailureKind.BUILD),
    (re.compile(r"^fatal:", re.MULTILINE | re.IGNORECASE), FailureKind.BUILD),
]


def _strip_timestamps(text: str) -> str:
    """Remove GitHub Actions timestamp prefixes from log lines."""
    return "\n".join(
        _TIMESTAMP_RE.sub("", line) for line in text.splitlines()
    )


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

    if kind == FailureKind.BUILD:
        context = _extract_build_context(lines)
        if context:
            return context[:_MAX_CONTEXT_CHARS]

    for marker, _ in _FAILURE_MARKERS:
        context = _extract_around_marker(lines, marker)
        if context:
            return context[:_MAX_CONTEXT_CHARS]

    tail = "\n".join(lines[-_CONTEXT_LINES:])
    return tail[:_MAX_CONTEXT_CHARS]


def _extract_test_context(lines: list[str]) -> str:
    """Extract test failure output — captures from first failure marker to summary."""
    fail_indices = []
    for i, line in enumerate(lines):
        if re.match(r"--- FAIL:", line) or re.match(r"FAIL\t", line):
            fail_indices.append(i)
        elif "FAILED" in line.upper() and ("assert" in line.lower() or "error" in line.lower()):
            fail_indices.append(i)

    if not fail_indices:
        return ""

    start = max(0, fail_indices[0] - 5)
    end = min(len(lines), fail_indices[-1] + 20)
    return "\n".join(lines[start:end])


def _extract_build_context(lines: list[str]) -> str:
    """Extract build error output — captures lines around error/fatal markers."""
    error_re = re.compile(r"^(error|fatal):", re.IGNORECASE)
    return _extract_around_marker(lines, error_re)


def _extract_around_marker(lines: list[str], marker: re.Pattern) -> str:
    """Extract context around the first line matching marker."""
    for i, line in enumerate(lines):
        if marker.search(line):
            start = max(0, i - 10)
            end = min(len(lines), i + 30)
            return "\n".join(lines[start:end])
    return ""


# ── State factories ────────────────────────────────────────────────────────

def empty_state(
    repo: str, branch: str, pr_number: int | None = None,
) -> CIState:
    """Create a fresh CI state object."""
    return CIState(
        repo=repo, pr_number=pr_number, branch=branch,
        runs={}, latest_run_id=None,
    )


# ── Serialization ──────────────────────────────────────────────────────────

def _item_to_dict(item: FailureItem) -> dict:
    return {
        "id": item.id,
        "annotation": item.annotation,
        "file": item.file,
        "line": item.line,
        "diagnosis": item.diagnosis,
        "fix_sha": item.fix_sha,
        "outcome": item.outcome.value if item.outcome else None,
    }


def _item_from_dict(data: dict) -> FailureItem:
    outcome_val = data.get("outcome")
    return FailureItem(
        id=data["id"],
        annotation=data["annotation"],
        file=data.get("file"),
        line=data.get("line"),
        diagnosis=data.get("diagnosis"),
        fix_sha=data.get("fix_sha"),
        outcome=Outcome(outcome_val) if outcome_val else None,
    )


def _group_to_dict(group: FailureGroup) -> dict:
    return {
        "job": group.job,
        "kind": group.kind.value,
        "items": [_item_to_dict(i) for i in group.items],
    }


def _group_from_dict(data: dict) -> FailureGroup:
    return FailureGroup(
        job=data["job"],
        kind=FailureKind(data["kind"]),
        items=tuple(_item_from_dict(i) for i in data.get("items", [])),
    )


def _run_to_dict(run: RunState) -> dict:
    return {
        "run_id": run.run_id,
        "run_number": run.run_number,
        "head_sha": run.head_sha,
        "status": run.status,
        "conclusion": run.conclusion,
        "fetched_at": run.fetched_at,
        "failures": {k: _group_to_dict(v) for k, v in run.failures.items()},
    }


def _run_from_dict(data: dict) -> RunState:
    return RunState(
        run_id=data["run_id"],
        run_number=data["run_number"],
        head_sha=data["head_sha"],
        status=data["status"],
        conclusion=data["conclusion"],
        fetched_at=data["fetched_at"],
        failures={k: _group_from_dict(v) for k, v in data.get("failures", {}).items()},
    )


def state_to_dict(state: CIState) -> dict:
    """Serialize CIState to a JSON-compatible dict."""
    return {
        "repo": state.repo,
        "pr_number": state.pr_number,
        "branch": state.branch,
        "runs": {str(k): _run_to_dict(v) for k, v in state.runs.items()},
        "latest_run_id": state.latest_run_id,
    }


def state_from_dict(data: dict) -> CIState:
    """Deserialize CIState from a dict."""
    return CIState(
        repo=data["repo"],
        pr_number=data.get("pr_number"),
        branch=data["branch"],
        runs={int(k): _run_from_dict(v) for k, v in data.get("runs", {}).items()},
        latest_run_id=data.get("latest_run_id"),
    )


# ── State I/O ──────────────────────────────────────────────────────────────

def load_state(worktree_root: Path) -> CIState | None:
    """Load state from worktree. Returns None if file doesn't exist."""
    path = worktree_root / STATE_DIR / STATE_FILE
    if not path.exists():
        return None
    with open(path) as f:
        return state_from_dict(json.load(f))


def save_state(worktree_root: Path, state: CIState) -> None:
    """Save state to worktree, creating parent directories."""
    path = worktree_root / STATE_DIR / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state_to_dict(state), f, indent=2)
        f.write("\n")


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
    )


def sync_state(state: CIState, run: RunState) -> CIState:
    """Merge a new run into state, preserving prior diagnosis and fix history.

    If a failure item existed in the prior run with a diagnosis or fix_sha,
    those values carry forward to the new run's matching item.
    """
    prior_run = state.runs.get(state.latest_run_id) if state.latest_run_id else None
    prior_items = collect_item_ids(prior_run.failures) if prior_run else {}

    synced_failures: dict[str, FailureGroup] = {}
    for group_key, group in run.failures.items():
        synced_items = [_carry_forward_item(item, prior_items) for item in group.items]
        synced_failures[group_key] = FailureGroup(
            job=group.job, kind=group.kind,
            items=tuple(synced_items),
        )

    synced_run = RunState(
        run_id=run.run_id, run_number=run.run_number,
        head_sha=run.head_sha, status=run.status,
        conclusion=run.conclusion, fetched_at=run.fetched_at,
        failures=synced_failures,
    )

    state.runs[run.run_id] = synced_run
    state.latest_run_id = run.run_id

    # Prune old runs to bound state file size
    _MAX_RUNS = 10
    if len(state.runs) > _MAX_RUNS:
        oldest_ids = sorted(state.runs)[:len(state.runs) - _MAX_RUNS]
        for old_id in oldest_ids:
            del state.runs[old_id]

    return state


# ── Dashboard ──────────────────────────────────────────────────────────────

def render_dashboard(run: RunState, progression: dict[str, Outcome]) -> str:
    """Render a human-readable dashboard string for stderr output."""
    lines = [f"## CI Run #{run.run_number} ({run.head_sha[:7]})", ""]

    if not run.failures:
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
