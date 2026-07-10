"""Shared constants, types, and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import log


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
TEMPLATE_SCOUT = "scout.md"
TEMPLATE_DISPROVE = "disprove.md"
TEMPLATE_FIX = "fix-findings.md"
TEMPLATE_FIX_COMMENTS = "fix-comments.md"
TEMPLATE_FIX_CI = "fix-ci.md"

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
FILENAME_SCOUT = "scout.md"
FILENAME_SCOUT_LOG = "scout.jsonl"
FILENAME_DISPROVE = "disprove.md"
FILENAME_DISPROVE_LOG = "disprove.jsonl"
FILENAME_FIX_LOG = "fix.jsonl"
FILENAME_META = "meta.json"
FILENAME_PIPELINE_STATE = "pipeline.json"
FILENAME_PROMPT_STATS = "prompt-stats.json"

FILENAME_POST_SESSION = "post.jsonl"
REVIEW_EXT = ".md"

PIPELINE_MULTI = "multi"
PIPELINE_SINGLE = "single"

_WORKBENCH_STATE_ENV = "WORKBENCH_STATE_DIR"
_WORKBENCH_STATE_DEFAULT = "~/.config/workbench"


def workbench_dir() -> Path:
    return Path(os.environ.get(
        _WORKBENCH_STATE_ENV, os.path.expanduser(_WORKBENCH_STATE_DEFAULT),
    ))


REVIEWS_DIR = workbench_dir() / "reviews"

SEVERITY_PREFIXES = ["M", "S", "N", "I"]
SEVERITY_JSON_KEYS = ["must_fix", "should_fix", "nit", "idiom"]
SEVERITY_COUNT_RE_FMT = r"^\s*- (\[ \] )?\*\*\[{}[0-9]+\]\*\*"


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


# ── Repo detection ────────────────────────────────────────────────────────────

def detect_repo(cwd: str | None = None) -> str:
    """Detect owner/repo from the git remote via ``gh``."""
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        log.error("Cannot determine repository from git remote")
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
        # Truthiness check intentionally treats "" and 0 as absent — no valid PR is #0
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


# ── Review file helpers ─────────────────────────────────────────────────────


def review_file_path(repo: str, pr_number: str) -> Path:
    """Return the expected path for a review file given repo and PR number."""
    repo_name = repo.split("/")[-1]
    return REVIEWS_DIR / f"{repo_name}-{pr_number}" / f"review{REVIEW_EXT}"


def find_review_file(repo: str, pr_number: str) -> Path | None:
    """Find a review file by repo and PR, checking canonical path then scanning meta."""
    canonical = review_file_path(repo, pr_number)
    if canonical.is_file():
        return canonical
    if not REVIEWS_DIR.is_dir():
        return None
    repo_name = repo.split("/")[-1]
    for entry in REVIEWS_DIR.iterdir():
        if not entry.is_dir() or not entry.name.startswith(repo_name):
            continue
        review = entry / f"review{REVIEW_EXT}"
        if not review.is_file():
            continue
        meta = read_review_meta(entry)
        if meta.repo == repo and str(meta.pr_number) == str(pr_number):
            return review
    return None


def read_review_meta(review_dir: Path) -> ReviewMeta:
    """Read meta.json from a review directory."""
    meta_file = review_dir / FILENAME_META
    if not meta_file.is_file():
        return ReviewMeta()
    try:
        return review_meta_from_dict(json.loads(meta_file.read_text()))
    except (json.JSONDecodeError, OSError):
        return ReviewMeta()


def count_severity(file: Path, prefix: str) -> int:
    """Count findings of a given severity prefix in a review file."""
    if not file.is_file():
        return 0
    try:
        text = file.read_text()
    except OSError:
        return 0
    pattern = SEVERITY_COUNT_RE_FMT.format(re.escape(prefix))
    return len(re.findall(pattern, text, re.MULTILINE))


def aggregate_session_usage(review_dir: Path | None) -> SessionUsage:
    """Aggregate usage from session and post-session logs."""
    if not review_dir:
        return SessionUsage()
    usages = [
        parse_session_log(str(review_dir / n))
        for n in (FILENAME_SESSION, FILENAME_POST_SESSION)
        if (review_dir / n).is_file()
    ]
    if not usages:
        return SessionUsage()
    return SessionUsage(
        cost=sum(u.cost for u in usages),
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_read_tokens=sum(u.cache_read_tokens for u in usages),
        cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        duration_ms=sum(u.duration_ms for u in usages),
    )


def read_pipeline_status(review_dir: Path | None) -> str:
    """Derive review status from pipeline state: 'error' if synthesis failed, else 'completed'."""
    from pr_state import ReviewStatus
    if not review_dir:
        return ReviewStatus.COMPLETED.value
    pipeline_path = review_dir / FILENAME_PIPELINE_STATE
    if not pipeline_path.is_file():
        return ReviewStatus.COMPLETED.value
    try:
        data = json.loads(pipeline_path.read_text())
        if data.get("synthesis_failed"):
            return ReviewStatus.ERROR.value
    except (json.JSONDecodeError, OSError):
        pass
    return ReviewStatus.COMPLETED.value


def parse_review_verdict(review_path: Path | None) -> str:
    """Extract verdict from review markdown's ## Verdict section.

    Returns the ReviewVerdict value if Disapprove is found, empty string otherwise
    (caller falls back to mechanical count-based verdict).
    """
    if not review_path or not review_path.is_file():
        return ""
    try:
        text = review_path.read_text()
    except OSError:
        return ""
    from pr_state import ReviewVerdict
    in_verdict = False
    for line in text.splitlines():
        if line.strip().lower().startswith("## verdict"):
            in_verdict = True
            continue
        if in_verdict:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("disapprove"):
                return ReviewVerdict.DISAPPROVE.value
            return ""
    return ""


def json_summary(repo: str, pr_number: str, review_file: str) -> str:
    """Build a REVIEW_SUMMARY:{json} string for a review."""
    counts = {}
    total = 0
    review_path = Path(review_file) if review_file else None
    for prefix, key in zip(SEVERITY_PREFIXES, SEVERITY_JSON_KEYS):
        c = count_severity(review_path, prefix) if review_path else 0
        counts[key] = c
        total += c

    from pr_state import ReviewVerdict
    parsed_verdict = parse_review_verdict(review_path)
    if parsed_verdict:
        verdict = parsed_verdict
    else:
        must_count = counts.get("must_fix", 0)
        verdict = ReviewVerdict.CHANGES_REQUESTED.value if must_count > 0 else ReviewVerdict.APPROVE.value

    review_dir = Path(review_file).parent if review_file else None
    usage = aggregate_session_usage(review_dir)

    review_content = None
    if review_path and review_path.is_file():
        try:
            review_content = review_path.read_text()
        except OSError:
            pass

    meta = read_review_meta(review_dir) if review_dir else ReviewMeta()

    status = read_pipeline_status(review_dir)

    data = {
        "repo": repo,
        "pr_number": int(pr_number) if pr_number else None,
        "head_sha": meta.head_sha or None,
        "head_ref": meta.head_ref or None,
        "base_ref": meta.base_ref or None,
        "review_type": meta.review_type or None,
        "review_file": review_file,
        "review_content": review_content,
        "findings": {**counts, "total": total},
        "verdict": verdict,
        "status": status,
        "cost_usd": usage.cost,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "duration_ms": usage.duration_ms,
    }
    return f"REVIEW_SUMMARY:{json.dumps(data)}"
