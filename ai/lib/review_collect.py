"""What a review collects before a prompt is built, and what it may cost.

Gathers the diff, the commit log, the changed files' contents and permissions,
the repository's own review context, and the delta against a prior review;
fits the result to the prompt's byte budget; and formats it into the
pre-collected data block a phase sends.

`fetch_branch_metadata` is here for the same reason: a self-review with no PR
behind it describes itself out of the worktree, off the same fork point and the
same `worktree_diff` the collection uses. Its counterpart for a branch that does
have a PR is `review_github.fetch_pr_metadata`, and both fill in `PRMetadata`.

The bounds on what a prompt may carry are `review_budget`'s, not this module's —
`_fit_to_budget` is still here, and is the one place that decides which files a
review can afford to inline, reading the same numbers `review_prompt` budgets
against. How the collected files are ranked and divided is `review_grouping`'s,
what a phase does with the block is `review_prompt`'s, and the records this
fills in are `review_types`'.
"""

# doc-group: pipeline

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import git_client
import git_topology
import log
from phases import Mode
from review_budget import (
    FILE_CONTENT_DENSITY_THRESHOLD, FILE_CONTENT_MIN_SIZE, FileFit,
    MAX_COMMIT_LOG_BYTES, MAX_DELTA_DIFF_BYTES, MAX_DELTA_LOG_BYTES,
    MAX_FILE_BYTES, MAX_PROMPT_BYTES, MAX_TRUNCATED_LINES,
    TEMPLATE_OVERHEAD_BYTES, fit_files, fixed_preflight_bytes,
)
from review_document import ReviewHeader
from review_grouping import (
    classify_tier, format_profiles_section, load_profiles, match_profiles,
)
from review_types import PreflightData, PRMetadata, ReviewJob


# ── Git reads ────────────────────────────────────────────────────────────────

def fetch_base(wt_path: str, base: str) -> None:
    """Refresh ``origin/<base>`` in the worktree at ``wt_path``.

    Every range in a review is anchored to that ref, so each entry point
    refreshes it before reading. A stale ref would otherwise put the file list
    and the diff on two different fork points.
    """
    git_client.run("fetch", "origin", base, cwd=wt_path)


def fork_point(wt_path: str, base: str) -> str:
    """Commit the branch forked from, or ``HEAD`` when ``base`` is unreachable.

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


def worktree_diff(wt_path: str, since: str, *, numstat: bool = False) -> str:
    """Every change from ``since`` to the working tree, untracked files included.

    ``numstat`` asks for the per-file add/delete counts instead of the patch,
    so the file list a review reports and the diff it sends come off the same
    range rather than off two commands that could disagree.

    ``since`` bounds the tracked half only. Untracked files have no history to
    compare against, so they come through whole every time — on a delta review
    that means one lingering across runs is re-shown rather than dropped.
    """
    # ceiling: untracked files ignore `since`; upgrade to diffing against the
    # prior review's copy if repeated deltas start drowning in re-shown files.
    flags = ["--numstat"] if numstat else []
    return _join_nonempty(
        git_client.out("diff", *flags, since, cwd=wt_path),
        _diff_untracked(wt_path, _untracked_files(wt_path), numstat=numstat),
    )


@dataclass(frozen=True)
class Numstat:
    """What ``git diff --numstat`` says a change set touched.

    ``files`` is one ``{path, additions, deletions}`` entry per line, in the
    order git listed them; ``additions`` and ``deletions`` are the totals over
    all of them.
    """

    files: list[dict]
    additions: int
    deletions: int


def parse_numstat(numstat: str) -> Numstat:
    """Read ``git diff --numstat`` output into per-file and total counts.

    A binary file's counts are ``-``; they land as zero rather than being
    dropped, so the file still appears in the review's file list.
    """
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
    return Numstat(files=files, additions=total_add, deletions=total_del)


def fetch_branch_metadata(wt_path: str, base: str | None = None) -> PRMetadata:
    """Describe a branch from its worktree, the way a PR describes itself.

    ``base`` is the branch to measure against; it is resolved from the
    repository's default when the caller has none. This is the no-PR
    self-review's route to the same `PRMetadata` `review_github` fetches, so a
    phase downstream cannot tell which one filled it in.
    """
    # Resolved rather than defaulted to "main" in the signature: this is the
    # no-PR self-review path, so nothing upstream has named a base, and a
    # `master` repository was previously fetched and diffed against a branch it
    # does not have.
    base = base or git_topology.default_branch(wt_path)
    fetch_base(wt_path, base)
    head_sha = git_client.head_sha(cwd=wt_path)
    branch = git_client.current_branch(cwd=wt_path)
    log_range = f"origin/{base}..HEAD"

    log_output = git_client.out("log", log_range, "--oneline", cwd=wt_path)
    first_subject = log_output.split("\n")[0].split(" ", 1)[-1] if log_output else branch

    # Diffing from the fork point reaches the working tree, so the file list
    # matches the diff `worktree_diff` builds for self-review: committed,
    # uncommitted and untracked changes alike.
    counts = parse_numstat(worktree_diff(wt_path, fork_point(wt_path, base), numstat=True))

    return PRMetadata(
        title=first_subject,
        body="",
        head=branch,
        base=base,
        head_sha=head_sha,
        additions=counts.additions,
        deletions=counts.deletions,
        changed_files=len(counts.files),
        files=counts.files,
    )


# ── File reads ───────────────────────────────────────────────────────────────

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


# ── Diff shaping ─────────────────────────────────────────────────────────────

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.MULTILINE)


@dataclass(frozen=True)
class TruncatedDiff:
    """A diff cut to a byte budget, and the files that did not fit in it.

    ``omitted`` is what the prompt has to name so the agent can go read it —
    a file dropped silently reads to the agent as a file the diff did not
    touch.
    """

    text: str
    omitted: list[str]


def scope_diff(full_diff: str, file_filter: list[str]) -> str:
    """``full_diff`` reduced to the per-file sections ``file_filter`` names."""
    filter_set = set(file_filter)
    matches = list(_DIFF_HEADER_RE.finditer(full_diff))
    sections: list[str] = []
    for i, m in enumerate(matches):
        if m.group(1) in filter_set:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_diff)
            sections.append(full_diff[m.start():end])
    return "".join(sections).strip()


def truncate_diff(full_diff: str, max_bytes: int) -> TruncatedDiff:
    """``full_diff`` cut to ``max_bytes``, dropping whole files by tier and size.

    Files are kept in `classify_tier` order, smallest first within a tier, so
    the bytes go to what a reviewer is most likely to need. A single file too
    large to fit at all is cut mid-body rather than dropped, since a diff with
    nothing in it says less than a partial one.
    """
    if len(full_diff.encode()) <= max_bytes:
        return TruncatedDiff(full_diff, [])

    matches = list(_DIFF_HEADER_RE.finditer(full_diff))
    if not matches:
        truncated = full_diff.encode()[:max_bytes].decode(errors="ignore")
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            truncated = truncated[:last_nl + 1]
        return TruncatedDiff(truncated + "\n... (diff truncated)\n", [])

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

    return TruncatedDiff(result, omitted_paths)


# ── Pre-flight data collection ───────────────────────────────────────────────

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
    return scope_diff(raw_diff, [f["path"] for f in pr_files])


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
        raw_diff = worktree_diff(job.wt_path, prior_sha)
    else:
        raw_diff = git_client.out("diff", f"{prior_sha}..HEAD", cwd=job.wt_path)
    raw_diff = _scope_to_surface(raw_diff, job.pr.files)
    delta_diff = truncate_diff(raw_diff, MAX_DELTA_DIFF_BYTES).text
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
    fetch_base(wt_path, base)
    commit_log = git_client.out(
        "log", "--stat", "--reverse", f"origin/{base}..HEAD", cwd=wt_path,
    )
    commit_log = _truncate_log(commit_log, MAX_COMMIT_LOG_BYTES)

    if include_worktree:
        return worktree_diff(wt_path, fork_point(wt_path, base)), commit_log

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
) -> FileFit:
    """Which of `all_contents` a review can afford, at collection time.

    Drops low-density files first — large ones `file_changes` shows only a
    sliver of, where the diff already carries what changed — then ranks
    whatever is left by `(classify_tier, size)` and keeps what fits in
    `MAX_PROMPT_BYTES` once `base_size` (the diff, the commit log, and the
    rest of the fixed overhead) is spent.
    """
    density_skipped = [
        p for p, c in all_contents.items()
        if _is_low_density(p, c, file_changes)
    ]
    candidates = {p: c for p, c in all_contents.items() if p not in set(density_skipped)}
    fit = fit_files(candidates, all_permissions, MAX_PROMPT_BYTES - base_size)
    omitted = density_skipped + fit.omitted

    if density_skipped:
        density_kb = sum(len(all_contents[p].encode()) for p in density_skipped) // 1024
        log.info(f"Skipped {len(density_skipped)} low-density files (~{density_kb}KB) — diff sufficient")
    if omitted:
        omitted_kb = sum(len(all_contents.get(p, "").encode()) for p in omitted) // 1024
        log.info(f"Pre-collected {len(fit.included)}/{len(all_contents)} files ({len(omitted)} omitted, ~{omitted_kb}KB)")

    return FileFit(fit.included, fit.permissions, omitted)


def collect_preflight_data(job: ReviewJob) -> PreflightData:
    """Everything a prompt for ``job`` can be built from, already within budget."""
    wt = Path(job.wt_path)
    base = job.pr.base or git_topology.default_branch(wt)

    diff, commit_log = _collect_git_data(
        job.wt_path, base, job.pr.files, include_worktree=job.mode == Mode.SELF,
    )
    claude_md, architecture_md, review_checklists, profiles = _collect_project_context(wt)
    all_contents, all_permissions, file_changes = _collect_file_data(wt, job.pr.files)

    base_size = (
        len(diff.encode())
        + fixed_preflight_bytes(
            commit_log, claude_md, architecture_md, review_checklists,
        )
        + TEMPLATE_OVERHEAD_BYTES
    )
    fit = _fit_to_budget(all_contents, all_permissions, file_changes, base_size)

    delta_diff, delta_commit_log, delta_files, prior_head_sha = _collect_delta(job)

    return PreflightData(
        diff=diff,
        commit_log=commit_log,
        file_contents=fit.included,
        file_permissions=fit.permissions,
        claude_md=claude_md,
        architecture_md=architecture_md,
        review_checklists=review_checklists,
        review_profiles=profiles,
        omitted_files=fit.omitted,
        delta_diff=delta_diff,
        delta_commit_log=delta_commit_log,
        delta_files=delta_files,
        prior_head_sha=prior_head_sha,
    )


# ── Formatting ───────────────────────────────────────────────────────────────

def _format_file_contents(
    data: PreflightData, file_filter: list[str] | None,
    files: FileFit | None = None,
) -> list[str]:
    """The changed files, inlined or named, scoped to ``file_filter``.

    ``files`` is the prompt budget's fit, if one ran. `None` means no fit ran:
    everything ``file_filter`` admits from ``data.file_contents`` is inlined,
    same as before this lever existed. A fit inlines only the files it kept
    and lists every path it dropped under "Files not pre-collected" instead —
    a file whose contents were dropped is in exactly the position of one that
    was never collected, and saying so is what lets the agent read it: the
    section is the only list the prompt gives it to read from, and dropping
    the contents silently used to drop the list along with them, leaving the
    agent told its files were pre-collected and shown none of them.
    """
    parts: list[str] = []
    contents = files.included if files is not None else data.file_contents
    files_to_include = [
        p for p in (file_filter or contents.keys())
        if p in contents
    ]
    if files_to_include:
        parts += ["", "### Changed file contents"]
        for path in files_to_include:
            perms = data.file_permissions.get(path, "?")
            parts.append(f"\n<file path=\"{path}\" permissions=\"{perms}\">")
            parts.append(contents[path])
            parts.append("</file>")

    omitted = data.omitted_files
    if file_filter:
        omitted = [p for p in omitted if p in set(file_filter)]
    if files is not None:
        omitted = files.omitted + [p for p in omitted if p not in data.file_contents]
    if omitted:
        parts += ["", "### Files not pre-collected (read directly)"]
        for path in omitted:
            parts.append(f"- {path}")
    return parts


def build_project_context(
    data: PreflightData,
    file_filter: list[str] | None = None,
) -> str:
    """The repository's own review guidance, scoped to ``file_filter``.

    CLAUDE.md, `.claude/architecture.md`, the review checklists, and the review
    profiles whose globs the filtered files match. An unfiltered call falls back
    to every profile rather than none, since a whole-review prompt has no group
    to match against.
    """
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
    files: FileFit | None = None,
    skip_project_context: bool = False,
    max_diff_bytes: int | None = None,
) -> str:
    """The "Pre-collected data" block a phase's prompt carries.

    ``file_filter`` scopes the diff and the file contents to one group's
    files. ``files`` is the budget's fit — `None` inlines everything
    ``file_filter`` admits, and a fit inlines only what it kept and names what
    it dropped. ``skip_project_context`` is the budget's other lever, and
    ``max_diff_bytes`` caps the diff itself — whatever each drops is named in
    the block rather than left out of it.
    """
    parts = [
        "## Pre-collected data",
        "",
        "Use this data directly. Do NOT re-read these files, re-run git diff, re-run git log,",
        "or re-fetch PR reviews via gh api. Only use Read/Bash for files NOT listed here",
        "(cross-references, callers, tests, config files outside the PR).",
    ]

    diff_text = scope_diff(data.diff, file_filter) if file_filter else data.diff
    diff_omitted: list[str] = []
    if max_diff_bytes is not None:
        cut = truncate_diff(diff_text, max_diff_bytes)
        diff_text, diff_omitted = cut.text, cut.omitted
    parts += ["", "### Full diff", "", "```diff", diff_text, "```"]

    if data.commit_log:
        parts += ["", "### Commit history", "", "```", data.commit_log, "```"]

    parts += _format_file_contents(data, file_filter, files=files)

    if diff_omitted:
        parts += ["", "### Diffs not pre-collected (use `git diff -- <path>` or Read tool)"]
        for path in diff_omitted:
            parts.append(f"- {path}")

    if not skip_project_context:
        project_ctx = build_project_context(data)
        if project_ctx:
            parts += ["", project_ctx]

    return "\n".join(parts)
