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
    FLAKY = "flaky"


class Outcome(Enum):
    NEW = "new"
    PERSISTING = "persisting"
    REGRESSED = "regressed"
    RESOLVED = "resolved"
    FIXED = "fixed"


# ── Constants ──────────────────────────────────────────────────────────────

STATE_DIR = "ignore/ci-failures"
STATE_FILE = "state.json"

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
        annotation_lower = annotation.lower()
        for signature in INFRA_SIGNATURES:
            if signature in annotation_lower:
                return FailureKind.INFRA

    for pattern, kind in JOB_PATTERNS:
        if pattern.search(job_name):
            return kind

    return FailureKind.BUILD


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
    for item_id, item in current_items.items():
        prior_item = prior_items.get(item_id)
        if prior_item is None:
            result[item_id] = Outcome.NEW
        elif prior_item.outcome == Outcome.FIXED:
            result[item_id] = Outcome.REGRESSED
        else:
            result[item_id] = Outcome.PERSISTING

    return result


# ── Root-cause grouping ────────────────────────────────────────────────────

def group_by_root_cause(groups: list[FailureGroup]) -> list[FailureGroup]:
    """Deduplicate failure items across groups by (file, line).

    Items sharing the same file and line (both non-None) are merged into
    one group. Items without file/line stay in their original group.
    """
    keyed: dict[tuple[str, int], list[FailureItem]] = {}
    keyed_jobs: dict[tuple[str, int], str] = {}
    keyed_kinds: dict[tuple[str, int], FailureKind] = {}
    ungrouped: list[FailureGroup] = []

    for group in groups:
        orphan_items: list[FailureItem] = []
        for item in group.items:
            if item.file is not None and item.line is not None:
                key = (item.file, item.line)
                keyed.setdefault(key, []).append(item)
                if key not in keyed_jobs:
                    keyed_jobs[key] = group.job
                    keyed_kinds[key] = group.kind
            else:
                orphan_items.append(item)
        if orphan_items:
            ungrouped.append(FailureGroup(
                job=group.job, kind=group.kind,
                items=tuple(orphan_items),
            ))

    merged = []
    for key, items in keyed.items():
        merged.append(FailureGroup(
            job=keyed_jobs[key], kind=keyed_kinds[key],
            items=tuple(items),
        ))

    return merged + ungrouped


# ── State sync ─────────────────────────────────────────────────────────────

def sync_state(state: CIState, run: RunState) -> CIState:
    """Merge a new run into state, preserving prior diagnosis and fix history.

    If a failure item existed in the prior run with a diagnosis or fix_sha,
    those values carry forward to the new run's matching item.
    """
    prior_run = state.runs.get(state.latest_run_id) if state.latest_run_id else None
    prior_items = collect_item_ids(prior_run.failures) if prior_run else {}

    synced_failures: dict[str, FailureGroup] = {}
    for group_key, group in run.failures.items():
        synced_items: list[FailureItem] = []
        for item in group.items:
            prior = prior_items.get(item.id)
            if prior and (prior.diagnosis or prior.fix_sha):
                synced_items.append(FailureItem(
                    id=item.id,
                    annotation=item.annotation,
                    file=item.file,
                    line=item.line,
                    diagnosis=prior.diagnosis if item.diagnosis is None else item.diagnosis,
                    fix_sha=prior.fix_sha if item.fix_sha is None else item.fix_sha,
                    outcome=item.outcome,
                ))
            else:
                synced_items.append(item)
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
    return state
