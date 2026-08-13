"""Shared PR context resolution.

Resolves repo, branch, PR number, worktree root, and HEAD SHA once
per invocation. Replaces the duplicated discovery logic in ci-check,
review-threads, and review_common.detect_repo().
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace as dataclass_replace
from pathlib import Path

import log
import pr_target

_PR_URL_RE = re.compile(r"/pull/(\d+)")
_PR_NUMBER_RE = re.compile(r"^\d+$")

RESOLVE_BRANCH = Path(__file__).resolve().parent.parent.parent / "bin" / "resolve-branch"


def is_pr_ref(s: str) -> bool:
    """True if *s* looks like a PR number or GitHub PR URL."""
    return bool(_PR_URL_RE.search(s) or _PR_NUMBER_RE.match(s))


def classify_target(target: str) -> tuple[str | None, str | None]:
    """Classify an ambiguous positional as (pr, branch).

    Returns a 2-tuple where exactly one element is the input string
    and the other is None.
    """
    if is_pr_ref(target):
        return target, None
    return None, target


@dataclass(frozen=True)
class ResolvedContext:
    """Immutable PR context resolved once at command entry."""
    repo: str
    branch: str
    pr_number: int | None
    worktree_root: Path | None
    head_sha: str
    current_branch: str | None = None
    # Where the run's bookkeeping lives, as opposed to where git runs. Keyword-
    # only and required: a caller that forgets it would silently get a context
    # whose state and lock point nowhere.
    target_dir: Path = field(kw_only=True)

    def require_worktree(self) -> Path:
        """The worktree root, or exit 1 naming what to do about its absence.

        ``worktree_root`` is legitimately None — a bare repo with no worktree
        checked out on the branch has nowhere to read state from or run git in.
        Every consumer that cannot work without one calls this instead of
        dereferencing the field, so the failure is one message here rather than
        a TypeError, a ``FileNotFoundError: 'None'``, or the string "None"
        reaching ``git -C`` several frames later.

        Consumers that can degrade (ci-check without --fix, pr create, pr gc)
        read the field directly and are visibly opted out.
        """
        if self.worktree_root is None:
            log.error(
                f"No worktree for {self.branch!r} — "
                f"run: wt switch {self.branch} (or pass --repo-dir)"
            )
            sys.exit(1)
        return self.worktree_root


def resolve(
    *,
    pr: str | None = None,
    branch: str | None = None,
    repo_dir: str | None = None,
) -> ResolvedContext:
    """Resolve PR context from arguments and git state.

    Resolution order:
    1. --pr given: derive branch and repo from the PR.
    2. --branch given: use directly, detect repo from remote.
    3. Neither: detect everything from current git state.

    Raises ValueError if both pr and branch are given.
    """
    if pr is not None and branch is not None:
        raise ValueError("--pr and --branch are mutually exclusive")

    cwd = repo_dir

    worktree_root, cwd = _resolve_worktree(cwd, pr=pr, branch=branch)

    repo = _detect_repo(cwd)

    if pr:
        pr_number = _parse_pr_input(pr)
        branch_name, pr_sha = _pr_head(repo, pr_number)
        if not branch_name or not pr_sha:
            log.error(
                f"Cannot resolve the head branch of {repo}#{pr_number} — "
                f"pr keys a run's state and lock on its target branch and "
                f"stamps state with its head SHA"
            )
            sys.exit(1)
        # The PR's HEAD, not the caller's: state written for this run belongs to
        # the PR, and the caller may be sitting on an unrelated branch.
        head_sha = pr_sha
    elif branch:
        branch_name = _resolve_branch(branch, cwd)
        pr_number = _pr_from_branch(repo, branch_name)
        head_sha = _head_sha(cwd) if worktree_root else ""
    else:
        branch_name = _current_branch(cwd)
        pr_number = _pr_from_current(cwd)
        head_sha = _head_sha(cwd) if worktree_root else ""

    current = _current_branch_quiet(cwd) if worktree_root else None

    return ResolvedContext(
        repo=repo,
        branch=branch_name,
        pr_number=pr_number,
        worktree_root=worktree_root,
        head_sha=head_sha,
        current_branch=current,
        target_dir=pr_target.target_dir(_target_repo_key(cwd), branch_name),
    )


def _target_repo_key(cwd: str | None) -> str:
    """The repo half of the target key, or exit 1.

    Fatal rather than falling back, and affordable because it is: _detect_repo
    has already exited 1 above if this is not a repo `gh` can name.
    """
    key = pr_target.repo_key_from_origin(cwd)
    if key:
        return key
    log.error(
        "Cannot read the origin remote — pr keys a run's state and lock on "
        "(origin repo, branch)"
    )
    sys.exit(1)


def _worktree_is_dirty(cwd: str) -> bool:
    """Whether *cwd* has uncommitted changes."""
    r = subprocess.run(
        ["git", "-C", cwd, "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _unpushed_count(cwd: str, branch: str) -> int:
    """Commits in *cwd* that origin/*branch* does not have.

    Only meaningful once origin/<branch> has been fetched — against a stale
    remote ref this over-reports, which is the safe direction for callers
    using it to decide whether a hard reset would destroy work.
    """
    r = subprocess.run(
        ["git", "-C", cwd, "rev-list", f"origin/{branch}..HEAD", "--count"],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    return int(r.stdout.strip())


def _reset_blocker(wt_path: str, branch: str) -> str | None:
    """Why hard-resetting *wt_path* to origin/*branch* would destroy work.

    Returns None when the reset is safe. Call only after fetching the branch.
    """
    current = _current_branch_quiet(wt_path)
    if current != branch:
        return f"it is on {current or 'detached HEAD'}, not {branch}"
    if _worktree_is_dirty(wt_path):
        return "it has uncommitted changes"
    unpushed = _unpushed_count(wt_path, branch)
    if unpushed:
        return f"it has {unpushed} unpushed commit(s)"
    return None


def fetch_and_reset(wt_path: str, branch: str) -> None:
    """Fetch branch from origin and hard-reset worktree to match.

    Skips the reset unless *wt_path* is actually on *branch*, clean, and fully
    pushed. Callers locate this worktree by branch, but find_worktree_for_branch
    can return one that merely has the right directory name — resetting it
    unconditionally destroys whatever is actually checked out there.
    """
    try:
        subprocess.run(
            ["git", "-C", wt_path, "fetch", "origin", branch],
            capture_output=True, text=True, check=True,
        )
    except Exception:
        return
    blocker = _reset_blocker(wt_path, branch)
    if blocker:
        log.warn(f"Not resetting {wt_path} to origin/{branch} — {blocker}")
        return
    try:
        subprocess.run(
            ["git", "-C", wt_path, "reset", "--hard", f"origin/{branch}"],
            capture_output=True, text=True,
        )
    except Exception:
        pass


def head_sha(cwd: str | None = None) -> str:
    """Current HEAD sha of the worktree at *cwd*, or "" if it can't be read.

    Use this when the worktree may have changed since ``resolve()`` — checking
    out a PR or branch can move HEAD after the context was captured.
    """
    return _head_sha(cwd)


def update_to_remote(ctx: ResolvedContext) -> ResolvedContext:
    """Fetch branch from remote and reset worktree to match, safely.

    Skips when the worktree has uncommitted changes or unpushed commits.
    Returns a new context with the updated head_sha when reset succeeds.
    """
    if not ctx.worktree_root or not ctx.branch:
        return ctx

    cwd = str(ctx.worktree_root)

    current = _current_branch_quiet(cwd)
    if current != ctx.branch:
        log.info(f"Worktree is on {current}, not {ctx.branch} — skipping update to remote")
        return ctx

    if _worktree_is_dirty(cwd):
        log.warn("Worktree has uncommitted changes — skipping update to remote")
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "fetch", "origin", ctx.branch],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--verify", f"origin/{ctx.branch}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ctx
    remote_sha = r.stdout.strip()

    local_sha = _head_sha(cwd)
    if local_sha == remote_sha:
        return ctx

    unpushed = _unpushed_count(cwd, ctx.branch)
    if unpushed > 0:
        log.warn(f"Branch has {unpushed} unpushed commit(s) — skipping update to remote")
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "reset", "--hard", f"origin/{ctx.branch}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.warn(f"reset --hard failed — keeping existing worktree state")
        return ctx

    log.info(f"Updated worktree to origin/{ctx.branch}")
    return dataclass_replace(ctx, head_sha=remote_sha)


def _current_branch_quiet(cwd: str | None = None) -> str | None:
    """Return current branch name, or None on failure (e.g. detached HEAD)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip() or r.stdout.strip() == "HEAD":
        return None
    return r.stdout.strip()


def _find_worktree_by_branch(
    branch_hint: str, cwd: str,
) -> Path | None:
    """Find a worktree for branch_hint, trying exact then fuzzy resolution."""
    wt = find_worktree_for_branch(branch_hint, cwd)
    if wt:
        return wt
    resolved = _resolve_branch(branch_hint, cwd)
    if resolved != branch_hint:
        return find_worktree_for_branch(resolved, cwd)
    return None


def _redirect_to_branch_worktree(
    branch: str, effective_cwd: str,
) -> Path | None:
    """If CWD's branch differs from the target, find the target's worktree."""
    current = _current_branch_quiet(effective_cwd)
    if current is None or current == branch:
        return None
    return _find_worktree_by_branch(branch, effective_cwd)


def _resolve_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve worktree root, handling bare repos transparently."""
    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return _resolve_non_worktree(cwd, pr=pr, branch=branch)

    if branch:
        wt = _redirect_to_branch_worktree(branch, cwd or str(toplevel))
        if wt:
            return wt, str(wt)
    return toplevel, cwd


def _resolve_non_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Handle bare repos and non-git directories."""
    if is_bare_repo(cwd):
        return _resolve_bare(cwd, pr=pr, branch=branch)

    if not pr and not branch:
        log.error("Not in a git repository")
        sys.exit(1)
    return None, cwd


def _resolve_bare(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve worktree from a bare repo."""
    wt = resolve_bare_repo_worktree(cwd, branch)
    if wt:
        return wt, str(wt)
    if not pr and not branch:
        log.error("Bare repository — pass --branch or --repo-dir")
        sys.exit(1)
    return None, cwd


def _detect_repo(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        log.error("Cannot determine repository from git remote")
        sys.exit(1)
    return r.stdout.strip()


def _current_branch(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        log.error("Cannot determine current branch")
        sys.exit(1)
    if branch == "HEAD":
        log.error("Cannot determine current branch — HEAD is detached")
        sys.exit(1)
    return branch


def _resolve_branch(hint: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(
            [str(RESOLVE_BRANCH), hint],
            capture_output=True, text=True, cwd=cwd,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        # resolve-branch exited non-zero or returned nothing — use hint as-is
        # rather than silently discarding the user's explicit --branch value
        log.warn(f"resolve-branch: could not resolve {hint!r}, using as-is")
        return hint
    except FileNotFoundError:
        return hint if hint else _current_branch(cwd)


def _git_toplevel(cwd: str | None = None) -> Path | None:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip())


def _head_sha(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    return r.stdout.strip()


def _parse_pr_input(pr_input: str) -> int:
    """Extract PR number from URL or raw number."""
    m = _PR_URL_RE.search(pr_input)
    if m:
        return int(m.group(1))
    if _PR_NUMBER_RE.match(pr_input):
        return int(pr_input)
    raise ValueError(
        f"Cannot parse PR number from {pr_input!r} — expected a number or GitHub PR URL"
    )


def _pr_from_current(cwd: str | None = None) -> int | None:
    r = subprocess.run(
        ["gh", "pr", "view", "--json", "number", "-q", ".number"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _pr_from_branch(repo: str, branch: str) -> int | None:
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch,
         "--json", "number", "--jq", ".[0].number"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _pr_head(repo: str, pr_number: int) -> tuple[str | None, str]:
    """The PR's head branch and head SHA, in one API call.

    Both in one request because the SHA is what a PR target's state must be
    stamped with — reading it from the caller's HEAD is how #2973's state ended
    up carrying the repo root's SHA.
    """
    r = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo,
         "--json", "headRefName,headRefOid",
         "--jq", '.headRefName + " " + .headRefOid'],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None, ""
    parts = r.stdout.split()
    if len(parts) != 2:
        return None, ""
    return parts[0], parts[1]


# ── Bare-repo helpers ──────────────────────────────────────────────────────


def is_bare_repo(cwd: str | None = None) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, cwd=cwd,
        )
        return r.stdout.strip() == "true"
    except Exception:
        return False


def _parse_worktree_block(block: str) -> tuple[Path, str | None] | None:
    """One ``--porcelain`` record as ``(path, branch)``, or None if not a checkout.

    Branch is None for a detached HEAD. The bare repo is not a checkout and is
    dropped — handing it back as one would point callers at the .git directory.
    """
    path: Path | None = None
    branch: str | None = None
    for line in block.splitlines():
        if line == "bare":
            return None
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
    return (path, branch) if path else None


def _worktree_entries(cwd: str | None = None) -> list[tuple[Path, str | None]]:
    """Every non-bare worktree as ``(path, branch)``; branch None when detached.

    Parses ``--porcelain`` rather than the human listing, which packs path,
    SHA and ``[branch]`` onto one whitespace-separated line: a path containing
    a space gets truncated by a naive split, and one containing a bracket reads
    as a branch tag. Porcelain gives the path verbatim on its own line.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, cwd=cwd,
        )
    except Exception:
        return []
    parsed = map(_parse_worktree_block, r.stdout.split("\n\n"))
    return [entry for entry in parsed if entry]


def find_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Find the worktree git reports as checked out on *branch*.

    Falls back to a worktree whose directory name matches the sanitized branch
    (slashes to dashes) so detached-HEAD worktrees are still found — but only
    when git reports no branch for it. A worktree named ``main/`` with someone
    else's branch checked out is not the main worktree, and callers that reset
    or check out what they get back would clobber that branch.

    Use find_worktree_dir_named when you only need a working directory.
    """
    entries = _worktree_entries(cwd)
    for path, wt_branch in entries:
        if wt_branch == branch:
            return path
    sanitized = branch.replace("/", "-")
    for path, wt_branch in entries:
        if wt_branch is None and path.name == sanitized:
            return path
    return None


def find_worktree_dir_named(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Any worktree whose directory is named after *branch*, whatever is in it.

    Answers "which directory is this repo's <branch> checkout" rather than
    "which worktree is on <branch>". Only for callers that need somewhere to
    run read-only commands — never for ones that reset or check out the result,
    which is how a feature branch parked in main/ got hard-reset away.
    """
    sanitized = branch.replace("/", "-")
    for path, _ in _worktree_entries(cwd):
        if path.name == sanitized:
            return path
    return None


def create_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Create a worktree for *branch*, or None if it can't be created.

    Delegates to ``wt switch`` so the worktree lands wherever worktrunk's
    path template puts it, keeping tooling-created worktrees in the same
    layout as hand-created ones.
    """
    path = wt_switch(branch, cwd)
    if not path:
        log.warn(f"Could not create a worktree for {branch}")
        return None
    log.info(f"Created worktree for {branch} at {path}")
    return Path(path)


def wt_switch(ref: str, cwd: str | None = None) -> str | None:
    """Path of the worktree ``wt switch`` lands on for *ref*, or None.

    *ref* is anything worktrunk accepts — a branch name or a ``pr:<n>`` ref.
    Non-interactive and hook-free so it is safe to call from tooling.
    """
    try:
        r = subprocess.run(
            ["wt", "switch", ref, "--no-cd", "--no-hooks", "--format", "json", "-y"]
            + (["-C", cwd] if cwd else []),
            capture_output=True, text=True,
        )
    except Exception:
        log.warn("worktrunk (wt) is not available — cannot switch worktrees")
        return None
    return parse_wt_switch_path(r.stdout)


def parse_wt_switch_path(stdout: str) -> str | None:
    """Pull the ``path`` field out of ``wt switch --format json`` output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            path = json.loads(line).get("path", "")
        except json.JSONDecodeError:
            continue
        if path:
            return path
    return None


def default_branch(cwd: str | Path | None = None) -> str:
    """The repo's default branch name, from origin/HEAD.

    Falls back to "main" whenever git cannot answer — an unfetched clone has no
    origin/HEAD, and every caller needs a base ref more than it needs an error.

    Deliberately uncached: this is imported by every `pr` script, and a
    module-level cache here would outlive the tests that set up their own repos.
    Call sites that need it per-file wrap it in their own cache.
    """
    try:
        ref = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, cwd=cwd,
        ).stdout.strip()
    except Exception:
        return "main"
    return ref.replace("refs/remotes/origin/", "") if ref else "main"


def resolve_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """Best-effort worktree discovery for bare repos.

    Tries the requested branch first (with fuzzy resolution), then creates a
    worktree for it. Only falls back to the default branch's worktree when no
    branch was requested at all.

    Never substitutes another branch's worktree for an explicitly requested
    branch: callers check the branch out, so handing back the default branch's
    worktree makes them displace it. See create_worktree_for_branch.
    """
    if branch:
        wt = _find_worktree_by_branch(branch, cwd)
        if wt:
            return wt
        return create_worktree_for_branch(branch, cwd)

    # With no branch requested this is "give me a working directory for this
    # repo", not "is this worktree on <default>" — so the directory named after
    # the default branch will do even when something else is checked out there.
    # Returning None instead would strand ~10 callers that dereference
    # worktree_root without a guard.
    default = default_branch(cwd)
    return (
        find_worktree_for_branch(default, cwd)
        or find_worktree_dir_named(default, cwd)
    )
