"""Git hook checks for cross-group references in commit messages.

``owners-check --message-file`` and ``--push`` call here. A check that cannot
run is ``CANNOT_RUN`` so the hook can warn and allow; only a content refusal
is ``REFUSE``.
"""

# doc-group: platform

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import config.workbench_config
import gh.owners
import gh.owner_targets
import git.client
import git.remote_url

OVERRIDE_WARNING = (
    f"warning: {gh.owners.ALLOW_CROSS_REF_ENV}=1 is set; allowing a cross-group reference")


class HookOutcome(Enum):
    """What a hook-mode check decided."""

    ALLOW = auto()
    REFUSE = auto()
    CANNOT_RUN = auto()


@dataclass(frozen=True)
class HookVerdict:
    """Hook-mode result: outcome plus the reason to print on stderr."""

    outcome: HookOutcome
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> HookVerdict:
        return cls(HookOutcome.ALLOW, reason)

    @classmethod
    def refuse(cls, reason: str) -> HookVerdict:
        return cls(HookOutcome.REFUSE, reason)

    @classmethod
    def cannot_run(cls, reason: str) -> HookVerdict:
        return cls(HookOutcome.CANNOT_RUN, reason)


def _owner_map() -> gh.owners.OwnerMap | HookVerdict:
    try:
        return gh.owners.OwnerMap.load()
    except config.workbench_config.ConfigError as exc:
        return HookVerdict.cannot_run(str(exc))


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("#"))


def _is_delete(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def _github_owner_remote(cwd: str | Path, owner_map: gh.owners.OwnerMap) -> bool:
    try:
        found = git.remote_url.remotes(cwd)
    except FileNotFoundError:
        return False
    if not found:
        return False
    host = gh.owner_targets.default_host({})
    known = owner_map.hosts()
    return any(
        gh.owner_targets.remote_owner(remote.url, host, known) is not None
        for remote in found)


def _content_verdict(verdict: gh.owners.Verdict) -> HookVerdict:
    if verdict.ok:
        return HookVerdict.allow()
    if verdict.references and gh.owners.cross_ref_allowed_by_user():
        return HookVerdict.allow(OVERRIDE_WARNING)
    return HookVerdict.refuse(verdict.reason)


def check_message(text: str, cwd: str | Path) -> HookVerdict:
    """Whether a commit message may be recorded in the checkout at *cwd*."""
    loaded = _owner_map()
    if isinstance(loaded, HookVerdict):
        return loaded
    if not loaded.configured:
        return HookVerdict.allow()
    if not _github_owner_remote(cwd, loaded):
        return HookVerdict.allow()
    return _content_verdict(gh.owners.check_text(_strip_comments(text), cwd))


def _push_shas(ref_lines: Sequence[str]) -> list[str] | HookVerdict:
    shas: list[str] = []
    for line in ref_lines:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            return HookVerdict.cannot_run(
                f"unparseable pre-push ref line: {line.strip()}")
        if _is_delete(fields[1]):
            continue
        shas.append(fields[1])
    return shas


def _check_commit(
        sha: str, group: str, owner_map: gh.owners.OwnerMap,
        cwd: str | Path) -> HookVerdict:
    logged = git.client.run("log", "-1", "--format=%B", sha, cwd=cwd)
    if not logged.ok:
        return HookVerdict.cannot_run(
            f"git log failed for {sha}: {logged.stderr.strip() or logged.stdout.strip()}")
    verdict = gh.owners.check_content(logged.stdout, group, owner_map)
    if verdict.ok:
        return HookVerdict.allow()
    content = _content_verdict(verdict)
    if content.outcome is HookOutcome.ALLOW:
        return content
    subject = (logged.stdout.splitlines() or [""])[0]
    return HookVerdict.refuse(
        f"{git.client.abbrev(sha)} {subject}\n{verdict.reason}")


def _check_new_commits(
        remote: str, local_sha: str, group: str, owner_map: gh.owners.OwnerMap,
        cwd: str | Path) -> HookVerdict:
    listed = git.client.run(
        "rev-list", local_sha, "--not", f"--remotes={remote}", cwd=cwd)
    if not listed.ok:
        return HookVerdict.cannot_run(
            f"git rev-list failed: {listed.stderr.strip() or listed.stdout.strip()}")
    for sha in listed.stdout.split():
        item = _check_commit(sha, group, owner_map, cwd)
        if item.outcome is not HookOutcome.ALLOW or item.reason:
            return item
    return HookVerdict.allow()


def check_push(
        remote: str, url: str, ref_lines: Sequence[str],
        cwd: str | Path) -> HookVerdict:
    """Whether commits about to be pushed to *remote* at *url* may go.

    *ref_lines* are git's ``<local ref> <local sha> <remote ref> <remote sha>``
    stdin lines. Deletes are skipped. Only commits new to *remote* are scanned.
    """
    loaded = _owner_map()
    if isinstance(loaded, HookVerdict):
        return loaded
    if not loaded.configured:
        return HookVerdict.allow()
    owner = gh.owner_targets.remote_owner(
        url, gh.owner_targets.default_host({}), loaded.hosts())
    if owner is None:
        return HookVerdict.allow()
    group = loaded.classify(owner).name
    parsed = _push_shas(ref_lines)
    if isinstance(parsed, HookVerdict):
        return parsed
    for local_sha in parsed:
        item = _check_new_commits(remote, local_sha, group, loaded, cwd)
        if item.outcome is not HookOutcome.ALLOW or item.reason:
            return item
    return HookVerdict.allow()
