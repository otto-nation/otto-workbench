"""Shared constants, types, and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants.
"""

from __future__ import annotations

import json
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


# ── Review metadata ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReviewMeta:
    repo: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    head_ref: str = ""
    base_ref: str = ""
    review_type: str = ""


def review_meta_from_dict(d: dict) -> ReviewMeta:
    pr_number = d.get("pr_number")
    return ReviewMeta(
        repo=d.get("repo", ""),
        pr_number=int(pr_number) if pr_number else None,
        head_sha=d.get("head_sha", ""),
        head_ref=d.get("head_ref", ""),
        base_ref=d.get("base_ref", ""),
        review_type=d.get("review_type", ""),
    )


# ── Session usage ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionUsage:
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


def parse_session_log(path: str) -> SessionUsage:
    """Parse a session JSONL log file and return aggregated usage."""
    cost = 0.0
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    duration_ms = 0
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return SessionUsage()
    for line in lines:
        if '"type":"result"' not in line and '"type": "result"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "result":
            continue
        cost += rec.get("total_cost_usd", 0) or 0
        usage = rec.get("usage", {})
        input_tokens += usage.get("input_tokens", 0) or 0
        output_tokens += usage.get("output_tokens", 0) or 0
        cache_read += usage.get("cache_read_input_tokens", 0) or 0
        cache_write += usage.get("cache_creation_input_tokens", 0) or 0
        duration_ms += rec.get("duration_ms", 0) or 0
    return SessionUsage(
        cost=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        duration_ms=duration_ms,
    )


# ── Subprocess ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        return ""
    return r.stdout.strip()
