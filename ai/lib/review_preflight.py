"""Pre-flight data collection, tier classification, file grouping, and PR fetching.

Handles everything needed before prompt construction: collecting diffs, commit logs,
file contents, permissions, and organizing files into review groups.

The records this fills in — `PRMetadata`, `PRContext`, `PreflightData`, `Group`
and the `ReviewJob` they hang off — are `review_types`', so a consumer that only
needs to name a job does not import the collection that builds one.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import os
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gh_client
import git_client
import log
import pr_context
from agent_types import Mode
from agent_types import Mode
from pr_comments import _is_acknowledgment, _is_pushback, fetch_threads
from review_dedup import _get_bot_login
from review_document import BOLD_FINDING_ID_RE, ReviewHeader
from review_github import PRData
from review_profiles import (
    format_profiles_section, load_profiles, match_profiles,
)
from review_types import (
    Group, PRContext, PreflightData, PRMetadata, ReviewJob,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_GROUP_LINES = 800
MAX_GROUP_FILES = 15
DEFAULT_MAX_PARALLEL = 1
HOLISTIC_MIN_GROUPS = 8

GROUP_TIER1 = "tier1-critical"
GROUP_TIER3 = "tier3-generated"

# What the Summary says when no synthesis agent wrote the review. Each names
# why synthesis did not produce the document, because a reader who cannot tell a
# failed agent from one nobody asked to run reads the same review two ways.
FALLBACK_SUMMARY = "Synthesis agent failed — findings below are from individual group reviews."
SKIPPED_SUMMARY = "Synthesis skipped by --no-synthesis — findings below are from individual group reviews."
BUDGET_SUMMARY = (
    "Synthesis did not run — the cost budget was reached first. "
    "Findings below are from individual group reviews."
)

TIER1_BASENAMES = {
    "CLAUDE.md", ".cursorrules", "AGENTS.md", "GEMINI.md",
    "go.mod", "package.json", "package-lock.json", "requirements.txt", "Gemfile",
}
TIER1_EXTENSIONS = {".proto", ".graphql"}
TIER1_PATH_SEGMENTS = {
    "migrations", "auth", "crypto", "permissions",
    "vault", "network-policies", "authorization-policies",
}
TIER3_BASENAMES = {"go.sum"}
TIER3_BASENAMES_SUFFIXES = (
    ".pb.go", "_pb2.py", "_pb.ts", "_pb2_grpc.py",
    ".latest.sql", ".ko.yaml",
)
TIER3_PATH_SEGMENTS = {"gen", "testdata"}

MAX_PROMPT_TOKENS = 120_000
MAX_PROMPT_BYTES = MAX_PROMPT_TOKENS * 4
TEMPLATE_OVERHEAD_BYTES = 20_000
MAX_FILE_BYTES = 100_000
MAX_TRUNCATED_LINES = 500
MAX_COMMIT_LOG_BYTES = 50_000
MAX_DELTA_DIFF_BYTES = 80_000
MAX_DELTA_LOG_BYTES = 20_000

# ceiling: a flat reserve for everything in a prompt that is not preflight data —
# the template, the PR header, prior reviews, reply threads. `review_prompt` now
# measures those sections exactly before it budgets, so this double-counts them:
# on a typical prompt it holds back ~116KB nothing spends, and the review is
# smaller than it had room to be. Shrinking it is not free — every byte returned
# is a byte of diff sent to the model, so it raises per-review cost, which is why
# it is left as-is while review cost is what is being worked on. Upgrade when a
# phase reports a cut in its prompt stats that this reserve alone would have
# covered, or once per-review cost has a budget of its own to spend it against.
NON_PREFLIGHT_OVERHEAD_BYTES = 120_000
MIN_DIFF_BYTES = 20_000

# How much of somebody else's prose a prompt quotes back: a prior review's body,
# a review comment, the root of a thread being re-reviewed. Each one is a
# gist — enough for the agent to recognise what was said and go read the thread
# — and there is no bound on how many of them a busy PR contributes, which is
# why the cap is per-body rather than on the section they land in.
MAX_REVIEW_BODY_LEN = 200

FILE_CONTENT_DENSITY_THRESHOLD = 0.15
FILE_CONTENT_MIN_SIZE = 5120


# ── Pre-flight data collection ───────────────────────────────────────────────

def _truncate_log(text: str, max_bytes: int, label: str = "Commit log") -> str:
    raw = text.encode()
    if len(raw) <= max_bytes:
        return text
    log.warn(f"{label} too large ({len(raw) // 1024}KB), truncating to {max_bytes // 1024}KB")
    truncated = raw[:max_bytes].decode(errors="ignore").rsplit("\n", 1)[0]
    return truncated + "\n\n... (truncated — full log exceeded size limit)"


def _read_file_safe(path: Path) -> str:
    try:
        content = path.read_text()
        byte_len = len(content.encode())
        if byte_len > MAX_FILE_BYTES:
            lines = content.splitlines(keepends=True)
            truncated = "".join(lines[:MAX_TRUNCATED_LINES])
            return f"{truncated}\n\n<truncated — file is {byte_len // 1024}KB, showing first {MAX_TRUNCATED_LINES} lines>"
        return content
    except FileNotFoundError:
        return "<file deleted>"
    except UnicodeDecodeError:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return f"<binary file, {size} bytes>"
    except PermissionError:
        return "<permission denied>"
    except IsADirectoryError:
        return "<directory>"


def _file_permissions(path: Path) -> str:
    try:
        mode = path.stat().st_mode
        return oct(stat.S_IMODE(mode))
    except OSError:
        return "?"


def _fetch_base(wt_path: str, base: str) -> None:
    """Refresh ``origin/<base>``.

    Every range in a review is anchored to that ref, so each entry point
    refreshes it before reading. A stale ref would otherwise put the file list
    and the diff on two different fork points.
    """
    git_client.run("fetch", "origin", base, cwd=wt_path)


def _fork_point(wt_path: str, base: str) -> str:
    """Commit the branch forked from, or ``HEAD`` when the base is unreachable.

    Diffing from here reaches the working tree, so uncommitted edits are part
    of the review surface. Falling back to ``HEAD`` narrows that to the
    uncommitted edits alone rather than reviewing nothing.
    """
    return git_client.out("merge-base", f"origin/{base}", "HEAD", cwd=wt_path) or "HEAD"


def _untracked_files(wt_path: str) -> list[str]:
    """Paths git does not track and .gitignore does not exclude."""
    return git_client.lines("ls-files", "--others", "--exclude-standard", cwd=wt_path)


def _diff_untracked(wt_path: str, paths: list[str], numstat: bool = False) -> str:
    """Diff each untracked path against nothing, as a whole-file addition."""
    flags = ["--numstat"] if numstat else []
    parts = []
    for path in paths:
        # --no-index exits 1 whenever the two sides differ, which is always here,
        # so the exit code is read past rather than through `out`.
        out = git_client.run(
            "diff", "--no-index", *flags, "--", os.devnull, path, cwd=wt_path,
        ).stdout.strip()
        if not out:
            continue
        # numstat names the pair "/dev/null => <path>"; the diff body is already clean.
        parts.append(out.rsplit("\t", 1)[0] + "\t" + path if numstat else out)
    return "\n".join(parts)


def _join_nonempty(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _worktree_diff(wt_path: str, since: str) -> str:
    """Every change from ``since`` to the working tree, untracked files included.

    ``since`` bounds the tracked half only. Untracked files have no history to
    compare against, so they come through whole every time — on a delta review
    that means one lingering across runs is re-shown rather than dropped.
    """
    # ceiling: untracked files ignore `since`; upgrade to diffing against the
    # prior review's copy if repeated deltas start drowning in re-shown files.
    return _join_nonempty(
        git_client.out("diff", since, cwd=wt_path),
        _diff_untracked(wt_path, _untracked_files(wt_path)),
    )


def _scope_to_surface(raw_diff: str, pr_files: list[dict]) -> str:
    """``raw_diff`` narrowed to the files the review is actually reviewing.

    ``pr_files`` is the review's surface — `PRMetadata.files`. A diff with no
    file headers, or a job with no surface to narrow to, comes back untouched.

    The delta range is `prior_sha..HEAD`, which spans the base branch as well
    as the branch: rebase onto a moved base and every commit the base gained
    lands in it. That is how a 107-file review came to report 4,974 changed
    files — the list alone was 260KB, and it pushed the synthesis prompt past
    its budget. Nothing outside the surface is reviewable in the first place,
    so narrowing here bounds the delta by the PR rather than by the base's
    churn, and `delta_files` — which decides whether a group's files changed
    enough to re-review — stops naming files no group holds.
    """
    if not pr_files:
        return raw_diff
    return _scope_diff(raw_diff, [f["path"] for f in pr_files])


def _collect_delta(job: ReviewJob) -> tuple[str, str, list[str], str]:
    empty = ("", "", [], "")
    if not job.prior_review:
        log.info("No prior review — running full review")
        return empty
    prior_sha = ReviewHeader.parse(job.prior_review).head_sha
    if not prior_sha:
        log.info("Prior review has no SHA marker — running full review")
        return empty
    if prior_sha == job.pr.head_sha:
        log.info("Prior review is on current HEAD — running full review")
        return empty
    verify = git_client.out("cat-file", "-t", prior_sha, cwd=job.wt_path)
    if verify != "commit":
        log.warn(
            f"Prior review SHA {git_client.abbrev(prior_sha)} not reachable "
            "— running full review")
        return empty

    if job.mode == Mode.SELF:
        # Self-review's surface reaches past HEAD, so a delta review still sees
        # edits that have not been committed since the prior review.
        raw_diff = _worktree_diff(job.wt_path, prior_sha)
    else:
        raw_diff = git_client.out("diff", f"{prior_sha}..HEAD", cwd=job.wt_path)
    raw_diff = _scope_to_surface(raw_diff, job.pr.files)
    delta_diff, _ = _truncate_diff(raw_diff, MAX_DELTA_DIFF_BYTES)
    # Same pathspec as the diff, for the same reason: a rebase puts every
    # commit the base gained in this range, and a log of them describes work
    # the review is not looking at.
    surface = ["--", *(f["path"] for f in job.pr.files)] if job.pr.files else []
    raw_log = git_client.out(
        "log", "--stat", "--reverse", f"{prior_sha}..HEAD", *surface, cwd=job.wt_path,
    )
    delta_log = _truncate_log(raw_log, MAX_DELTA_LOG_BYTES, "Delta commit log")
    delta_files = [m.group(1) for m in _DIFF_HEADER_RE.finditer(raw_diff)]
    log.info(
        f"Incremental review: {len(delta_files)} files changed since "
        f"prior review ({git_client.abbrev(prior_sha)}..{git_client.abbrev(job.pr.head_sha)})"
    )
    return delta_diff, delta_log, delta_files, prior_sha


def _collect_git_data(
    wt_path: str, base: str, pr_files: list[dict], include_worktree: bool = False,
) -> tuple[str, str]:
    _fetch_base(wt_path, base)
    commit_log = git_client.out(
        "log", "--stat", "--reverse", f"origin/{base}..HEAD", cwd=wt_path,
    )
    commit_log = _truncate_log(commit_log, MAX_COMMIT_LOG_BYTES)

    if include_worktree:
        return _worktree_diff(wt_path, _fork_point(wt_path, base)), commit_log

    diff = git_client.out("diff", f"origin/{base}...HEAD", cwd=wt_path)
    if not diff and pr_files:
        diff = git_client.out("diff", "HEAD", cwd=wt_path)
    return diff, commit_log


def _collect_project_context(
    wt: Path,
) -> tuple[str, str, dict[str, str], list]:
    claude_md = ""
    for name in ("CLAUDE.md", ".claude/CLAUDE.md"):
        p = wt / name
        if p.exists():
            claude_md = _read_file_safe(p)
            break

    architecture_md = ""
    arch_path = wt / ".claude" / "architecture.md"
    if arch_path.exists():
        architecture_md = _read_file_safe(arch_path)

    review_checklists: dict[str, str] = {}
    review_dir = wt / ".claude" / "review"
    if review_dir.is_dir():
        for checklist in sorted(review_dir.glob("*.md")):
            review_checklists[checklist.name] = _read_file_safe(checklist)

    profiles = load_profiles(str(wt))

    return claude_md, architecture_md, review_checklists, profiles


def _collect_file_data(
    wt: Path, pr_files: list[dict],
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    contents: dict[str, str] = {}
    permissions: dict[str, str] = {}
    changes: dict[str, int] = {}
    for f in pr_files:
        p = wt / f["path"]
        contents[f["path"]] = _read_file_safe(p)
        permissions[f["path"]] = _file_permissions(p)
        changes[f["path"]] = f.get("additions", 0) + f.get("deletions", 0)
    return contents, permissions, changes


def _is_low_density(path: str, content: str, file_changes: dict[str, int]) -> bool:
    size = len(content.encode())
    if size <= FILE_CONTENT_MIN_SIZE:
        return False
    total_lines = content.count("\n") or 1
    changed = file_changes.get(path, total_lines)
    return (changed / total_lines) < FILE_CONTENT_DENSITY_THRESHOLD


def _fit_to_budget(
    all_contents: dict[str, str],
    all_permissions: dict[str, str],
    file_changes: dict[str, int],
    base_size: int,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    density_skipped = [
        p for p, c in all_contents.items()
        if _is_low_density(p, c, file_changes)
    ]
    candidates = {p: c for p, c in all_contents.items() if p not in set(density_skipped)}

    file_sizes = {p: len(c.encode()) for p, c in candidates.items()}
    sorted_paths = sorted(candidates, key=lambda p: (classify_tier(p), file_sizes[p]))

    included: dict[str, str] = {}
    included_perms: dict[str, str] = {}
    budget_omitted: list[str] = []
    remaining = max(0, MAX_PROMPT_BYTES - base_size)
    for path in sorted_paths:
        if file_sizes[path] <= remaining:
            included[path] = candidates[path]
            included_perms[path] = all_permissions[path]
            remaining -= file_sizes[path]
        else:
            budget_omitted.append(path)

    omitted = density_skipped + budget_omitted

    if density_skipped:
        density_kb = sum(len(all_contents[p].encode()) for p in density_skipped) // 1024
        log.info(f"Skipped {len(density_skipped)} low-density files (~{density_kb}KB) — diff sufficient")
    if omitted:
        omitted_kb = sum(len(all_contents.get(p, "").encode()) for p in omitted) // 1024
        log.info(f"Pre-collected {len(included)}/{len(all_contents)} files ({len(omitted)} omitted, ~{omitted_kb}KB)")

    return included, included_perms, omitted


def collect_preflight_data(job: ReviewJob) -> PreflightData:
    wt = Path(job.wt_path)
    base = job.pr.base or pr_context.default_branch(wt)

    diff, commit_log = _collect_git_data(
        job.wt_path, base, job.pr.files, include_worktree=job.mode == Mode.SELF,
    )
    claude_md, architecture_md, review_checklists, profiles = _collect_project_context(wt)
    all_contents, all_permissions, file_changes = _collect_file_data(wt, job.pr.files)

    base_size = (
        len(diff.encode())
        + len(commit_log.encode())
        + len(claude_md.encode())
        + len(architecture_md.encode())
        + sum(len(v.encode()) for v in review_checklists.values())
        + TEMPLATE_OVERHEAD_BYTES
    )
    included, included_perms, omitted = _fit_to_budget(
        all_contents, all_permissions, file_changes, base_size,
    )

    delta_diff, delta_commit_log, delta_files, prior_head_sha = _collect_delta(job)

    return PreflightData(
        diff=diff,
        commit_log=commit_log,
        file_contents=included,
        file_permissions=included_perms,
        claude_md=claude_md,
        architecture_md=architecture_md,
        review_checklists=review_checklists,
        review_profiles=profiles,
        omitted_files=omitted,
        delta_diff=delta_diff,
        delta_commit_log=delta_commit_log,
        delta_files=delta_files,
        prior_head_sha=prior_head_sha,
    )


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.MULTILINE)


def _scope_diff(full_diff: str, file_filter: list[str]) -> str:
    filter_set = set(file_filter)
    matches = list(_DIFF_HEADER_RE.finditer(full_diff))
    sections: list[str] = []
    for i, m in enumerate(matches):
        if m.group(1) in filter_set:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_diff)
            sections.append(full_diff[m.start():end])
    return "".join(sections).strip()


def _truncate_diff(full_diff: str, max_bytes: int) -> tuple[str, list[str]]:
    if len(full_diff.encode()) <= max_bytes:
        return full_diff, []

    matches = list(_DIFF_HEADER_RE.finditer(full_diff))
    if not matches:
        truncated = full_diff.encode()[:max_bytes].decode(errors="ignore")
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            truncated = truncated[:last_nl + 1]
        return truncated + "\n... (diff truncated)\n", []

    sections: list[tuple[int, str, str, int]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_diff)
        path = m.group(1)
        text = full_diff[m.start():end]
        sections.append((i, path, text, len(text.encode())))

    prioritized = sorted(sections, key=lambda s: (classify_tier(s[1]), s[3]))

    included_indices: set[int] = set()
    remaining = max_bytes
    omitted_paths: list[str] = []
    for idx, path, text, size in prioritized:
        if size <= remaining:
            included_indices.add(idx)
            remaining -= size
        else:
            omitted_paths.append(path)

    if not included_indices and sections:
        idx, path, text, size = prioritized[0]
        truncated = text.encode()[:max(0, max_bytes - 200)].decode(errors="ignore")
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            truncated = truncated[:last_nl + 1]
        shown_kb = len(truncated.encode()) // 1024
        truncated += f"\n... (truncated — showing first {shown_kb}KB of {size // 1024}KB)\n"
        omitted_paths = [p for _, p, _, _ in prioritized]
        result = truncated
        included_count = 0
    else:
        parts = [sections[i][2] for i in sorted(included_indices)]
        result = "".join(parts)
        included_count = len(included_indices)

    if omitted_paths:
        log.warn(
            f"Diff truncated: {included_count}/{len(sections)} file diffs included, "
            f"{len(omitted_paths)} omitted — agent can read via tools"
        )

    return result, omitted_paths


def _format_file_contents(
    data: PreflightData, file_filter: list[str] | None,
    skip_contents: bool = False,
) -> list[str]:
    """The changed files, inlined or named, scoped to ``file_filter``.

    ``skip_contents`` inlines nothing and names every in-scope file under
    "Files not pre-collected" instead — the budget's first lever drops the
    contents, and a file whose contents were dropped is in exactly the position
    of one that was never collected. Saying so is what lets the agent read it:
    the section is the only list the prompt gives it to read from, and dropping
    the contents silently used to drop the list along with them, leaving the
    agent told its files were pre-collected and shown none of them.
    """
    parts: list[str] = []
    files_to_include = [
        p for p in (file_filter or data.file_contents.keys())
        if p in data.file_contents
    ]
    if files_to_include and not skip_contents:
        parts += ["", "### Changed file contents"]
        for path in files_to_include:
            perms = data.file_permissions.get(path, "?")
            parts.append(f"\n<file path=\"{path}\" permissions=\"{perms}\">")
            parts.append(data.file_contents[path])
            parts.append("</file>")

    omitted = data.omitted_files
    if file_filter:
        omitted = [p for p in omitted if p in set(file_filter)]
    if skip_contents:
        omitted = files_to_include + [p for p in omitted if p not in data.file_contents]
    if omitted:
        parts += ["", "### Files not pre-collected (read directly)"]
        for path in omitted:
            parts.append(f"- {path}")
    return parts


def build_project_context(
    data: PreflightData,
    file_filter: list[str] | None = None,
) -> str:
    has_content = data.claude_md or data.architecture_md or data.review_checklists or data.review_profiles
    if not has_content:
        return ""
    parts: list[str] = ["### Project context"]
    if data.claude_md:
        parts += ["", "#### CLAUDE.md", "", data.claude_md]
    if data.architecture_md:
        parts += ["", "#### .claude/architecture.md", "", data.architecture_md]
    if data.review_checklists:
        parts.append("\n#### Review checklists")
        for name, content in data.review_checklists.items():
            parts += [f"\n##### {name}", "", content]
    if data.review_profiles:
        matched = match_profiles(data.review_profiles, file_filter or [])
        if not matched and not file_filter:
            matched = data.review_profiles
        profiles_section = format_profiles_section(matched)
        if profiles_section:
            parts += ["", profiles_section]
    return "\n".join(parts)


def format_preflight_data(
    data: PreflightData,
    file_filter: list[str] | None = None,
    skip_file_contents: bool = False,
    skip_project_context: bool = False,
    max_diff_bytes: int | None = None,
) -> str:
    parts = [
        "## Pre-collected data",
        "",
        "Use this data directly. Do NOT re-read these files, re-run git diff, re-run git log,",
        "or re-fetch PR reviews via gh api. Only use Read/Bash for files NOT listed here",
        "(cross-references, callers, tests, config files outside the PR).",
    ]

    diff_text = _scope_diff(data.diff, file_filter) if file_filter else data.diff
    diff_omitted: list[str] = []
    if max_diff_bytes is not None:
        diff_text, diff_omitted = _truncate_diff(diff_text, max_diff_bytes)
    parts += ["", "### Full diff", "", "```diff", diff_text, "```"]

    if data.commit_log:
        parts += ["", "### Commit history", "", "```", data.commit_log, "```"]

    parts += _format_file_contents(
        data, file_filter, skip_contents=skip_file_contents,
    )

    if diff_omitted:
        parts += ["", "### Diffs not pre-collected (use `git diff -- <path>` or Read tool)"]
        for path in diff_omitted:
            parts.append(f"- {path}")

    if not skip_project_context:
        project_ctx = build_project_context(data)
        if project_ctx:
            parts += ["", project_ctx]

    return "\n".join(parts)


# ── Tier classification ──────────────────────────────────────────────────────

def classify_tier(path: str) -> int:
    parts = path.split("/")
    basename = parts[-1]

    if any(seg in TIER3_PATH_SEGMENTS for seg in parts):
        return 3
    if basename in TIER3_BASENAMES or basename.endswith(TIER3_BASENAMES_SUFFIXES):
        return 3
    if basename in TIER1_BASENAMES:
        return 1
    if any(basename.endswith(ext) for ext in TIER1_EXTENSIONS):
        return 1
    if any(seg in TIER1_PATH_SEGMENTS for seg in parts):
        return 1
    return 2


# ── File grouping ─────────────────────────────────────────────────────────────

def _split_large_dir(name: str, files: list[str], file_lines: dict[str, int]) -> list[Group]:
    groups: list[Group] = []
    sub_files: list[str] = []
    sub_lines = 0
    sub_idx = 1
    for f in files:
        fl = file_lines[f]
        if sub_files and (sub_lines + fl > MAX_GROUP_LINES or len(sub_files) >= MAX_GROUP_FILES):
            groups.append(Group(f"{name}-{sub_idx}", sub_files, sub_lines))
            sub_files = []
            sub_lines = 0
            sub_idx += 1
        sub_files.append(f)
        sub_lines += fl
    if sub_files:
        groups.append(Group(f"{name}-{sub_idx}", sub_files, sub_lines))
    return groups


def group_files(pr: PRMetadata) -> list[Group]:
    file_lines = {f["path"]: f["additions"] + f["deletions"] for f in pr.files}

    tiers: dict[int, list[str]] = {1: [], 2: [], 3: []}
    tier_lines: dict[int, int] = {1: 0, 2: 0, 3: 0}

    for path, lines in file_lines.items():
        t = classify_tier(path)
        tiers[t].append(path)
        tier_lines[t] += lines

    groups: list[Group] = []

    if tiers[1]:
        groups.append(Group(GROUP_TIER1, tiers[1], tier_lines[1]))

    dir_files: dict[str, list[str]] = {}
    dir_lines: dict[str, int] = {}
    dir_order: list[str] = []
    for f in tiers[2]:
        d = f.split("/")[0]
        if d not in dir_files:
            dir_files[d] = []
            dir_lines[d] = 0
            dir_order.append(d)
        dir_files[d].append(f)
        dir_lines[d] += file_lines[f]

    for d in dir_order:
        files = dir_files[d]
        total = dir_lines[d]
        if total > MAX_GROUP_LINES or len(files) > MAX_GROUP_FILES:
            groups.extend(_split_large_dir(d, files, file_lines))
        else:
            groups.append(Group(d, files, total))

    if tiers[3]:
        groups.append(Group(GROUP_TIER3, tiers[3], tier_lines[3]))

    return groups


def _merge_score(a: Group, b: Group) -> tuple[int, int]:
    """Lower score = better merge: longest shared name prefix, then smallest combined size."""
    # os.path.commonprefix is character-based (not path-component-based), which is
    # intentional here — we want a quick name-similarity heuristic, not strict path ancestry.
    shared = len(os.path.commonprefix([a.name, b.name]))
    return (-shared, a.lines + b.lines)


def _find_best_merge_pair(groups: list[Group]) -> tuple[int, int]:
    pairs = [(i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))]
    return min(pairs, key=lambda p: _merge_score(groups[p[0]], groups[p[1]]))


def _merge_smallest_groups(groups: list[Group], max_groups: int) -> list[Group]:
    groups = list(groups)
    while len(groups) > max_groups:
        i, j = _find_best_merge_pair(groups)
        a, b = groups[i], groups[j]
        merged = Group(
            name=f"{a.name}+{b.name}",
            files=a.files + b.files,
            lines=a.lines + b.lines,
        )
        groups = [g for k, g in enumerate(groups) if k not in (i, j)]
        groups.append(merged)
    return groups




# ── PR data fetching ──────────────────────────────────────────────────────────

def _parse_numstat(numstat: str) -> tuple[list[dict], int, int]:
    files = []
    total_add = 0
    total_del = 0
    for line in numstat.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add = int(parts[0]) if parts[0] != "-" else 0
        delete = int(parts[1]) if parts[1] != "-" else 0
        files.append({"path": parts[2], "additions": add, "deletions": delete})
        total_add += add
        total_del += delete
    return files, total_add, total_del


def fetch_branch_metadata(wt_path: str, base: str | None = None) -> PRMetadata:
    # Resolved rather than defaulted to "main" in the signature: this is the
    # no-PR self-review path, so nothing upstream has named a base, and a
    # `master` repository was previously fetched and diffed against a branch it
    # does not have.
    base = base or pr_context.default_branch(wt_path)
    _fetch_base(wt_path, base)
    head_sha = git_client.head_sha(cwd=wt_path)
    branch = git_client.current_branch(cwd=wt_path)
    log_range = f"origin/{base}..HEAD"

    log_output = git_client.out("log", log_range, "--oneline", cwd=wt_path)
    first_subject = log_output.split("\n")[0].split(" ", 1)[-1] if log_output else branch
    title = first_subject

    # Diffing from the fork point reaches the working tree, so the file list
    # matches the diff _collect_git_data builds for self-review: committed,
    # uncommitted and untracked changes alike.
    numstat = _join_nonempty(
        git_client.out("diff", "--numstat", _fork_point(wt_path, base), cwd=wt_path),
        _diff_untracked(wt_path, _untracked_files(wt_path), numstat=True),
    )
    files, total_add, total_del = _parse_numstat(numstat)

    return PRMetadata(
        title=title,
        body="",
        head=branch,
        base=base,
        head_sha=head_sha,
        additions=total_add,
        deletions=total_del,
        changed_files=len(files),
        files=files,
    )


def fetch_pr_metadata(
    repo: str, pr_number: str, pin_sha: str = "", wt_path: str = "",
) -> PRMetadata:
    """Fetch PR metadata, optionally pinned to an earlier commit.

    ``pin_sha`` is the commit a --recover run must complete against; ``wt_path``
    is a checkout of it. Both must be set for pinning to take effect.
    """
    data = gh_client.pr_view(
        pr_number, "title", "body", "headRefName", "baseRefName", "headRefOid",
        "additions", "deletions", "changedFiles", "files",
        "isDraft", "labels", "author",
        repo=repo,
    )
    if not data:
        log.error(f"failed to fetch PR #{pr_number} from {repo}")
        sys.exit(1)
    head_sha = data["headRefOid"]
    additions = data["additions"]
    deletions = data["deletions"]
    changed_files = data["changedFiles"]
    files = [
        {"path": f["path"], "additions": f["additions"], "deletions": f["deletions"]}
        for f in data["files"]
    ]

    # --recover completes a run against the commit it started from, so the
    # changeset must come from the pinned checkout rather than the moved PR head.
    if pin_sha and pin_sha != head_sha and wt_path:
        numstat = git_client.out(
            "diff", "--numstat", f"origin/{data['baseRefName']}...HEAD", cwd=wt_path,
        )
        files, additions, deletions = _parse_numstat(numstat)
        changed_files = len(files)
        head_sha = pin_sha

    return PRMetadata(
        title=data["title"],
        body=data.get("body") or "",
        head=data["headRefName"],
        base=data["baseRefName"],
        head_sha=head_sha,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
        files=files,
        is_draft=data.get("isDraft", False),
        labels=[l["name"] for l in data.get("labels", [])],
        author=(data.get("author") or {}).get("login", ""),
    )


def fetch_pr_context(
    repo: str, pr_number: str, pr_data: PRData | None = None,
) -> PRContext:
    if pr_data is not None:
        return _pr_context_from_data(pr_data)

    cmds = {
        "commits": [
            "pr", "view", pr_number, "--repo", repo,
            "--json", "commits",
            "--jq", '[.commits[] | .messageHeadline] | join("\\n")',
        ],
        "reviews": [
            "api", f"repos/{repo}/pulls/{pr_number}/reviews",
            "--jq", '[.[] | {user: .user.login, state, body}]',
        ],
        "review_comments": [
            "api", f"repos/{repo}/pulls/{pr_number}/comments",
            "--jq", '[.[] | {id, path, line, body, user: .user.login, in_reply_to_id}]',
        ],
        "comments": [
            "api", f"repos/{repo}/issues/{pr_number}/comments",
            "--jq", '[.[] | {user: .user.login, body}]',
        ],
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(gh_client.out, *cmd): name for name, cmd in cmds.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return PRContext(
        commits=results["commits"],
        reviews=results["reviews"] or "[]",
        review_comments=results["review_comments"] or "[]",
        comments=results["comments"] or "[]",
    )


def _thread_comment_entries(thread: dict) -> list[dict]:
    """Convert a review thread's comment nodes into flat entry dicts."""
    path = thread.get("path", "")
    line = thread.get("line")
    nodes = thread.get("comments", {}).get("nodes", [])
    root_id = None
    entries = []
    for i, c in enumerate(nodes):
        entries.append({
            "id": c.get("databaseId"),
            "path": path,
            "line": line,
            "body": c.get("body", ""),
            "user": (c.get("author") or {}).get("login", ""),
            "in_reply_to_id": root_id,
        })
        if i == 0:
            root_id = c.get("databaseId")
    return entries


def _pr_context_from_data(pr_data: PRData) -> PRContext:
    """Build PRContext from PRData without any API calls."""
    commits = "\n".join(
        c.get("commit", {}).get("messageHeadline", "")
        for c in pr_data.commits
    )

    reviews = [
        {
            "user": (r.get("author") or {}).get("login", ""),
            "state": r.get("state", ""),
            "body": r.get("body", ""),
        }
        for r in pr_data.reviews
    ]

    review_comments = []
    for thread in pr_data.review_threads:
        review_comments.extend(_thread_comment_entries(thread))

    comments = [
        {
            "user": (c.get("author") or {}).get("login", ""),
            "body": c.get("body", ""),
        }
        for c in pr_data.issue_comments
    ]

    return PRContext(
        commits=commits,
        reviews=json.dumps(reviews),
        review_comments=json.dumps(review_comments),
        comments=json.dumps(comments),
    )


# ── Reply thread classification for re-reviews ──────────────────────────────

THREAD_RESOLVED = "resolved"
THREAD_ACKNOWLEDGED = "acknowledged"
THREAD_CONTESTED = "contested"
THREAD_REPLIED = "replied"
THREAD_UNREPLIED = "unreplied"

def _classify_thread_for_rereview(
    comments: list[dict], is_resolved: bool, bot_login: str,
) -> tuple[str, list[dict]]:
    """Classify a review thread from the bot-reviewer's perspective.

    Returns (state, author_replies) where author_replies are non-bot comments
    after the first bot comment.
    """
    if is_resolved:
        return THREAD_RESOLVED, []

    bot_lower = bot_login.lower()
    author_replies = []
    seen_bot = False
    for c in comments:
        login = (c.get("author") or {}).get("login", "").lower()
        if login == bot_lower:
            seen_bot = True
        elif seen_bot:
            author_replies.append(c)

    if not author_replies:
        return THREAD_UNREPLIED, []

    last_reply = author_replies[-1]
    body = last_reply.get("body", "")
    if _is_acknowledgment(body):
        return THREAD_ACKNOWLEDGED, author_replies
    if _is_pushback(body):
        return THREAD_CONTESTED, author_replies
    return THREAD_REPLIED, author_replies


def _match_thread_to_finding(root_body: str) -> str:
    """Extract finding ID (e.g. 'M1') from a bot-posted review comment body."""
    m = BOLD_FINDING_ID_RE.search(root_body)
    return m.group(1) if m else ""


def fetch_reply_threads(
    repo: str, pr_number: str, bot_login: str = "",
    pr_data: PRData | None = None,
) -> dict:
    """Fetch and classify reply threads on bot-authored review comments.

    Returns a dict with:
      - threads: list of per-thread dicts with state, finding_id, replies, path, line
      - summary: count per state
    """
    if not bot_login:
        bot_login = pr_data.viewer_login if pr_data is not None else _get_bot_login()
    if not bot_login:
        log.warn("Could not detect bot login — skipping reply thread analysis")
        return {"threads": [], "summary": {}}

    owner, name = repo.split("/", 1)
    try:
        raw_threads = fetch_threads(owner, name, int(pr_number), pr_data)
    except Exception:
        return {"threads": [], "summary": {}}

    if not raw_threads:
        return {"threads": [], "summary": {}}

    bot_lower = bot_login.lower()
    classified = []
    summary: dict[str, int] = {}

    for thread in raw_threads:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        root = comments[0]
        root_author = (root.get("author") or {}).get("login", "").lower()
        if root_author != bot_lower:
            continue

        is_resolved = thread.get("isResolved", False)
        state, author_replies = _classify_thread_for_rereview(
            comments, is_resolved, bot_login,
        )
        finding_id = _match_thread_to_finding(root.get("body", ""))

        classified.append({
            "state": state,
            "finding_id": finding_id,
            "path": thread.get("path", ""),
            "line": thread.get("line"),
            "root_body": root.get("body", "")[:MAX_REVIEW_BODY_LEN],
            "replies": [
                {
                    "author": (r.get("author") or {}).get("login", ""),
                    "body": r.get("body", ""),
                }
                for r in author_replies
            ],
        })
        summary[state] = summary.get(state, 0) + 1

    return {"threads": classified, "summary": summary}

