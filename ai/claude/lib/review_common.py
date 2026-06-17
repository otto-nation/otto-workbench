"""Shared constants, types, and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


# ── Severity ─────────────────────────────────────────────────────────────────

SEVERITY_MUST = "M"
SEVERITY_SHOULD = "S"
SEVERITY_NIT = "N"
SEVERITY_IDIOMS = "I"


@dataclass(frozen=True)
class SeverityConfig:
    key: str
    label: str
    section: str
    posting: str
    body_group: str
    aliases: tuple[str, ...] = ()


SEVERITIES = [
    SeverityConfig(SEVERITY_MUST,    "must-fix",  "Must fix",  posting="inline", body_group="by_severity"),
    SeverityConfig(SEVERITY_SHOULD,  "should-fix", "Should fix", posting="inline", body_group="by_severity"),
    SeverityConfig(SEVERITY_NIT,     "nit",       "Nit",        posting="body",   body_group="by_file", aliases=("Nits",)),
    SeverityConfig(SEVERITY_IDIOMS,  "idiom",     "Idioms",     posting="body",   body_group="by_file"),
]

_SEVERITY_BY_KEY = {s.key: s for s in SEVERITIES}


def severity_by_key(key: str) -> SeverityConfig:
    return _SEVERITY_BY_KEY[key]


SECTION_FILE_TRIAGE = "File Triage"


# ── Modes ────────────────────────────────────────────────────────────────────

MODE_PR = "pr"
MODE_SELF = "self"


# ── Templates ────────────────────────────────────────────────────────────────

TEMPLATE_SINGLE = "single-agent.md"
TEMPLATE_HOLISTIC = "holistic.md"
TEMPLATE_GROUP = "group.md"
TEMPLATE_SYNTHESIS = "synthesis.md"
TEMPLATE_SELF_REVIEW = "self-review.md"
TEMPLATE_SELF_SYNTHESIS = "self-review-synthesis.md"
TEMPLATE_ANGLES = "angles.md"
TEMPLATE_FIX = "fix-findings.md"

TEMPLATE_DIR_REL = Path("lib") / "review-templates"


# ── Filenames ────────────────────────────────────────────────────────────────

FILENAME_PRIOR = "prior.md"
FILENAME_SESSION = "session.jsonl"
FILENAME_HOLISTIC = "holistic.md"
FILENAME_HOLISTIC_LOG = "holistic.jsonl"
FILENAME_SYNTHESIS_LOG = "synthesis.jsonl"
FILENAME_GROUP = "group-{}.md"
FILENAME_GROUP_LOG = "group-{}.jsonl"
FILENAME_ANGLES = "angles.md"
FILENAME_ANGLES_LOG = "angles.jsonl"
FILENAME_FIX_LOG = "fix.jsonl"
FILENAME_META = "meta.json"
FILENAME_PIPELINE_STATE = "pipeline.json"
FILENAME_PROMPT_STATS = "prompt-stats.json"

PIPELINE_MULTI = "multi"
PIPELINE_SINGLE = "single"


# ── Metadata format ──────────────────────────────────────────────────────────

FILE_STAT_FMT = "  - {path} (+{additions} -{deletions})"
META_DATE = "<!-- date: {today} -->"
META_HEAD_SHA = "<!-- head_sha: {head_sha} -->"
META_REVIEW_TYPE = "<!-- review_type: {review_type} -->"
META_PRIOR_SHA = "<!-- prior_sha: {prior_sha} -->"
META_PRIOR_DATE = "<!-- prior_date: {prior_date} -->"
META_DELTA_FILES = "<!-- delta_files: {delta_file_count} -->"
META_SKIPPED_GROUPS = "<!-- skipped_groups: {skipped}/{total} -->"
META_GENERATOR = "<!-- generator: {generator_version} -->"

PRIOR_SHA_RE = re.compile(r"<!-- head_sha: ([a-f0-9]+) -->")
PRIOR_DATE_RE = re.compile(r"<!-- date: (\d{4}-\d{2}-\d{2}) -->")


# ── ANSI output ──────────────────────────────────────────────────────────────

ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_INFO = "\033[1;34m▸\033[0m"
ANSI_WARN = "\033[1;33m⚠\033[0m"
ANSI_ERR = "\033[1;31m✗\033[0m"
ANSI_INFO_DOT = "\033[1;34m●\033[0m"
ANSI_WARN_DOT = "\033[1;33m●\033[0m"
ANSI_ERR_DOT = "\033[1;31m●\033[0m"

_print_lock = threading.Lock()


def _info(msg: str):
    with _print_lock:
        print(f"{ANSI_INFO} {msg}", flush=True)


def _warn(msg: str):
    with _print_lock:
        print(f"{ANSI_WARN} {msg}", file=sys.stderr, flush=True)


def _err(msg: str):
    with _print_lock:
        print(f"{ANSI_ERR_DOT} {msg}", file=sys.stderr, flush=True)


# ── Repo detection ────────────────────────────────────────────────────────────

def detect_repo(cwd: str | None = None) -> str:
    """Detect owner/repo from the git remote via ``gh``."""
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        _err("Cannot determine repository from git remote")
        sys.exit(1)
    return r.stdout.strip()


# ── Path helpers ─────────────────────────────────────────────────────────────

def _derive_path(review_file: str, filename: str) -> str:
    return str(Path(review_file).parent / filename)


# ── Subprocess ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        return ""
    return r.stdout.strip()
