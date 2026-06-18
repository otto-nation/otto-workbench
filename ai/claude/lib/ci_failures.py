#!/usr/bin/env python3
"""CI failure lifecycle tracking.

Handles failure classification, progression tracking, and local state
persistence for the ci-failures skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


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
