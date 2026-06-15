"""Pre-flight data collection, tier classification, file grouping, and PR fetching.

Handles everything needed before prompt construction: collecting diffs, commit logs,
file contents, permissions, and organizing files into review groups.
"""

from __future__ import annotations

import json
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from review_common import (
    FILE_STAT_FMT, MODE_PR, PRIOR_SHA_RE,
    _info, _run, _warn,
)

# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class PRMetadata:
    title: str
    body: str
    head: str
    base: str
    head_sha: str
    additions: int
    deletions: int
    changed_files: int
    files: list[dict]

    @property
    def total_lines(self):
        return self.additions + self.deletions

    @property
    def file_stats(self):
        if self.total_lines <= MULTI_PHASE_LINE_THRESHOLD:
            return ""
        sorted_files = sorted(
            self.files, key=lambda f: f["additions"] + f["deletions"], reverse=True
        )
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in sorted_files
        )

    @property
    def all_files_formatted(self):
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in self.files
        )


@dataclass
class PRContext:
    commits: str = ""
    reviews: str = "[]"
    review_comments: str = "[]"
    comments: str = "[]"


@dataclass
class Group:
    name: str
    files: list[str]
    lines: int



@dataclass
class ReviewJob:
    repo: str
    pr_number: str
    pr: PRMetadata
    ctx: PRContext
    wt_path: str
    review_file: str
    session_log: str
    reviews_dir: str
    issue_link: str = ""
    issue_context: str = ""
    prior_review: str = ""
    mode: str = MODE_PR
    generator_version: str = ""
    preflight: "PreflightData | None" = None
    model: str = ""


@dataclass
class PreflightData:
    diff: str
    commit_log: str
    file_contents: dict[str, str]
    file_permissions: dict[str, str]
    claude_md: str
    context_md: str
    review_checklists: dict[str, str] = field(default_factory=dict)
    omitted_files: list[str] = field(default_factory=list)
    delta_diff: str = ""
    delta_commit_log: str = ""
    delta_files: list[str] = field(default_factory=list)
    prior_head_sha: str = ""


@dataclass
class PipelineState:
    head_sha: str
    group_names: list[str]
    holistic_done: bool = False
    groups_done: list[int] = field(default_factory=list)
    groups_failed: dict[int, str] = field(default_factory=dict)
    synthesis_done: bool = False
    synthesis_failed: str = ""
    review_type: str = "full"
    prior_sha: str = ""
    skipped_groups: list[int] = field(default_factory=list)
    angles_done: bool = False

    @property
    def group_count(self):
        return len(self.group_names)


# ── Constants ─────────────────────────────────────────────────────────────────

MULTI_PHASE_LINE_THRESHOLD = 500
MULTI_PHASE_FILE_THRESHOLD = 10
MAX_GROUP_LINES = 800
DEFAULT_MAX_PARALLEL = 4
DEFAULT_MAX_GROUPS = 12
HOLISTIC_MIN_GROUPS = 4

DEFAULT_MAX_COST = 20.0
DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_TURNS_GROUP = 15
DEFAULT_MAX_TURNS_HOLISTIC = 15
DEFAULT_MAX_TURNS_SYNTHESIS = 15
DEFAULT_MAX_TURNS_SINGLE = 15
DEFAULT_MAX_BUDGET_PER_AGENT = 5.0

DEFAULT_MODEL_GROUP = "sonnet"
DEFAULT_MODEL_HOLISTIC = "opus"
DEFAULT_MODEL_SYNTHESIS = "opus"
DEFAULT_MODEL_SINGLE = "opus"

DEFAULT_BASE_BRANCH = "main"
GIT_RANGE_TO_HEAD = f"origin/{DEFAULT_BASE_BRANCH}..HEAD"

GROUP_TIER1 = "tier1-critical"
GROUP_TIER3 = "tier3-generated"

FALLBACK_SUMMARY = "Synthesis agent failed — findings below are from individual group reviews."

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

NON_PREFLIGHT_OVERHEAD_BYTES = 120_000
MIN_DIFF_BYTES = 20_000


# ── Pre-flight data collection ───────────────────────────────────────────────

def _truncate_log(text: str, max_bytes: int, label: str = "Commit log") -> str:
    raw = text.encode()
    if len(raw) <= max_bytes:
        return text
    _warn(f"{label} too large ({len(raw) // 1024}KB), truncating to {max_bytes // 1024}KB")
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


def _file_permissions(path: Path) -> str:
    try:
        mode = path.stat().st_mode
        return oct(stat.S_IMODE(mode))
    except OSError:
        return "?"


def _collect_delta(job: "ReviewJob") -> tuple[str, str, list[str], str]:
    empty = ("", "", [], "")
    if not job.prior_review:
        return empty
    prior_sha_match = PRIOR_SHA_RE.search(job.prior_review)
    if not prior_sha_match:
        return empty
    prior_sha = prior_sha_match.group(1)
    verify = _run(["git", "cat-file", "-t", prior_sha], cwd=job.wt_path, check=False)
    if verify.strip() != "commit":
        _warn(f"Prior review SHA {prior_sha[:7]} not reachable — running full review")
        return empty

    raw_diff = _run(["git", "diff", f"{prior_sha}..HEAD"], cwd=job.wt_path)
    delta_diff, _ = _truncate_diff(raw_diff, MAX_DELTA_DIFF_BYTES)
    raw_log = _run(
        ["git", "log", "--stat", "--reverse", f"{prior_sha}..HEAD"],
        cwd=job.wt_path,
    )
    delta_log = _truncate_log(raw_log, MAX_DELTA_LOG_BYTES, "Delta commit log")
    delta_names = _run(
        ["git", "diff", "--name-only", f"{prior_sha}..HEAD"],
        cwd=job.wt_path,
    )
    delta_files = [f for f in delta_names.split("\n") if f.strip()]
    _info(
        f"Incremental review: {len(delta_files)} files changed since "
        f"prior review ({prior_sha[:7]}..{job.pr.head_sha[:7]})"
    )
    return delta_diff, delta_log, delta_files, prior_sha


def collect_preflight_data(job: "ReviewJob") -> PreflightData:
    wt = Path(job.wt_path)
    base = job.pr.base or DEFAULT_BASE_BRANCH
    _run(["git", "fetch", "origin", base], cwd=job.wt_path, check=False)
    git_range = f"origin/{base}...HEAD"
    git_range_log = f"origin/{base}..HEAD"

    diff = _run(["git", "diff", git_range], cwd=job.wt_path)
    commit_log = _run(["git", "log", "--stat", "--reverse", git_range_log], cwd=job.wt_path)

    commit_log = _truncate_log(commit_log, MAX_COMMIT_LOG_BYTES)

    # Fall back to uncommitted diff when no commits exist on the branch
    if not diff and job.pr.files:
        diff = _run(["git", "diff", "HEAD"], cwd=job.wt_path)

    claude_md = ""
    for name in ("CLAUDE.md", ".claude/CLAUDE.md"):
        p = wt / name
        if p.exists():
            claude_md = _read_file_safe(p)
            break

    context_md = ""
    ctx_path = wt / ".claude" / "context.md"
    if ctx_path.exists():
        context_md = _read_file_safe(ctx_path)

    review_checklists: dict[str, str] = {}
    review_dir = wt / ".claude" / "review"
    if review_dir.is_dir():
        for checklist in sorted(review_dir.glob("*.md")):
            review_checklists[checklist.name] = _read_file_safe(checklist)

    all_contents: dict[str, str] = {}
    all_permissions: dict[str, str] = {}
    for f in job.pr.files:
        p = wt / f["path"]
        all_contents[f["path"]] = _read_file_safe(p)
        all_permissions[f["path"]] = _file_permissions(p)

    file_sizes = {p: len(c.encode()) for p, c in all_contents.items()}

    base_size = (
        len(diff.encode())
        + len(commit_log.encode())
        + len(claude_md.encode())
        + len(context_md.encode())
        + sum(len(v.encode()) for v in review_checklists.values())
        + TEMPLATE_OVERHEAD_BYTES
    )
    remaining = max(0, MAX_PROMPT_BYTES - base_size)

    sorted_paths = sorted(
        all_contents.keys(),
        key=lambda p: (classify_tier(p), file_sizes[p]),
    )

    included: dict[str, str] = {}
    included_perms: dict[str, str] = {}
    omitted: list[str] = []
    for path in sorted_paths:
        if file_sizes[path] <= remaining:
            included[path] = all_contents[path]
            included_perms[path] = all_permissions[path]
            remaining -= file_sizes[path]
        else:
            omitted.append(path)

    if omitted:
        omitted_kb = sum(file_sizes[p] for p in omitted) // 1024
        _info(f"Pre-collected {len(included)}/{len(all_contents)} files ({len(omitted)} omitted, ~{omitted_kb}KB)")

    delta_diff, delta_commit_log, delta_files, prior_head_sha = _collect_delta(job)

    return PreflightData(
        diff=diff,
        commit_log=commit_log,
        file_contents=included,
        file_permissions=included_perms,
        claude_md=claude_md,
        context_md=context_md,
        review_checklists=review_checklists,
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
        _warn(
            f"Diff truncated: {included_count}/{len(sections)} file diffs included, "
            f"{len(omitted_paths)} omitted — agent can read via tools"
        )

    return result, omitted_paths


def _format_file_contents(
    data: PreflightData, file_filter: list[str] | None,
) -> list[str]:
    parts: list[str] = []
    files_to_include = [
        p for p in (file_filter or data.file_contents.keys())
        if p in data.file_contents
    ]
    if files_to_include:
        parts += ["", "### Changed file contents"]
        for path in files_to_include:
            perms = data.file_permissions.get(path, "?")
            parts.append(f"\n<file path=\"{path}\" permissions=\"{perms}\">")
            parts.append(data.file_contents[path])
            parts.append("</file>")

    omitted = data.omitted_files
    if file_filter:
        omitted = [p for p in omitted if p in set(file_filter)]
    if omitted:
        parts += ["", "### Files not pre-collected (read directly)"]
        for path in omitted:
            parts.append(f"- {path}")
    return parts


def format_preflight_data(
    data: PreflightData,
    file_filter: list[str] | None = None,
    skip_file_contents: bool = False,
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

    if not skip_file_contents:
        parts += _format_file_contents(data, file_filter)

    if diff_omitted:
        parts += ["", "### Diffs not pre-collected (use `git diff -- <path>` or Read tool)"]
        for path in diff_omitted:
            parts.append(f"- {path}")

    if data.claude_md:
        parts += ["", "### Project context", "", "#### CLAUDE.md", "", data.claude_md]
    if data.context_md:
        parts += ["", "#### .claude/context.md", "", data.context_md]
    if data.review_checklists:
        parts.append("\n#### Review checklists")
        for name, content in data.review_checklists.items():
            parts += [f"\n##### {name}", "", content]

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
        if sub_lines + fl > MAX_GROUP_LINES and sub_files:
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
        if total > MAX_GROUP_LINES:
            groups.extend(_split_large_dir(d, files, file_lines))
        else:
            groups.append(Group(d, files, total))

    if tiers[3]:
        groups.append(Group(GROUP_TIER3, tiers[3], tier_lines[3]))

    return groups


def _merge_smallest_groups(groups: list[Group], max_groups: int) -> list[Group]:
    groups = list(groups)
    while len(groups) > max_groups:
        groups.sort(key=lambda g: g.lines)
        a, b = groups[0], groups[1]
        merged = Group(
            name=f"{a.name}+{b.name}",
            files=a.files + b.files,
            lines=a.lines + b.lines,
        )
        groups = [merged] + groups[2:]
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


def fetch_branch_metadata(wt_path: str) -> PRMetadata:
    head_sha = _run(["git", "rev-parse", "HEAD"], cwd=wt_path)
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path)

    log_output = _run(["git", "log", GIT_RANGE_TO_HEAD, "--oneline"], cwd=wt_path)
    first_subject = log_output.split("\n")[0].split(" ", 1)[-1] if log_output else branch
    title = first_subject

    numstat = _run(["git", "diff", "--numstat", GIT_RANGE_TO_HEAD], cwd=wt_path)
    files, total_add, total_del = _parse_numstat(numstat)

    # Fall back to uncommitted changes when there are no commits on the
    # branch yet — common in self-review before first commit.
    if not files:
        numstat = _run(["git", "diff", "--numstat", "HEAD"], cwd=wt_path)
        files, total_add, total_del = _parse_numstat(numstat)

    return PRMetadata(
        title=title,
        body="",
        head=branch,
        base=DEFAULT_BASE_BRANCH,
        head_sha=head_sha,
        additions=total_add,
        deletions=total_del,
        changed_files=len(files),
        files=files,
    )


def fetch_pr_metadata(repo: str, pr_number: str) -> PRMetadata:
    raw = _run([
        "gh", "pr", "view", pr_number, "--repo", repo,
        "--json", "title,body,headRefName,baseRefName,headRefOid,"
                  "additions,deletions,changedFiles,files",
    ])
    if not raw:
        print(f"error: failed to fetch PR #{pr_number} from {repo}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(raw)
    return PRMetadata(
        title=data["title"],
        body=data.get("body") or "",
        head=data["headRefName"],
        base=data["baseRefName"],
        head_sha=data["headRefOid"],
        additions=data["additions"],
        deletions=data["deletions"],
        changed_files=data["changedFiles"],
        files=[
            {"path": f["path"], "additions": f["additions"], "deletions": f["deletions"]}
            for f in data["files"]
        ],
    )


def fetch_pr_context(repo: str, pr_number: str) -> PRContext:
    cmds = {
        "commits": [
            "gh", "pr", "view", pr_number, "--repo", repo,
            "--json", "commits",
            "--jq", '[.commits[] | .messageHeadline] | join("\\n")',
        ],
        "reviews": [
            "gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews",
            "--jq", '[.[] | {user: .user.login, state, body}]',
        ],
        "review_comments": [
            "gh", "api", f"repos/{repo}/pulls/{pr_number}/comments",
            "--jq", '[.[] | {id, path, line, body, user: .user.login, in_reply_to_id}]',
        ],
        "comments": [
            "gh", "api", f"repos/{repo}/issues/{pr_number}/comments",
            "--jq", '[.[] | {user: .user.login, body}]',
        ],
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_run, cmd, check=False): name for name, cmd in cmds.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return PRContext(
        commits=results["commits"],
        reviews=results["reviews"] or "[]",
        review_comments=results["review_comments"] or "[]",
        comments=results["comments"] or "[]",
    )

