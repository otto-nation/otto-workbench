"""Fold the replayed commits into one before git rebase starts.

Never moves the branch. The squash commit is built with ``commit-tree`` and
picked in place of the original todo, so orig-head, abort and resume still
see the unsquashed tip — ``pr rebase --abort`` restores the original
commits. Each original is recorded as rewritten to that commit so
``git.replay.ReplayFinder`` and batch publish can follow the chain and
treat those originals as incorporated.

``rebase.squash`` is ``auto`` (the default), ``always``, or ``never``.
``auto`` squashes only in a squash-merge-only repo, when the replay would
conflict repeatedly on the same files, and when the branch has one author.
If the repo's merge settings cannot be read, it does not squash.
``always`` squashes anyway (including a multi-author branch).

The squash commit's subject is the PR title, or the first original subject
when there is no snapshot. The body is a bullet per original commit, then
every footer line from those originals, de-duplicated.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum

import config.workbench_config
from config.workbench_config import SquashPolicy
import core.log
from core.trail import Trail, tdecision, twarn
import gh.budget
import gh.landed
import gh.repo_settings
import git.client
import git.rewrites
import pr.context

from . import pr_snapshot as rebase_pr_snapshot
from .repo_regen import repo_root

# Pinned on the one `git rebase` that picks the squash commit. A user
# `rebase.missingCommitsCheck=error` otherwise rejects a todo that dropped
# the originals; `updateRefs=false` keeps `update-ref` lines from vanishing
# with them. `--continue` / `--skip` / resume never get these.
SQUASH_CONFIG = {
    "rebase.missingCommitsCheck": "ignore",
    "rebase.updateRefs": "false",
}

# Trailer-shaped lines, including `BREAKING CHANGE` which git's `%(trailers)`
# will not parse because the token has a space.
_FOOTER_LINE = re.compile(r"^(BREAKING CHANGE|[A-Za-z][A-Za-z0-9-]*): ")


class SquashSkip(StrEnum):
    """Why ``plan`` left the replay unsquashed."""

    NEVER = "never"
    TOO_FEW_COMMITS = "too_few_commits"
    NO_REPEATED_CONFLICT = "no_repeated_conflict"
    SETTINGS_UNREAD = "settings_unread"
    OTHER_MERGE_METHODS = "other_merge_methods"
    MULTIPLE_AUTHORS = "multiple_authors"


@dataclass(frozen=True)
class SquashPlan:
    """The squash commit to pick instead of the original todo."""

    base: str
    commit: str
    replaced: tuple[str, ...]

    @property
    def count(self) -> int:
        """How many original commits this fold replaces."""
        return len(self.replaced)

    def git_config(self) -> dict[str, str]:
        """`-c` pins for the one rebase that picks ``commit``."""
        return dict(SQUASH_CONFIG)

    def git_env(self) -> dict[str, str]:
        """Replace the todo with a single pick of the squash commit.

        Must outrank ``unattended_env``'s ``GIT_SEQUENCE_EDITOR=true``.
        git invokes the value as ``sh -c '$0 "$@"'`` with the todo path,
        so the trailing ``>`` writes that path.
        """
        return {"GIT_SEQUENCE_EDITOR": f"printf 'pick {self.commit}\\n' >"}


def _skip(trail: Trail | None, reason: SquashSkip) -> None:
    tdecision(trail, "squash", "not squashing", reason=reason)


def _last_paragraph(body: str) -> str:
    text = body.strip("\n")
    if not text:
        return ""
    parts = re.split(r"\n[ \t]*\n", text)
    return parts[-1] if parts else ""


def _message(
    cwd: str,
    commits: list[gh.landed.BranchCommit],
    snapshot: rebase_pr_snapshot.PRSnapshot | None,
) -> str:
    """Subject, then one bullet per original, then de-duplicated footer lines."""
    title = snapshot.title if snapshot is not None and snapshot.answered else ""
    subject = title or commits[0].subject
    bullets = [f"- {commit.subject}" for commit in commits]
    footer_set: dict[str, None] = {}
    for commit in commits:
        # %b, not %B: a body-less commit's last paragraph would otherwise be
        # its subject, and a conventional subject has a trailer's shape.
        body = git.client.out("log", "-1", "--format=%b", commit.sha, cwd=cwd)
        lines = _last_paragraph(body).splitlines()
        # dict keys: insertion-ordered de-duplication
        footer_set.update({ln: None for ln in lines if _FOOTER_LINE.match(ln)})
    parts = [subject, "", *bullets]
    if footer_set:
        parts.extend(["", *footer_set])
    return "\n".join(parts) + "\n"


def _build(cwd: str, base: str, first: str, message: str) -> str:
    """A commit with HEAD's tree, parent *base*, and *first*'s author.

    ``--no-gpg-sign`` because this object is intermediate; the pick writes
    S′ under the user's normal signing config, and an unattended run must
    not block on a passphrase prompt.
    """
    ident = git.client.out(
        "log", "-1", "--format=%an%x1f%ae%x1f%aI", first, cwd=cwd,
    )
    parts = ident.split("\x1f")
    name = parts[0] if parts else ""
    email = parts[1] if len(parts) > 1 else ""
    date = parts[2] if len(parts) > 2 else ""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
    }
    built = git.client.run(
        "commit-tree", "--no-gpg-sign", "HEAD^{tree}", "-p", base, "-F", "-",
        cwd=cwd, input_text=message, env=env,
    )
    return built.stdout.strip() if built.ok else ""


def _repeated_conflict_files(cwd: str, base: str, target_ref: str) -> tuple[str, ...]:
    """Files the target changed that two or more replayed commits also touch."""
    merge_base = git.client.out("merge-base", "HEAD", target_ref, cwd=cwd)
    target_files: set[str] = set()
    if merge_base:
        target_files = set(git.client.lines(
            "diff", "--name-only", "--no-renames", merge_base, target_ref,
            cwd=cwd,
        ))
    raw = git.client.out(
        "log", "--no-merges", "--no-renames", "--format=%x00%H", "--name-only",
        f"{base}..HEAD", cwd=cwd,
    )
    counts: dict[str, int] = {}
    for chunk in raw.split("\0"):
        lines = [line for line in chunk.splitlines() if line]
        if len(lines) < 2:
            continue
        for path in lines[1:]:
            counts[path] = counts.get(path, 0) + 1
    repeated = {path for path, n in counts.items() if n >= 2}
    return tuple(sorted(target_files & repeated))


def _author_emails(cwd: str, base: str) -> set[str]:
    return set(git.client.lines(
        "log", "--no-merges", "--format=%ae", f"{base}..HEAD", cwd=cwd,
    ))


def _record(cwd: str, originals: tuple[str, ...], squash: str) -> None:
    common = git.rewrites.common_dir(cwd)
    if common is None:
        return
    git.rewrites.record(
        common,
        [git.rewrites.Rewrite(old=old, new=squash) for old in originals],
    )


def plan(
    cwd: str, ctx: pr.context.ResolvedContext, *,
    target_ref: str, replay_from: str,
    snapshot: rebase_pr_snapshot.PRSnapshot | None,
    trail: Trail | None,
) -> SquashPlan | None:
    """The squash to pick for this replay, or None when the range stays as-is.

    Counts commits before reading config or calling GitHub, so an empty git
    stub (the fresh-path tests) means no squash and no extra I/O.
    """
    base = replay_from or git.client.out("merge-base", "HEAD", target_ref, cwd=cwd)
    if not base:
        return None
    commits = gh.landed.branch_commits(cwd, target_ref=base)
    if len(commits) < 2:
        _skip(trail, SquashSkip.TOO_FEW_COMMITS)
        return None

    policy = config.workbench_config.load_config_or_default(repo_root(cwd)).rebase.squash
    if policy is SquashPolicy.NEVER:
        _skip(trail, SquashSkip.NEVER)
        return None

    repeated: tuple[str, ...] = ()
    if policy is SquashPolicy.AUTO:
        repeated = _repeated_conflict_files(cwd, base, target_ref)
        if not repeated:
            _skip(trail, SquashSkip.NO_REPEATED_CONFLICT)
            return None
        if len(_author_emails(cwd, base)) > 1:
            _skip(trail, SquashSkip.MULTIPLE_AUTHORS)
            return None
        methods = gh.repo_settings.merge_methods(ctx.repo, cwd)
        if methods is None:
            latch = gh.budget.latched(gh.budget.resource_for(("repo",)))
            twarn(
                trail, "squash",
                "merge settings unreadable — not squashing",
                data={"remedy": latch.remedy() if latch else ""},
            )
            core.log.warn("Merge settings unreadable — not squashing.")
            _skip(trail, SquashSkip.SETTINGS_UNREAD)
            return None
        if not methods.squash_only:
            _skip(trail, SquashSkip.OTHER_MERGE_METHODS)
            return None

    message = _message(cwd, commits, snapshot)
    sha = _build(cwd, base, commits[0].sha, message)
    if not sha:
        core.log.warn("Could not build the squash commit — replaying unsquashed.")
        return None
    replaced = tuple(commit.sha for commit in commits)
    _record(cwd, replaced, sha)
    why = policy.value
    if policy is SquashPolicy.AUTO:
        why = f"{policy.value}: repeated {', '.join(repeated)}"
    tdecision(
        trail, "squash",
        f"squashing {len(replaced)} commits into one before the replay",
        reason=why,
        data={
            "base": base,
            "commit": sha,
            "replaced": list(replaced),
            "policy": policy.value,
            "repeated_files": list(repeated),
        },
    )
    core.log.info(f"Squashing {len(replaced)} commits into one before the replay.")
    return SquashPlan(base=base, commit=sha, replaced=replaced)
