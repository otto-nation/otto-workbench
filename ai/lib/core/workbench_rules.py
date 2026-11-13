"""Machine-local coding-rule layers: override files, generated workbench.md, AGENTS.md.

`workbench-rules` is the CLI; this module owns the paths, the writes, and the
domain aliases. The generated layer is under the state root, the operator's
overrides under the config root, and the repo's `AGENTS.md` under the worktree
`project_root` names — never a walk up looking for a `.git` directory.
"""

# doc-group: platform

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import core.project_context
import core.serde
import core.workbench_paths

_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"

FRONTMATTER_DELIMITER = "---"
LOCAL_SUFFIX = ".local.md"
GENERATED_RULE_NAME = "workbench.md"
CONVENTIONS_HEADING = "## Conventions"

_DOMAIN_ALIASES = {
    "ts": "typescript",
    "js": "typescript",
    "py": "python",
    "sh": "bash",
    "shell": "bash",
    "yml": "yaml",
}


class RepoRootError(Enum):
    """Why `resolve_repo_root` could not name a worktree."""

    NOT_GIT = "not_git"
    NO_WORKTREE = "no_worktree"


@dataclass(frozen=True)
class RepoRoot:
    """The worktree a `project` command should write into, or why it cannot."""

    path: Path | None
    error: RepoRootError | None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.error is None


@dataclass(frozen=True)
class LocalRule:
    """One `*.local.md` in the override layer."""

    path: Path
    line_count: int

    @property
    def name(self) -> str:
        return self.path.name


def normalize_domain(domain: str) -> str:
    """Canonical domain name for aliases (`ts`/`js` → `typescript`, and so on)."""
    return _DOMAIN_ALIASES.get(domain, domain)


def workbench_dir() -> Path:
    """The workbench checkout whose rules tree `sync` reads.

    `WORKBENCH_DIR` wins, matching bash `lib/constants.sh`. Otherwise this
    code's own checkout.
    """
    override = os.environ.get("WORKBENCH_DIR")
    if override:
        return Path(override)
    checkout = core.workbench_paths.source_checkout()
    if checkout is not None:
        return checkout
    return Path(__file__).resolve().parents[3]


def user_rules_dir() -> Path:
    """Where `add` writes and `list`/`status` read: the operator's override layer."""
    return (core.workbench_paths.config_dir()
            / "overrides" / "ai" / "guidelines" / "rules")


def generated_rules_dir() -> Path:
    """Where `sync` writes `workbench.md`."""
    return core.workbench_paths.state_dir() / "rules"


def guidelines_rules_src_dir() -> Path:
    """The repo-shipped rule layer `sync` requires and `add` copies frontmatter from."""
    return workbench_dir() / "ai" / "guidelines" / "rules"


def ai_src_dir() -> Path:
    return workbench_dir() / "ai"


def newline_count(path: Path) -> int:
    """`wc -l`: newline characters, not logical lines."""
    return path.read_bytes().count(b"\n")


def write_frontmatter(path: Path, domain: str) -> None:
    """Create `path` with YAML frontmatter from the matching source rule.

    No-op when `path` already exists. An unknown domain, or a source with no
    frontmatter, yields an empty file.
    """
    if path.is_file():
        return
    source = guidelines_rules_src_dir() / f"{normalize_domain(domain)}.md"
    if not source.is_file():
        path.touch()
        return
    extracted = _extract_frontmatter(source)
    if extracted is None:
        path.touch()
        return
    path.write_text(extracted)


def add_local_rule(domain: str, rule: str) -> Path:
    """Append `rule` as a list item to `<domain>.local.md`, creating the file."""
    directory = user_rules_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{domain}{LOCAL_SUFFIX}"
    write_frontmatter(path, domain)
    with path.open("a") as handle:
        handle.write(f"- {rule}\n")
    return path


def list_local_rules() -> list[LocalRule]:
    """Every `*.local.md` in the override layer, sorted by name."""
    directory = user_rules_dir()
    if not directory.is_dir():
        return []
    found = [
        LocalRule(path, newline_count(path))
        for path in sorted(directory.glob(f"*{LOCAL_SUFFIX}"))
        if path.is_file()
    ]
    return found


def body_lines(path: Path) -> list[str]:
    """Non-empty lines of `path` with a leading YAML frontmatter block skipped."""
    in_frontmatter = False
    frontmatter_done = False
    lines: list[str] = []
    for line in path.read_text().splitlines():
        if in_frontmatter and line == FRONTMATTER_DELIMITER:
            in_frontmatter = False
            frontmatter_done = True
            continue
        if in_frontmatter:
            continue
        if not frontmatter_done and line == FRONTMATTER_DELIMITER:
            in_frontmatter = True
            continue
        if line == "":
            continue
        lines.append(line)
    return lines


def workbench_md_text() -> str:
    """The generated `workbench.md` body, with this machine's paths baked in."""
    root = workbench_dir()
    setup = ai_src_dir() / "setup.sh"
    return (
        f"# Workbench\n"
        f"\n"
        f"Your developer environment is managed at: {root}\n"
        f"\n"
        f"What it owns: Claude config (`ai/claude/`), coding rules (`ai/guidelines/rules/`),\n"
        f"bin scripts (`bin/`), zsh config (`zsh/`), git config (`git/`),\n"
        f"Docker setup (`docker/`), Packages (`packages/`).\n"
        f"\n"
        f"When modifying Claude config (MCPs, agents, skills, settings), bin scripts, zsh config,\n"
        f"git config, or developer tooling — make the change in the workbench repo and re-run\n"
        f"the relevant setup script. Do not edit `~/` directly.\n"
        f"\n"
        f"Re-run AI setup:   bash {setup}\n"
        f"Add a local rule:  workbench-rules add <domain> \"rule text\"\n"
        f"Edit local rules:  workbench-rules open [domain]\n"
        f"Review untracked:  workbench-rules status\n"
    )


def install_workbench_rule() -> Path:
    """Write `workbench.md` into the generated layer, atomically."""
    directory = generated_rules_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / GENERATED_RULE_NAME
    fd, tmp = tempfile.mkstemp(
        dir=directory, prefix=f"{GENERATED_RULE_NAME}.", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(fd, "w")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(workbench_md_text())
        core.serde.replace_file(Path(tmp), target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def append_project_rule(path: Path, rule: str) -> None:
    """Append `rule` under `## Conventions`, creating that section if missing."""
    text = path.read_text()
    if _has_conventions_heading(text):
        with path.open("a") as handle:
            handle.write(f"- {rule}\n")
        return
    with path.open("a") as handle:
        handle.write(f"\n{CONVENTIONS_HEADING}\n\n- {rule}\n")


def resolve_repo_root(directory: str | None = None) -> RepoRoot:
    """The working tree `directory` belongs in, or why a `project` command cannot write.

    `git_layout.project_root` collapses "not a repo" and "bare container with
    no worktree" to None; the CLI needs both messages, so a failed lookup is
    classified through `worktree_for`'s exit codes — the same split bash
    `project_root` returns as 2 vs any other non-zero.
    """
    if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
        sys.path.insert(0, str(_WORKBENCH_LIB))
    import git_layout

    start = directory or os.getcwd()
    found = git_layout.project_root(start)
    if found:
        return RepoRoot(Path(found), None)
    status = git_layout.worktree_for(start).status
    if status == git_layout.NOT_BARE:
        return RepoRoot(None, RepoRootError.NOT_GIT)
    return RepoRoot(None, RepoRootError.NO_WORKTREE)


def _extract_frontmatter(source: Path) -> str | None:
    in_fm = False
    chunks: list[str] = []
    for line in source.read_text().splitlines():
        if line != FRONTMATTER_DELIMITER and in_fm:
            chunks.append(line)
            continue
        if line != FRONTMATTER_DELIMITER:
            continue
        if not in_fm:
            in_fm = True
            chunks.append(FRONTMATTER_DELIMITER)
            continue
        chunks.append(FRONTMATTER_DELIMITER)
        break
    if not chunks:
        return None
    return "\n".join(chunks) + "\n\n"


def _has_conventions_heading(text: str) -> bool:
    return any(line.startswith(CONVENTIONS_HEADING) for line in text.splitlines())
