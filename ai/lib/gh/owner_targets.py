"""Which repository a GitHub write is aimed at, read from its words and its cwd.

In order:

1. Explicit: a ``-R/--repo [HOST/]OWNER/REPO`` (before or after the
   subcommand), a ``GH_REPO`` prefix or environment variable, or a PR, issue or
   release URL among the arguments. Every explicit source present counts, so a
   ``--repo`` cannot launder a URL pointing elsewhere.
2. ``gh api``: the ``repos/OWNER/REPO/…`` path, on ``--hostname``'s host.
   ``{owner}`` is gh's placeholder for the cwd repository and resolves as 3.
3. Otherwise the repository at cwd — every remote, not just ``origin``. ``gh``
   picks the base repository among the remotes (``gh-resolved``, or the parent
   of a fork), so a fork with an ``upstream`` remote can post to the third
   party with no ``--repo``. Each remote is a candidate owner; the policy then
   requires them all in one group.
4. Unresolvable, and so refused by the caller: a GraphQL write from a shell
   (it names its target by node ID), a REST write outside ``repos/``, a cwd
   that is no repository or has no GitHub remote, and a cwd-resolved write in a
   command that changes directory — which directory it runs in is not knowable
   from the text.

A bare ``OWNER/REPO`` is on ``GH_HOST`` when set (prefix, then environment),
else public GitHub — gh's own default host.

ceiling: gh's default host can also come from ``gh auth``'s configuration (a
machine logged in only to an Enterprise host), which this does not read. The
error direction is a refusal — an Enterprise owner read as a github.com owner is
not one of ours — except where the same login is ours on github.com. Upgrade
trigger: a report of a refusal or an admission traced to it.

A remote's host is its URL's when that host is public GitHub, ``GH_HOST``, or a
host ``github.owners`` names. An unrecognised authority in an ssh spelling is
an ``~/.ssh/config`` alias (``github.com-work``) and is read as the default
host; an unrecognised ``https`` host is a forge gh does not treat as GitHub, and
that remote is not a candidate.

ceiling: an ssh alias for an Enterprise host (``ghebox:platform/svc``) reads as
public GitHub, so an Enterprise owner reached through one is refused as not
ours. Upgrade trigger: the first such refusal; resolve the alias with
``ssh -G`` then, as ``git.remote_url``'s ceiling says.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import config.github_owners
import git.remote_url
import gh.write_shapes
from config.github_owners import Owner

_URL_RE = re.compile(r"^https?://([^/\s]+)/([^/\s]+)/[^/\s]+/(?:pull|issues|releases)(?:/|$)")
_API_PATH_RE = re.compile(r"^/?repos/([^/]+)/[^/]+")
_OWNER_PLACEHOLDER = "{owner}"
# gh api's flags that consume the next word, so the endpoint is the first word
# that is neither a flag nor one of their values.
_API_VALUE_FLAGS = frozenset({
    "-X", "--method", "-f", "-F", "--field", "--raw-field", "-H", "--header",
    "--input", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview",
})
# A URL in these flags' values is content (a body, title, notes, field), not a
# target. ``gh issue create --repo ours/x --body https://github.com/upstream/y/issues/1``
# must resolve to ours/x.
_URL_VALUE_FLAGS = frozenset({
    "--body", "-b", "--title", "-t", "--notes", "-n", "--body-file",
    "-F", "-f", "--field", "--raw-field", "--input",
})
_URL_VALUE_PREFIXES = (
    "--body=", "--title=", "--notes=", "--body-file=",
    "--field=", "--raw-field=", "--input=",
)


class TargetSource(StrEnum):
    """Where a target came from — what a refusal tells the reader to change."""

    EXPLICIT = "explicit"
    API_PATH = "api path"
    REMOTES = "remotes"


@dataclass(frozen=True)
class Target:
    """The owners a write may land on, how they were found, and how to say so."""

    owners: tuple[Owner, ...]
    source: TargetSource
    detail: str


@dataclass(frozen=True)
class Resolution:
    """A target, or the reason there is none."""

    target: Target | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.target is not None


def default_host(env: Mapping[str, str]) -> str:
    """The host a bare ``OWNER/REPO`` is on: ``GH_HOST`` (prefix, then environment), else github.com."""
    host = env.get("GH_HOST") or os.environ.get("GH_HOST") or config.github_owners.DEFAULT_HOST
    return host.strip().lower()


def _repo_owner(spec: str, host: str) -> Owner | None:
    parts = spec.strip().split("/")
    if len(parts) == 2 and all(parts):
        return config.github_owners.owner(host, parts[0])
    if len(parts) == 3 and all(parts):
        return config.github_owners.owner(parts[0], parts[1])
    return None


def remote_owner(url: str, host: str, known_hosts: frozenset[str]) -> Owner | None:
    """The owner a remote URL names on GitHub, or None when it is not a GitHub remote."""
    loc = git.remote_url.parse(url)
    if not loc.hosted:
        return None
    name = loc.path.strip("/").lstrip("~").split("/", 1)[0]
    if not name:
        return None
    remote_host = loc.host.lower()
    if remote_host in known_hosts | {config.github_owners.DEFAULT_HOST, host}:
        return config.github_owners.owner(remote_host, name)
    if loc.ssh:
        return config.github_owners.owner(host, name)
    return None


def _flag_values(args: Sequence[str], short: str, long: str) -> list[str]:
    values = []
    for i, word in enumerate(args):
        if word in (short, long) and i + 1 < len(args):
            values.append(args[i + 1])
        elif word.startswith(f"{long}="):
            values.append(word[len(long) + 1:])
    return values


def _positional(args: Sequence[str]) -> list[str]:
    """Words that are not a flag or a value-taking flag's value.

    URL-shaped words among these are an explicit target; the same shape as a
    ``--body`` (or title, notes, field, input) value is content, not a target.
    """
    words: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        if word in _URL_VALUE_FLAGS:
            i += 2
            continue
        if word.startswith(_URL_VALUE_PREFIXES):
            i += 1
            continue
        if not word.startswith("-"):
            words.append(word)
        i += 1
    return words


def _explicit(args: Sequence[str], env: Mapping[str, str], host: str, *,
              read_gh_repo: bool = True) -> Resolution | None:
    owners: list[Owner] = []
    details: list[str] = []
    specs = [(spec, f"--repo {spec}") for spec in _flag_values(args, "-R", "--repo")]
    repo_env = (env.get("GH_REPO") or os.environ.get("GH_REPO")) if read_gh_repo else ""
    if repo_env:
        specs.append((repo_env, f"GH_REPO={repo_env}"))
    for spec, detail in specs:
        parsed = _repo_owner(spec, host)
        if parsed is None:
            return Resolution(reason=f"cannot read an owner from {detail} — write OWNER/REPO")
        owners.append(parsed)
        details.append(detail)
    for word in _positional(args):
        match = _URL_RE.match(word)
        if match:
            owners.append(config.github_owners.owner(match.group(1), match.group(2)))
            details.append(word)
    if not owners:
        return None
    return Resolution(Target(tuple(dict.fromkeys(owners)), TargetSource.EXPLICIT, ", ".join(details)))


def _remotes(cwd: str, after_cd: bool, host: str, known_hosts: frozenset[str]) -> Resolution:
    if after_cd:
        return Resolution(reason=(
            "the command changes directory before it writes, so which repository it"
            " targets cannot be checked — run it from the repository, or pass --repo OWNER/REPO"))
    found = git.remote_url.remotes(cwd) if cwd and Path(cwd).is_dir() else None
    if found is None:
        return Resolution(reason=(
            f"{cwd or 'the working directory'} is not a git repository, so the write has"
            f" no repository to check — pass --repo OWNER/REPO"))
    owners = [o for o in (remote_owner(r.url, host, known_hosts) for r in found) if o is not None]
    if not owners:
        return Resolution(reason=f"no remote at {cwd} names a GitHub repository — pass --repo OWNER/REPO")
    return Resolution(Target(tuple(dict.fromkeys(owners)), TargetSource.REMOTES, f"the remotes at {cwd}"))


def _graphql(target_repo: str, host: str, cwd: str, after_cd: bool,
             known_hosts: frozenset[str], graphql_from_cwd: bool) -> Resolution:
    parsed = _repo_owner(target_repo, host) if target_repo else None
    if parsed is not None:
        return Resolution(Target((parsed,), TargetSource.EXPLICIT, target_repo))
    if graphql_from_cwd:
        return _remotes(cwd, after_cd, host, known_hosts)
    return Resolution(reason=(
        "a GraphQL write names its target by node ID, which cannot be checked"
        " against our owners — draft it for the user to run"))


def _api(args: Sequence[str], gh_args: Sequence[str], env: Mapping[str, str], host: str, cwd: str,
         after_cd: bool, known_hosts: frozenset[str], target_repo: str,
         graphql_from_cwd: bool) -> Resolution:
    hostnames = _flag_values(args, "--hostname", "--hostname")
    if hostnames:
        host = hostnames[-1].strip().lower()
    endpoint, i = "", 0
    while i < len(args):
        if args[i] in _API_VALUE_FLAGS:
            i += 2
            continue
        if not args[i].startswith("-"):
            endpoint = args[i]
            break
        i += 1
    if endpoint == "graphql":
        return _graphql(target_repo, host, cwd, after_cd, known_hosts, graphql_from_cwd)
    match = _API_PATH_RE.match(endpoint)
    if not match:
        return Resolution(reason=(
            f"{endpoint or 'this gh api call'} is not under repos/OWNER/REPO, so its"
            f" target cannot be checked"))
    if match.group(1) == _OWNER_PLACEHOLDER:
        # gh fills {owner} from -R/--repo or GH_REPO when given, else the cwd repo.
        return _explicit(gh_args, env, host) or _remotes(cwd, after_cd, host, known_hosts)
    return Resolution(Target((config.github_owners.owner(host, match.group(1)),),
                             TargetSource.API_PATH, endpoint))


def _gh(args: Sequence[str], env: Mapping[str, str], cwd: str, after_cd: bool,
        known_hosts: frozenset[str], target_repo: str, graphql_from_cwd: bool) -> Resolution:
    host = default_host(env)
    sub_at = gh.write_shapes.skip_repo_flags(args)
    if sub_at < len(args) and args[sub_at] == "api":
        # gh api ignores --repo and GH_REPO except to fill {owner}: the path is
        # the target, so an explicit repo must not stand in for it.
        return _api(args[sub_at + 1:], args[:sub_at], env, host, cwd, after_cd, known_hosts,
                    target_repo, graphql_from_cwd)
    explicit = _explicit(args, env, host)
    if explicit is not None:
        return explicit
    return _remotes(cwd, after_cd, host, known_hosts)


def _workbench(args: Sequence[str], env: Mapping[str, str], cwd: str, after_cd: bool,
               known_hosts: frozenset[str]) -> Resolution:
    host = default_host(env)
    explicit = _explicit(args, {}, host, read_gh_repo=False)
    if explicit is not None:
        return explicit
    repo_dirs = _flag_values(args, "--repo-dir", "--repo-dir")
    if repo_dirs:
        repo_dir = repo_dirs[-1]
        if Path(repo_dir).is_absolute():
            return _remotes(repo_dir, False, host, known_hosts)
        return _remotes(str(Path(cwd) / repo_dir), after_cd, host, known_hosts)
    return _remotes(cwd, after_cd, host, known_hosts)


def resolve_statement(statement: Sequence[str], cwd: str, *, after_cd: bool,
                      known_hosts: frozenset[str]) -> Resolution:
    """The target of one shell statement already judged a write."""
    inv = gh.write_shapes.invocation(statement)
    if inv.program == "gh":
        return _gh(inv.args, inv.env, cwd, after_cd, known_hosts, "", graphql_from_cwd=False)
    return _workbench(inv.args, inv.env, cwd, after_cd, known_hosts)


def resolve_gh_call(args: Sequence[str], *, cwd: str, target_repo: str,
                    known_hosts: frozenset[str]) -> Resolution:
    """The target of a ``gh.client`` call. A GraphQL write takes *target_repo*, else the cwd repo."""
    return _gh(tuple(args), {}, cwd, False, known_hosts, target_repo, graphql_from_cwd=True)
