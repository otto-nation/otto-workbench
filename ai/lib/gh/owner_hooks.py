"""Git hook checks for cross-group references in commit messages.

``owners-check --message-file`` and ``--push`` call here. A check that cannot
run is ``CANNOT_RUN`` so the hook can warn and allow; only a content refusal
is ``REFUSE``.
"""

# doc-group: platform

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import config.github_owners
import config.workbench_config
import core.log
import gh.owners
import gh.owner_targets
import git.client
import git.push
import git.remote_url

_SCISSORS_RE = re.compile(r"^\S ------------------------ >8 ------------------------$")
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
    override: bool = False

    @classmethod
    def allow(cls, reason: str = "", override: bool = False) -> HookVerdict:
        return cls(HookOutcome.ALLOW, reason, override)

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
    kept: list[str] = []
    for line in text.splitlines():
        if _SCISSORS_RE.match(line):
            break
        if not line.startswith(comment):
            kept.append(line)
    return "\n".join(kept)


def _is_delete(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def _hex_sha(sha: str) -> bool:
    return bool(sha) and all(c in _HEX for c in sha)


def _is_github_remote(url: str, owner_map: gh.owners.OwnerMap) -> bool:
    parsed = git.remote_url.parse(url)
    # A dotless scp alias parses as host ""; the github-substring check uses the alias.
    host = (parsed.host or git.remote_url.ssh_alias(url)).lower()
    if not host:
        return False
    known = owner_map.hosts() | {
        config.github_owners.DEFAULT_HOST,
        gh.owner_targets.default_host({}),
    }
    if host in known:
        return True
    if parsed.ssh and git.remote_url.ssh_hostname(git.remote_url.ssh_alias(url)) in known:
        return True
    # ceiling: a host containing "github" is treated as GitHub so an alias ssh
    # cannot resolve to a known host (github-work) is still checked. A
    # non-GitHub host whose name contains the substring is over-checked.
    # Upgrade trigger: if a configured remote is a gitlab ssh alias whose name
    # contains github.
    return "github" in host


def _github_owner_remote(cwd: str | Path, owner_map: gh.owners.OwnerMap) -> bool:
    try:
        found = git.remote_url.remotes(cwd)
    except FileNotFoundError:
        return False
    if not found:
        return False
    return any(_is_github_remote(remote.url, owner_map) for remote in found)


@dataclass(frozen=True)
class _Destination:
    """The GitHub owner a commit is going to, or a fallback/allow decision."""

    owner: config.github_owners.Owner | None = None
    scan: bool = True


@dataclass(frozen=True)
class _PushUpdate:
    """One pre-push ref line's local and remote shas."""

    local_sha: str
    remote_sha: str


@dataclass(frozen=True)
class _LogRecord:
    """One commit from ``git log``: sha and message body."""

    sha: str
    body: str


def _remote_names(cwd: str | Path) -> list[str]:
    return git.client.lines("remote", cwd=cwd)


def _remote_from_push_ref(full: str, names: Sequence[str]) -> str | None:
    """The longest remote name ``refs/remotes/<name>/`` prefixes *full*."""
    prefix = "refs/remotes/"
    if not full.startswith(prefix):
        return None
    matches = [
        name for name in names
        if name and full.startswith(f"{prefix}{name}/")
    ]
    if not matches:
        return None
    return max(matches, key=len)


def _configured_push_remote(cwd: str | Path, names: Sequence[str]) -> str | None:
    """The remote git's config sends the current branch to, if it names one in *names*.

    git's order: ``branch.<b>.pushRemote``, ``remote.pushDefault``, then
    ``branch.<b>.remote`` — the remote the branch tracks, which still decides
    the push when ``@{push}`` cannot resolve (its tracking ref is gone, or a
    new branch's ref was never fetched). A detached HEAD has no branch keys.
    ``branch.<b>.remote`` is ``.`` for a branch tracking a local one, which is
    not a remote name and falls through.
    """
    branch = git.client.out(
        "symbolic-ref", "--short", "-q", "HEAD", cwd=cwd, default="")
    keys = ("remote.pushDefault",)
    if branch:
        keys = (f"branch.{branch}.pushRemote", "remote.pushDefault",
                f"branch.{branch}.remote")
    for key in keys:
        named = git.client.out("config", "--get", key, cwd=cwd, default="")
        if named and named in names:
            return named
    return None


def _push_remote_name(cwd: str | Path) -> str | None:
    """``@{push}``'s remote, else the configured one, else ``origin``, else None."""
    names = _remote_names(cwd)
    full = git.client.out(
        "rev-parse", "--symbolic-full-name", "@{push}",
        cwd=cwd, default="")
    matched = _remote_from_push_ref(full, names)
    if matched:
        return matched
    configured = _configured_push_remote(cwd, names)
    if configured:
        return configured
    if "origin" in names:
        return "origin"
    return None


def _url_for_remote(cwd: str | Path, name: str) -> str | None:
    found = git.remote_url.remotes(cwd)
    if not found:
        return None
    for remote in found:
        if remote.name == name:
            return remote.url
    return None


def commit_destination(
        cwd: str | Path, owner_map: gh.owners.OwnerMap,
        url: str | None = None) -> _Destination:
    """The group a commit goes to: push remote for commit-msg, *url* for pre-push.

    ``scan=False`` is a non-GitHub destination (allow). ``owner=None`` with
    ``scan=True`` means none of the push remotes exist, so fall back to every
    GitHub remote on the checkout.
    """
    if url is None:
        name = _push_remote_name(cwd)
        if name is None:
            return _Destination()
        url = _url_for_remote(cwd, name)
        if url is None:
            return _Destination()
    if not _is_github_remote(url, owner_map):
        return _Destination(scan=False)
    owner = gh.owner_targets.remote_owner(
        url, gh.owner_targets.default_host({}), owner_map.hosts())
    if owner is None:
        return _Destination(scan=False)
    return _Destination(owner=owner)


def _content_verdict(verdict: gh.owners.Verdict) -> HookVerdict:
    if verdict.ok:
        return HookVerdict.allow()
    warning = gh.owners.lift_content_refusal(verdict)
    if warning is not None:
        return HookVerdict.allow(warning, override=True)
    return HookVerdict.refuse(verdict.reason)


def check_message(text: str, cwd: str | Path) -> HookVerdict:
    """Whether a commit message may be recorded in the checkout at *cwd*."""
    loaded = _owner_map()
    if isinstance(loaded, HookVerdict):
        return loaded
    if not loaded.configured:
        return HookVerdict.allow()
    # ceiling: a checkout with no GitHub remote is not scanned. A GitHub
    # cross-reference event cannot be created there. Upgrade trigger: if we
    # start recording commits that another GitHub remote will fetch later.
    if not _github_owner_remote(cwd, loaded):
        return HookVerdict.allow()
    dest = commit_destination(cwd, loaded)
    if not dest.scan:
        return HookVerdict.allow()
    body = _message_body(text, cwd)
    return _content_verdict(gh.owners.check_text(body, cwd, dest.owner))


def _push_updates(ref_lines: Sequence[str]) -> list[_PushUpdate] | HookVerdict:
    updates: list[_PushUpdate] = []
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
        updates.append(_PushUpdate(local_sha, remote_sha))
    return updates


def _existing_tip(sha: str, cwd: str | Path) -> str | None:
    if _is_delete(sha) or not _hex_sha(sha):
        return None
    if git.client.ok("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd):
        return sha
    return None


def _parse_log(blob: str) -> list[_LogRecord]:
    parts = blob.split("\0")
    records: list[_LogRecord] = []
    index = 0
    while index + 1 < len(parts):
        sha, body = parts[index].lstrip("\n"), parts[index + 1]
        if sha:
            records.append(_LogRecord(sha, body))
        index += 2
    return records


def _combine_hook_items(items: Iterable[HookVerdict]) -> HookVerdict:
    """Stop on refuse or cannot-run; remember an override without hiding later failures."""
    warned = ""
    for item in items:
        if item.outcome is not HookOutcome.ALLOW:
            return item
        if item.override:
            warned = item.reason
    return HookVerdict.allow(warned, override=bool(warned))


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
    return _combine_hook_items(
        _commit_verdict(record.sha, record.body, group, owner_map)
        for record in _parse_log(logged.stdout))


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
    dest = commit_destination(cwd, loaded, url=url)
    # ceiling: a destination that is not a GitHub remote is not scanned. A
    # GitHub cross-reference event cannot be created there. Upgrade trigger:
    # if we push to a forge that creates GitHub-style reference events.
    if not dest.scan or dest.owner is None:
        return HookVerdict.allow()
    group = loaded.classify(dest.owner).name
    parsed = _push_updates(ref_lines)
    if isinstance(parsed, HookVerdict):
        return parsed
    return _combine_hook_items(
        _check_new_commits(
            remote, update.local_sha, update.remote_sha, group, loaded, cwd)
        for update in parsed)


def refuse_outgoing(cwd: str | Path, outgoing: git.push.Outgoing) -> None:
    """The `git.push.OwnerCheck` every workbench push passes: raise when its commits may not go.

    Runs the same scan the global pre-push hook does, so a push made through
    the workbench is checked where a repo's own ``core.hooksPath`` keeps that
    hook from running, and with ``--no-verify``. A refusal raises
    ``gh.owners.OwnerBoundaryError``; a check that cannot run warns and allows,
    as the hook does, and the user's cross-ref override is honoured inside
    ``check_push`` with its own warning.
    """
    verdict = check_push(outgoing.remote, outgoing.url, outgoing.ref_lines, cwd)
    if verdict.outcome is HookOutcome.REFUSE:
        raise gh.owners.OwnerBoundaryError(gh.owners.Verdict.refuse(verdict.reason))
    if verdict.reason:
        core.log.warn(verdict.reason)
