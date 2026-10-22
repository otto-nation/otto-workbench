"""Owners for the gh writes that do not land on a repository's issues and PRs.

``gh.owner_targets`` resolves most writes to the repository ``--repo``,
``GH_REPO``, a URL or the cwd remotes name. Some of gh's write commands aim
somewhere else, and ``resolve`` answers for those first:

* ``gh gist create|edit|delete|rename`` — a gist belongs to the authenticated
  account, whose login the command does not carry: refused.
* ``gh project …`` — the project's ``--owner``. ``@me``, or no ``--owner``,
  is the authenticated account: refused.
* ``gh secret|variable set|delete`` — ``--org/-o ORG`` is ORG; ``--user/-u``
  is the authenticated account: refused. Otherwise the repository, as usual.
* ``gh repo create OWNER/NAME`` is OWNER; a bare NAME is the authenticated
  account: refused. ``gh repo fork`` lands in ``--org`` or the authenticated
  account: refused without ``--org``.
* ``gh repo edit|delete|archive|unarchive|sync [REPO]`` is REPO when it is
  given, as ``OWNER/REPO``, ``HOST/OWNER/REPO`` or a URL. Every positional that
  reads as one counts, so a flag value that looks like a repository fails
  toward refusal. Without one, the repository as usual.

``None`` means the usual repository resolution applies.
"""

# doc-group: platform

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import config.github_owners
import gh.write_shapes
from config.github_owners import Owner

_ACCOUNT_REASON = (
    "{what} lands in the authenticated GitHub account, whose login the command does not"
    " name, so it cannot be checked against our owners — {fix}")
_REPO_URL_RE = re.compile(r"^https?://([^/\s]+)/([^/\s]+)/[^/\s]+/?$", re.IGNORECASE)
_REPO_POSITIONAL_VERBS = frozenset({"edit", "delete", "archive", "unarchive", "sync"})


@dataclass(frozen=True)
class Scoped:
    """The owners a scoped write lands on and how they were named, or why it cannot be read."""

    owners: tuple[Owner, ...] = ()
    detail: str = ""
    reason: str = ""


def repo_owner(spec: str, host: str) -> Owner | None:
    """The owner of ``OWNER/REPO`` (on *host*) or ``HOST/OWNER/REPO``, else None."""
    parts = spec.strip().split("/")
    if len(parts) == 2 and all(parts):
        return config.github_owners.owner(host, parts[0])
    if len(parts) == 3 and all(parts):
        return config.github_owners.owner(parts[0], parts[1])
    return None


def _flag_values(args: Sequence[str], short: str, long: str) -> list[str]:
    """Every value of ``-s VALUE``/``-sVALUE``/``-s=VALUE``/``--long VALUE``/``--long=VALUE``."""
    values: list[str] = []
    for i, word in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if word == long or (short and word == short):
            values.append(nxt)
        elif word.startswith(f"{long}="):
            values.append(word[len(long) + 1:])
        elif short and word.startswith(short) and len(word) > 2 and not word.startswith("--"):
            values.append(word[2:].removeprefix("="))
    return values


def _has_flag(args: Sequence[str], short: str, long: str) -> bool:
    return any(word in (short, long) for word in args)


def _positionals(args: Sequence[str]) -> list[str]:
    """The words after gh's group and verb that are not options, ``-R`` values excluded."""
    words: list[str] = []
    i = 0
    while i < len(args):
        _, nxt = gh.write_shapes.take_repo_flag(args, i)
        if nxt != i:
            i = nxt
            continue
        if not args[i].startswith("-"):
            words.append(args[i])
        i += 1
    return words[2:]


def _repo_spec_owner(word: str, host: str) -> Owner | None:
    url = _REPO_URL_RE.match(word.removesuffix(".git"))
    if url:
        return config.github_owners.owner(url.group(1), url.group(2))
    if "://" in word:
        return None
    return repo_owner(word, host)


def _named(owners: Sequence[tuple[Owner, str]]) -> Scoped:
    return Scoped(tuple(dict.fromkeys(o for o, _ in owners)), ", ".join(d for _, d in owners))


def _account(what: str, fix: str = "draft the command for the user to run") -> Scoped:
    return Scoped(reason=_ACCOUNT_REASON.format(what=what, fix=fix))


def _project(args: Sequence[str], host: str) -> Scoped:
    owners = _flag_values(args, "", "--owner")
    if not owners or any(not o or o == "@me" for o in owners):
        return _account("a project with no --owner, or --owner @me,", "pass --owner OWNER")
    return _named([(config.github_owners.owner(host, o), f"--owner {o}") for o in owners])


def _org_scoped(args: Sequence[str], host: str) -> Scoped | None:
    if _has_flag(args, "-u", "--user"):
        return _account("a --user secret")
    orgs = _flag_values(args, "-o", "--org")
    if not orgs:
        return None
    return _named([(config.github_owners.owner(host, o), f"--org {o}") for o in orgs])


def _repo(verb: str, args: Sequence[str], host: str) -> Scoped | None:
    if verb == "fork":
        orgs = _flag_values(args, "", "--org")
        if not orgs:
            return _account("gh repo fork without --org")
        return _named([(config.github_owners.owner(host, o), f"--org {o}") for o in orgs])
    named = [(o, w) for w in _positionals(args) if (o := _repo_spec_owner(w, host)) is not None]
    if verb == "create":
        return _named(named) if named else _account("gh repo create without OWNER/NAME")
    if verb in _REPO_POSITIONAL_VERBS and named:
        return _named(named)
    return None


def resolve(args: Sequence[str], host: str) -> Scoped | None:
    """The owners of ``gh *args`` when its target is not the usual repository, else None."""
    path = gh.write_shapes.gh_subcommands(args)
    if len(path) < 2 or path[1] not in gh.write_shapes.GH_WRITES.get(path[0], frozenset()):
        return None
    group, verb = path
    if group == "gist":
        return _account("a gist")
    if group == "project":
        return _project(args, host)
    if group in ("secret", "variable"):
        return _org_scoped(args, host)
    if group == "repo":
        return _repo(verb, args, host)
    return None
