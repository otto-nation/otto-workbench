"""SessionStart hook: reuse level, ceiling nudge, issue tracker, PR template.

Five responsibilities, in the order run() handles them:
1. Emit the active reuse level as session context
2. Register the repo in the project registry
3. Run ceiling-scan --json; if no-trigger markers exist, nudge
4. Emit the repo's issue tracker, configured or not
5. Emit the repo's PR template and the sections it requires

The registration rides along here because this hook already resolves the repo
root for the ceiling scan, so it costs nothing extra and it is the cheapest
observation of "a workbench-managed session ran in this repo" the machine has.

Not: the reuse vocabulary (``config.reuse_levels``), the project registry
(``config.workbench_projects``), PR template resolution (``core.pr_template``),
the marker scan itself (``ai/bin/ceiling-scan``).
"""

# doc-group: platform

from __future__ import annotations

import json
import sys
from pathlib import Path

import core.pr_template
import core.proc
import core.timeouts
import config.workbench_projects
import config.reuse_levels
import config.workbench_config
# git_layout is reached through config.workbench_config's insert of <root>/lib.
import git_layout  # noqa: E402

# ceiling-scan is harness-neutral and lives in ai/bin, not under ai/claude/.
CEILING_SCAN = Path(__file__).resolve().parents[2] / "bin" / "ceiling-scan"

def _repo_root() -> str | None:
    """The working tree this session started in, or None outside one.

    Claude Code roots a session wherever it was launched, and for a bare-repo
    checkout that is routinely the container — the directory holding the bare
    `.git` with every worktree as a peer. `rev-parse --show-toplevel` exits 128
    there, so asking only git returned None for the common case and this hook
    emitted nothing at all: no issue tracker, no ceiling nudge, silently.

    A container is resolved to the checkout on its default branch, because a
    container is the wrong argument for both callers downstream. The project
    registry refuses one outright, and the config loader answers *differently*
    rather than failing — `container_dir` of a container is None, so the
    container's own `.workbench.yml` is read as the project scope and the
    worktree's committed one is never seen.

    A container naming no worktree stays None. There is no tree to read, and
    guessing a checkout is the one thing `resolve-worktree`'s exit 1 exists to
    prevent.

    `git_layout` owns both halves of that resolution, so this asks it rather
    than spelling the working-tree-then-container fallback out again.
    """
    return git_layout.project_root()

def _ceiling_counts(repo: str) -> dict | None:
    """What ceiling-scan makes of the repo, or None if it could not answer.

    The scan is a local AST walk over the tree, so it is bounded like any other
    flat-cost local read: a breach means something is wrong, not that the repo
    is large.
    """
    try:
        result = core.proc.run(
            [sys.executable, str(CEILING_SCAN), "--json", repo],
            timeout=core.timeouts.LOCAL,
        )
    except FileNotFoundError:
        return None
    if not result.ok:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

def _issues_line(repo: str) -> str:
    """Where this repo files issues, as a line the agent reads in context.

    Emitted in both states, unlike the reuse level. A rule that says "go look
    up issues.provider" is a rule that gets skipped, so the resolved
    answer goes into context instead of behind a lookup — and "nobody has
    said" is the state the agent most needs to act on.

    The unconfigured line names the command rather than the file. Told only
    the key, an agent writes the shared config by hand under whatever name the
    worktree in front of it happens to use; the command is what checks that
    name against the workbench doing the reading.
    """
    provider = config.workbench_config.load_config_or_default(repo).issues.provider
    if provider is None:
        return (
            "Issue tracker: not configured — ask before filing, then record the"
            " answer by running otto-workbench config set"
            f" {config.workbench_config.ISSUE_PROVIDER_KEY} PROVIDER"
        )
    return f"Issue tracker: {provider}"

def _pr_template_line(repo: str) -> str:
    """Which PR template this repo ships, and what sections it requires.

    A derived fact, not a configured one — and that is why it is emitted rather
    than left to be looked up. The template is six `stat` calls away, so every
    session that opens or updates a PR rediscovered it: grep `.github/`, read
    the file, infer the headers. Once per session, for an answer that does not
    change within one.

    Recording it in `.workbench.yml` instead would cache a filesystem fact in a
    second place and be wrong the moment someone added, moved or deleted the
    file. So the line is derived on every session start, from the same
    `core.pr_template` that `task pr:create` and `pr describe` resolve through
    — what the agent is told and what the automation enforces cannot disagree.

    The headers are named, not just the path. The path alone is another lookup;
    the sections are what an agent writing a PR body actually needs, and they
    are short enough to spend the context on.
    """
    template = core.pr_template.load(Path(repo))
    sections = ", ".join(template.headers) or "no sections"
    if not template.found:
        return f"PR template: none in this repo — use the fallback ({sections})"
    return f"PR template: {template.path} ({sections})"

def run() -> None:
    lines: list[str] = []

    level = config.reuse_levels.read_level()
    if level != config.reuse_levels.read_default():
        lines.append(f"Reuse level: {level} — {config.reuse_levels.LEVEL_DESCRIPTIONS.get(level, '')}")

    repo = _repo_root()
    config.workbench_projects.register(repo)
    counts = _ceiling_counts(repo) if repo else None
    if counts and counts.get("no_trigger", 0) > 0:
        total = counts["total"]
        no_trigger = counts["no_trigger"]
        lines.append(
            f"{total} ceiling marker(s), {no_trigger} with no trigger"
            " — run /ceiling-debt to review"
        )

    if repo:
        lines.append(_issues_line(repo))
        lines.append(_pr_template_line(repo))

    if lines:
        print("\n".join(lines))

