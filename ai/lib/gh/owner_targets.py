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

``GH_REPO``/``GH_HOST`` from the process environment count only when they
reach the program: ``env -i`` drops them, and ``sudo``/``doas`` — which may
or may not, depending on sudoers and doas.conf — are resolved both ways and
the owners unioned (``gh.shell_wrappers.Peeled.views``).

Writes aimed at an account rather than a repository's issues and PRs — gists,
projects, org secrets and variables, ``gh repo create|fork`` and a ``gh repo``
verb naming its repository — are resolved by ``gh.owner_scopes`` first.

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
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import config.github_owners
import git.client
import git.remote_url
import gh.owner_scopes
import gh.shell_wrappers
import gh.write_shapes
from config.github_owners import Owner

_URL_RE = re.compile(
    r"^https?://([^/\s]+)/([^/\s]+)/[^/\s]+/(?:pull|issues|releases)(?:/|$)", re.IGNORECASE)
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
# -f is --fill/--force (boolean) on gh pr create / gh label create, and a field
# flag on gh api. Skipping a following URL fails safe when another explicit
# source exists. Per-subcommand tables are deferred.
_URL_VALUE_FLAGS = frozenset({
    "--body", "-b", "--title", "-t", "--notes", "-n", "--body-file",
    "-F", "-f", "--field", "--raw-field", "--input",
})
_URL_VALUE_PREFIXES = (
    "--body=", "--title=", "--notes=", "--body-file=",
    "--field=", "--raw-field=", "--input=",
)
# Flags we know take no value. Anything else that starts with ``-`` is treated
# as value-taking so a following ``-R/--repo`` or ``--hostname`` unions cwd.
_BOOLEAN_FLAGS = frozenset({
    "--web", "-w", "--draft", "--fill", "--yes", "-y",
    "--squash", "--merge", "--rebase", "--delete-branch",
    "--auto", "--admin",
})
_BOOLEAN_SHORT = {
    ("pr", "merge"): frozenset({"-d"}),
}
_WRAPPER_REASON = (
    "the command reaches GitHub through a wrapper, so its target cannot be"
    " checked — draft it for the user to run")
_SHIFTED_ENV_REASON = (
    "the command assigns {names} in another statement, so which repository"
    " the write targets cannot be checked — pass --repo HOST/OWNER/REPO"
    " explicitly, or drop the assignment")
_SHIFTED_HOST_REASON = (
    "the command assigns GH_HOST in another statement, so a bare OWNER/REPO"
    " cannot be checked — pass --repo HOST/OWNER/REPO")


class TargetSource(StrEnum):
    """Where a target came from — what a refusal tells the reader to change."""

    EXPLICIT = "explicit"
    API_PATH = "api path"
    REMOTES = "remotes"


CHOOSE_WITH_REPO = "pass --repo OWNER/REPO explicitly, or draft the command for the user to run"
CHOOSE_WORKBENCH = (
    "the workbench command has no --repo flag — remove or rename the remote it must not"
    " use, so gh cannot pick it, or draft the command for the user to run")


@dataclass(frozen=True)
class Target:
    """The owners a write may land on, how they were found, and how to say so.

    *choose* is how the reader picks one repository when several candidates
    came from the remotes: gh takes ``--repo``, a workbench command does not.
    """

    owners: tuple[Owner, ...]
    source: TargetSource
    detail: str
    choose: str = CHOOSE_WITH_REPO


@dataclass(frozen=True)
class Resolution:
    """A target, or the reason there is none."""

    target: Target | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.target is not None


class _ArgFlags(NamedTuple):
    repos: tuple[str, ...]
    hostnames: tuple[str, ...]
    repo_dirs: tuple[str, ...]
    positionals: tuple[str, ...]


def default_host(env: Mapping[str, str], environ: Mapping[str, str] | None = None) -> str:
    """The host a bare ``OWNER/REPO`` is on: ``GH_HOST`` (prefix, then *environ*), else github.com.

    *environ* is the process environment the program inherits, ``os.environ``
    when None.
    """
    inherited = os.environ if environ is None else environ
    host = env.get("GH_HOST") or inherited.get("GH_HOST") or config.github_owners.DEFAULT_HOST
    return host.strip().lower()


def inherited_environ(view: gh.shell_wrappers.EnvView) -> dict[str, str]:
    """The ``GH_REPO``/``GH_HOST`` values of ``os.environ`` that reach a program under *view*."""
    return {name: os.environ[name] for name in ("GH_REPO", "GH_HOST")
            if name in os.environ and view.admits(name)}


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


def _content_span(word: str) -> int:
    """How many words a value-taking content flag occupies, else 0."""
    if word in _URL_VALUE_FLAGS:
        return 2
    if word.startswith(_URL_VALUE_PREFIXES):
        return 1
    return 0


def _booleans_for(args: Sequence[str]) -> frozenset[str]:
    return _BOOLEAN_FLAGS | _BOOLEAN_SHORT.get(gh.write_shapes.gh_subcommands(args), frozenset())


def _could_be_value_taking_flag(word: str, booleans: frozenset[str]) -> bool:
    """True when *word* might consume the next word as its value."""
    if not word.startswith("-") or word == "-":
        return False
    if word in booleans:
        return False
    if word.startswith("--") and "=" in word:
        return False
    return word.startswith("--") or len(word) == 2


def _collect_flag(args: Sequence[str], take,
                  booleans: frozenset[str] = _BOOLEAN_FLAGS) -> tuple[tuple[str, ...], bool]:
    """Every value *take* finds, and whether any could be a previous flag's value."""
    specs: list[str] = []
    ambiguous = False
    i = 0
    while i < len(args):
        spec, nxt = take(args, i)
        if nxt == i:
            i += 1
            continue
        if spec is not None:
            specs.append(spec)
        if i > 0 and _could_be_value_taking_flag(args[i - 1], booleans):
            ambiguous = True
        i = nxt
    return tuple(specs), ambiguous


def _take_hostname(args: Sequence[str], i: int) -> tuple[str | None, int]:
    if i >= len(args):
        return None, i
    word = args[i]
    if word == "--hostname" and i + 1 < len(args):
        return args[i + 1], i + 2
    if word.startswith("--hostname="):
        return word[len("--hostname="):], i + 1
    return None, i


def _union_owners(left: Resolution, right: Resolution) -> Resolution:
    if not left.ok:
        return left
    if not right.ok:
        return right
    owners = tuple(dict.fromkeys((*left.target.owners, *right.target.owners)))
    detail = f"{left.target.detail}, {right.target.detail}"
    return Resolution(Target(owners, left.target.source, detail, left.target.choose))


def _across_hosts(hosts: Sequence[str], resolve_one) -> Resolution:
    combined: Resolution | None = None
    for host in hosts:
        item = resolve_one(host)
        combined = item if combined is None else _union_owners(combined, item)
    return combined if combined is not None else Resolution(reason=_SHIFTED_HOST_REASON)


def _long_value(word: str, args: Sequence[str], i: int, long: str) -> tuple[str, int] | None:
    if word == long and i + 1 < len(args):
        return args[i + 1], i + 2
    if word.startswith(f"{long}="):
        return word[len(long) + 1:], i + 1
    return None


def _parse_args(args: Sequence[str]) -> _ArgFlags:
    """Flags and positionals, skipping content-flag values so ``-b --repo=x`` is a body."""
    repos: list[str] = []
    hostnames: list[str] = []
    repo_dirs: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        skip = _content_span(word)
        if skip:
            i += skip
            continue
        spec, nxt = gh.write_shapes.take_repo_flag(args, i)
        if nxt != i:
            repos.extend([spec] if spec is not None else [])
            i = nxt
            continue
        taken = _long_value(word, args, i, "--hostname")
        if taken is not None:
            hostnames.append(taken[0])
            i = taken[1]
            continue
        taken = _long_value(word, args, i, "--repo-dir")
        if taken is not None:
            repo_dirs.append(taken[0])
            i = taken[1]
            continue
        if not word.startswith("-"):
            positionals.append(word)
        i += 1
    return _ArgFlags(tuple(repos), tuple(hostnames), tuple(repo_dirs), tuple(positionals))


def _host_is_shifted(env: Mapping[str, str], shifted_env: frozenset[str]) -> bool:
    return "GH_HOST" in shifted_env and not (env.get("GH_HOST") or "").strip()


def _explicit(args: Sequence[str], env: Mapping[str, str], host: str, environ: Mapping[str, str], *,
              read_gh_repo: bool = True,
              shifted_env: frozenset[str] = frozenset()) -> Resolution | None:
    owners: list[Owner] = []
    details: list[str] = []
    flags = _parse_args(args)
    repo_specs, _ = _collect_flag(args, gh.write_shapes.take_repo_flag)
    specs = [(spec, f"--repo {spec}") for spec in repo_specs]
    repo_env = ""
    if read_gh_repo and "GH_REPO" not in shifted_env:
        repo_env = env.get("GH_REPO") or environ.get("GH_REPO") or ""
    if repo_env:
        specs.append((repo_env, f"GH_REPO={repo_env}"))
    host_shifted = _host_is_shifted(env, shifted_env)
    for spec, detail in specs:
        parts = spec.strip().split("/")
        if len(parts) == 2 and host_shifted:
            return Resolution(reason=_SHIFTED_HOST_REASON)
        parsed = gh.owner_scopes.repo_owner(spec, host)
        if parsed is None:
            return Resolution(reason=f"cannot read an owner from {detail} — write OWNER/REPO")
        owners.append(parsed)
        details.append(detail)
    for word in flags.positionals:
        match = _URL_RE.match(word)
        if match:
            owners.append(config.github_owners.owner(match.group(1), match.group(2)))
            details.append(word)
    if not owners:
        return None
    return Resolution(Target(tuple(dict.fromkeys(owners)), TargetSource.EXPLICIT, ", ".join(details)))


def _shifted_reason(shifted_env: frozenset[str]) -> str:
    return _SHIFTED_ENV_REASON.format(names=" or ".join(sorted(shifted_env)))


def _parse_gh_resolved(raw: str, host: str) -> tuple[Owner | None, bool]:
    """``(owner, ok)``. *owner* is None when there is nothing to add."""
    if not raw or raw == "base":
        return None, True
    spec = raw[5:] if raw.lower().startswith("base:") else raw
    parsed = gh.owner_scopes.repo_owner(spec, host)
    return parsed, parsed is not None


def _extend_gh_resolved(owners: list[Owner], cwd: str, found: Sequence[git.remote_url.Remote],
                        host: str, known_hosts: frozenset[str]) -> Resolution | None:
    for remote in found:
        raw = git.client.out("config", "--get", f"remote.{remote.name}.gh-resolved", cwd=cwd)
        remote_as = remote_owner(remote.url, host, known_hosts)
        parsed, ok = _parse_gh_resolved(raw, remote_as.host if remote_as else host)
        if not ok:
            return Resolution(reason=(
                f"remote.{remote.name}.gh-resolved={raw} is not OWNER/REPO, so the"
                f" write's target cannot be checked — pass --repo OWNER/REPO"))
        if parsed is not None:
            owners.append(parsed)
    return None


def _remotes(cwd: str, after_cd: bool, host: str, known_hosts: frozenset[str],
             shifted_env: frozenset[str] = frozenset()) -> Resolution:
    if after_cd:
        return Resolution(reason=(
            "the command changes directory before it writes, so which repository it"
            " targets cannot be checked — run it from the repository, or pass --repo OWNER/REPO"))
    if shifted_env:
        return Resolution(reason=_shifted_reason(shifted_env))
    found = git.remote_url.remotes(cwd) if cwd and Path(cwd).is_dir() else None
    if found is None:
        return Resolution(reason=(
            f"{cwd or 'the working directory'} is not a git repository, so the write has"
            f" no repository to check — pass --repo OWNER/REPO"))
    owners = [o for o in (remote_owner(r.url, host, known_hosts) for r in found) if o is not None]
    refusal = _extend_gh_resolved(owners, cwd, found, host, known_hosts)
    if refusal is not None:
        return refusal
    if not owners:
        return Resolution(reason=f"no remote at {cwd} names a GitHub repository — pass --repo OWNER/REPO")
    return Resolution(Target(tuple(dict.fromkeys(owners)), TargetSource.REMOTES, f"the remotes at {cwd}"))


def _graphql(target_repo: str, host: str, cwd: str, after_cd: bool,
             known_hosts: frozenset[str], graphql_from_cwd: bool,
             shifted_env: frozenset[str]) -> Resolution:
    parsed = gh.owner_scopes.repo_owner(target_repo, host) if target_repo else None
    if parsed is not None:
        return Resolution(Target((parsed,), TargetSource.EXPLICIT, target_repo))
    if graphql_from_cwd:
        return _remotes(cwd, after_cd, host, known_hosts, shifted_env)
    return Resolution(reason=(
        "a GraphQL write names its target by node ID, which cannot be checked"
        " against our owners — draft it for the user to run"))


def _unsafe_api_path(endpoint: str) -> bool:
    lowered = endpoint.lower()
    if "%2e" in lowered or "%2f" in lowered:
        return True
    path = endpoint.split("?", 1)[0]
    return any(segment in {".", ".."} for segment in path.split("/"))


def _api_endpoint(args: Sequence[str]) -> str:
    i = 0
    while i < len(args):
        if args[i] in _API_VALUE_FLAGS:
            i += 2
            continue
        if not args[i].startswith("-"):
            return args[i]
        i += 1
    return ""


def _api_hosts(args: Sequence[str], env: Mapping[str, str], host: str,
              shifted_env: frozenset[str]) -> Resolution | tuple[str, ...]:
    specs, ambiguous = _collect_flag(args, _take_hostname)
    names = [spec.strip().lower() for spec in specs if spec.strip()]
    if not names:
        if _host_is_shifted(env, shifted_env):
            return Resolution(reason=_SHIFTED_HOST_REASON)
        return (host,)
    if ambiguous:
        names.append(host)
    return tuple(dict.fromkeys(names))


def _api_path(endpoint: str, hosts: Sequence[str], gh_args: Sequence[str],
              env: Mapping[str, str], environ: Mapping[str, str], cwd: str, after_cd: bool,
              known_hosts: frozenset[str], shifted_env: frozenset[str]) -> Resolution:
    if _unsafe_api_path(endpoint):
        return Resolution(reason=(
            f"{endpoint} has a '.' or '..' path segment, so its target cannot be checked"))
    match = _API_PATH_RE.match(endpoint)
    if not match:
        return Resolution(reason=(
            f"{endpoint or 'this gh api call'} is not under repos/OWNER/REPO, so its"
            f" target cannot be checked"))
    if match.group(1) == _OWNER_PLACEHOLDER:
        return _across_hosts(hosts, lambda host: _explicit(
            gh_args, env, host, environ, shifted_env=shifted_env) or _remotes(
            cwd, after_cd, host, known_hosts, shifted_env))
    owners = tuple(dict.fromkeys(
        config.github_owners.owner(item, match.group(1)) for item in hosts))
    return Resolution(Target(owners, TargetSource.API_PATH, endpoint))


def _api(args: Sequence[str], gh_args: Sequence[str], env: Mapping[str, str],
         environ: Mapping[str, str], host: str, cwd: str,
         after_cd: bool, known_hosts: frozenset[str], target_repo: str,
         graphql_from_cwd: bool, shifted_env: frozenset[str]) -> Resolution:
    hosts = _api_hosts(args, env, host, shifted_env)
    if isinstance(hosts, Resolution):
        return hosts
    endpoint = _api_endpoint(args)
    if endpoint == "graphql":
        return _across_hosts(hosts, lambda host: _graphql(
            target_repo, host, cwd, after_cd, known_hosts, graphql_from_cwd, shifted_env))
    return _api_path(endpoint, hosts, gh_args, env, environ, cwd, after_cd, known_hosts, shifted_env)


def _gh_repo_target(args: Sequence[str], env: Mapping[str, str], environ: Mapping[str, str],
                    host: str, cwd: str, after_cd: bool, known_hosts: frozenset[str],
                    shifted_env: frozenset[str]) -> Resolution:
    _, ambiguous = _collect_flag(args, gh.write_shapes.take_repo_flag, _booleans_for(args))
    explicit = _explicit(args, env, host, environ, shifted_env=shifted_env)
    if explicit is not None and not explicit.ok:
        return explicit
    if explicit is not None and not ambiguous:
        return explicit
    remotes = _remotes(cwd, after_cd, host, known_hosts, shifted_env)
    if explicit is None:
        return remotes
    return _union_owners(explicit, remotes)


def _transfer_dest(positionals: Sequence[str]) -> str:
    if len(positionals) < 4:
        return ""
    if positionals[0] != "issue" or positionals[1] != "transfer":
        return ""
    return positionals[3]


def _with_transfer(resolution: Resolution, args: Sequence[str], host: str) -> Resolution:
    if not resolution.ok:
        return resolution
    spec = _transfer_dest(_parse_args(args).positionals)
    if not spec:
        return resolution
    parsed = gh.owner_scopes.repo_owner(spec, host)
    if parsed is None:
        return Resolution(reason=(
            f"cannot read an owner from transfer destination {spec} — write OWNER/REPO"))
    owners = tuple(dict.fromkeys((*resolution.target.owners, parsed)))
    detail = f"{resolution.target.detail}, {spec}"
    return Resolution(Target(owners, resolution.target.source, detail))


def _gh(args: Sequence[str], env: Mapping[str, str], environ: Mapping[str, str], cwd: str,
        after_cd: bool, known_hosts: frozenset[str], target_repo: str, graphql_from_cwd: bool,
        shifted_env: frozenset[str]) -> Resolution:
    host = default_host(env, environ)
    sub_at = gh.write_shapes.skip_repo_flags(args)
    if sub_at < len(args) and args[sub_at] == "api":
        # gh api ignores --repo and GH_REPO except to fill {owner}: the path is
        # the target, so an explicit repo must not stand in for it.
        return _api(args[sub_at + 1:], args[:sub_at], env, environ, host, cwd, after_cd,
                    known_hosts, target_repo, graphql_from_cwd, shifted_env)
    scoped = gh.owner_scopes.resolve(args, host)
    if scoped is not None:
        return _scoped(scoped)
    return _with_transfer(
        _gh_repo_target(args, env, environ, host, cwd, after_cd, known_hosts, shifted_env),
        args, host)


def _scoped(scoped: gh.owner_scopes.Scoped) -> Resolution:
    if scoped.reason:
        return Resolution(reason=scoped.reason)
    return Resolution(Target(scoped.owners, TargetSource.EXPLICIT, scoped.detail))


def _workbench(args: Sequence[str], env: Mapping[str, str], environ: Mapping[str, str],
               cwd: str, after_cd: bool, known_hosts: frozenset[str],
               shifted_env: frozenset[str]) -> Resolution:
    host = default_host(env, environ)
    explicit = _explicit(args, {}, host, environ, read_gh_repo=False, shifted_env=shifted_env)
    if explicit is not None:
        return explicit
    flags = _parse_args(args)
    if flags.repo_dirs:
        repo_dir = flags.repo_dirs[-1]
        if Path(repo_dir).is_absolute():
            return _remotes(repo_dir, False, host, known_hosts, shifted_env)
        return _remotes(str(Path(cwd) / repo_dir), after_cd, host, known_hosts, shifted_env)
    return _remotes(cwd, after_cd, host, known_hosts, shifted_env)


def _with_choice(resolution: Resolution, choose: str) -> Resolution:
    if not resolution.ok:
        return resolution
    return Resolution(replace(resolution.target, choose=choose))


def _resolve_in(inv: gh.write_shapes.Invocation, environ: Mapping[str, str], cwd: str,
                after_cd: bool, known_hosts: frozenset[str],
                shifted_env: frozenset[str]) -> Resolution:
    if inv.program == "gh":
        return _gh(inv.args, inv.env, environ, cwd, after_cd, known_hosts, "", False, shifted_env)
    return _with_choice(
        _workbench(inv.args, inv.env, environ, cwd, after_cd, known_hosts, shifted_env),
        CHOOSE_WORKBENCH)


def resolve_statement(statement: Sequence[str], cwd: str, *, after_cd: bool,
                      known_hosts: frozenset[str],
                      shifted_env: frozenset[str] = frozenset()) -> Resolution:
    """The target of one shell statement already judged a write.

    A wrapper that may have reset the environment (``sudo``, ``doas``) is
    resolved both ways and the owners unioned; ``env -i`` is resolved with
    no inherited ``GH_REPO``/``GH_HOST`` at all.
    """
    if gh.write_shapes.opaque_github_write(statement):
        return Resolution(reason=_WRAPPER_REASON)
    peeled = gh.write_shapes.peeled_statement(statement)
    if peeled.opaque or peeled.lookup or not peeled.words:
        return Resolution(reason=_WRAPPER_REASON)
    combined: Resolution | None = None
    for view in peeled.views:
        inv = gh.write_shapes.Invocation(env=view.env, words=peeled.words)
        item = _resolve_in(inv, inherited_environ(view), cwd, after_cd, known_hosts, shifted_env)
        combined = item if combined is None else _union_owners(combined, item)
    return combined


def resolve_gh_call(args: Sequence[str], *, cwd: str, target_repo: str,
                    known_hosts: frozenset[str]) -> Resolution:
    """The target of a ``gh.client`` call. A GraphQL write takes *target_repo*, else the cwd repo."""
    return _gh(tuple(args), {}, os.environ, cwd, False, known_hosts, target_repo, True, frozenset())
