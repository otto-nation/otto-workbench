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

import config.github_owners
import config.workbench_config
import gh.owners
import gh.owner_targets
import git.client
import git.remote_url

OVERRIDE_WARNING = (
    f"warning: {gh.owners.ALLOW_CROSS_REF_ENV}=1 is set; allowing a cross-group reference")
_SCISSORS = " ------------------------ >8 ------------------------"
_LOG_FMT = "%H%x00%B%x00"
_HEX = frozenset("0123456789abcdefABCDEF")


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


def _comment_char(cwd: str | Path) -> str:
    try:
        raw = git.client.out("config", "core.commentChar", cwd=cwd, default="")
    except FileNotFoundError:
        return "#"
    if len(raw) != 1:
        return "#"
    return raw


def _message_body(text: str, cwd: str | Path) -> str:
    comment = _comment_char(cwd)
    marker = comment + _SCISSORS
    kept: list[str] = []
    for line in text.splitlines():
        if line.startswith(marker):
            break
        if not line.startswith(comment):
            kept.append(line)
    return "\n".join(kept)


def _is_delete(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def _hex_sha(sha: str) -> bool:
    return bool(sha) and all(c in _HEX for c in sha)


def _is_github_remote(url: str, owner_map: gh.owners.OwnerMap) -> bool:
    host = git.remote_url.parse(url).host.lower()
    if not host:
        return False
    known = owner_map.hosts() | {
        config.github_owners.DEFAULT_HOST,
        gh.owner_targets.default_host({}),
    }
    return host in known or "github" in host


def _github_owner_remote(cwd: str | Path, owner_map: gh.owners.OwnerMap) -> bool:
    try:
        found = git.remote_url.remotes(cwd)
    except FileNotFoundError:
        return False
    if not found:
        return False
    return any(_is_github_remote(remote.url, owner_map) for remote in found)


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
    return _content_verdict(gh.owners.check_text(_message_body(text, cwd), cwd))


def _push_updates(ref_lines: Sequence[str]) -> list[tuple[str, str]] | HookVerdict:
    updates: list[tuple[str, str]] = []
    for line in ref_lines:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            return HookVerdict.cannot_run(
                f"unparseable pre-push ref line: {line.strip()}")
        local_sha, remote_sha = fields[1], fields[3]
        if _is_delete(local_sha):
            continue
        if not _hex_sha(local_sha):
            return HookVerdict.cannot_run(f"invalid local sha: {local_sha}")
        updates.append((local_sha, remote_sha))
    return updates


def _existing_tip(sha: str, cwd: str | Path) -> str | None:
    if _is_delete(sha) or not _hex_sha(sha):
        return None
    if git.client.ok("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd):
        return sha
    return None


def _parse_log(blob: str) -> list[tuple[str, str]]:
    parts = blob.split("\0")
    records: list[tuple[str, str]] = []
    index = 0
    while index + 1 < len(parts):
        sha, body = parts[index], parts[index + 1]
        if sha:
            records.append((sha, body))
        index += 2
    return records


def _commit_verdict(
        sha: str, body: str, group: str,
        owner_map: gh.owners.OwnerMap) -> HookVerdict:
    verdict = gh.owners.check_content(body, group, owner_map)
    if verdict.ok:
        return HookVerdict.allow()
    content = _content_verdict(verdict)
    if content.outcome is HookOutcome.ALLOW:
        return content
    subject = (body.splitlines() or [""])[0]
    return HookVerdict.refuse(
        f"{git.client.abbrev(sha)} {subject}\n{verdict.reason}")


def _check_new_commits(
        remote: str, local_sha: str, remote_sha: str, group: str,
        owner_map: gh.owners.OwnerMap, cwd: str | Path) -> HookVerdict:
    extra = []
    tip = _existing_tip(remote_sha, cwd)
    if tip:
        extra.append(tip)
    logged = git.client.run(
        "log", f"--format={_LOG_FMT}", local_sha,
        "--not", f"--remotes={remote}", *extra, cwd=cwd)
    if not logged.ok:
        return HookVerdict.cannot_run(
            f"git log failed: {logged.stderr.strip() or logged.stdout.strip()}")
    for sha, body in _parse_log(logged.stdout):
        item = _commit_verdict(sha, body, group, owner_map)
        if item.outcome is not HookOutcome.ALLOW or item.reason:
            return item
    return HookVerdict.allow()


def check_push(
        remote: str, url: str, ref_lines: Sequence[str],
        cwd: str | Path) -> HookVerdict:
    """Whether commits about to be pushed to *remote* at *url* may go.

    *ref_lines* are git's ``<local ref> <local sha> <remote ref> <remote sha>``
    stdin lines. Deletes are skipped. Only commits new to *remote* are scanned.
    A destination that is not a GitHub remote is allowed without scanning.
    """
    loaded = _owner_map()
    if isinstance(loaded, HookVerdict):
        return loaded
    if not loaded.configured:
        return HookVerdict.allow()
    if not _is_github_remote(url, loaded):
        return HookVerdict.allow()
    owner = gh.owner_targets.remote_owner(
        url, gh.owner_targets.default_host({}), loaded.hosts())
    if owner is None:
        return HookVerdict.allow()
    group = loaded.classify(owner).name
    parsed = _push_updates(ref_lines)
    if isinstance(parsed, HookVerdict):
        return parsed
    for local_sha, remote_sha in parsed:
        item = _check_new_commits(
            remote, local_sha, remote_sha, group, loaded, cwd)
        if item.outcome is not HookOutcome.ALLOW or item.reason:
            return item
    return HookVerdict.allow()
