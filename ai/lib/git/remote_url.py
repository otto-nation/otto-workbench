"""A remote URL read the way git reads it, and the remotes a checkout has.

``parse`` answers two questions from one string: which path names the repo, and
which host serves it. They stay separate fields because they serve different
callers — ``pr.target`` keys a run on the path and collapses every spelling of
one repo, while a rendered link and ``gh.owners`` need the host.

* **A remote is hosted per its scheme, never per its authority.** ``file`` is
  never hosted: git discards a file URL's authority and clones the path.
* **Credentials and port are dropped from the host.** A token in a remote URL
  would otherwise be rendered into a review body; the port belongs to git's
  transport, not to the forge.
* **A dotless scp authority is an ssh alias**, not a host, and yields ``""``.
  ``ssh`` says the URL is one of the two forms an ``~/.ssh/config`` alias can
  appear in (scp-style, ``ssh://``), which is what lets ``gh.owners`` read an
  unrecognised authority there as an alias for the default host rather than as
  a forge it has never heard of.

  An ssh authority nobody recognises is resolved with ``ssh -G`` by the callers
  that judge hosts (``gh.owner_targets``, ``gh.owner_hooks``) through
  ``ssh_hostname``. A recognised host is never resolved: a ``Host github.com`` /
  ``HostName ssh.github.com`` entry would otherwise make github.com unknown.

Lifted out of ``pr.target`` so a layer below ``pr`` — ``gh.owners`` — reads the
same answer rather than a second parser; ``pr.target`` delegates to it.

``remotes`` lists ``git remote -v``'s fetch URLs: the same list ``gh`` reads
when it picks a base repository, ``insteadOf`` rewriting applied, which is the
reading the owner boundary has to share with the tool it guards.
"""

# doc-group: platform

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import core.proc
import core.timeouts
import git.client

SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*)://", re.IGNORECASE)
# The one scheme that names no host. git parses a file URL's authority and then
# discards it.
LOCAL_SCHEME = "file"
# No "user@" requirement: git reads any colon before the first slash as the
# host/path separator, so an ~/.ssh/config alias (gitbox:acme/widget.git) is
# scp-style too.
SCP_RE = re.compile(r"^[^/:]+:")
_SSH_SCHEMES = frozenset({"ssh", "git+ssh", "ssh+git"})


@dataclass(frozen=True)
class RemoteUrl:
    """What one remote URL says: its repo path, whether a host qualifies it,
    the host (``""`` for none), and whether it is an ssh-transport spelling."""

    path: str
    hosted: bool
    host: str
    ssh: bool


@dataclass(frozen=True)
class Remote:
    """One named remote and its fetch URL."""

    name: str
    url: str


def bare_host(authority: str) -> str:
    """An authority reduced to its hostname — no credentials, no port.

    Bracketed IPv6 literals keep their brackets, which is how a URL spells them.
    """
    host = authority.rpartition("@")[2]
    if host.startswith("["):
        bracket = host.find("]")
        return host[: bracket + 1] if bracket != -1 else host
    return host.partition(":")[0]


def parse(url: str) -> RemoteUrl:
    """*url* as git reads it. See the module docstring for the rules."""
    scheme = SCHEME_RE.match(url)
    if scheme:
        name = scheme.group(1).lower()
        authority, _, path = url[scheme.end():].partition("/")
        if name == LOCAL_SCHEME:
            return RemoteUrl(path=path, hosted=False, host="", ssh=False)
        return RemoteUrl(path=path, hosted=True, host=bare_host(authority),
                         ssh=name in _SSH_SCHEMES)
    if SCP_RE.match(url):
        authority, _, path = url.partition(":")
        host = bare_host(authority)
        # A dotted name is a domain; a bare word is an ssh alias. See the ceiling.
        return RemoteUrl(path=path, hosted=True, host=host if "." in host else "", ssh=True)
    return RemoteUrl(path=url, hosted=False, host="", ssh=False)


def ssh_alias(url: str) -> str:
    """The authority host of an ssh-spelled *url* before ssh config applies; ``""`` otherwise."""
    if not parse(url).ssh:
        return ""
    if SCHEME_RE.match(url):
        return parse(url).host
    return bare_host(url.partition(":")[0])


@functools.lru_cache(maxsize=None)
def ssh_hostname(alias: str) -> str:
    """The host ``ssh -G`` says *alias* connects to, lowercased; ``""`` when it cannot say.

    Cached per alias for the process. An empty or option-shaped alias is never
    handed to ssh, so a crafted remote cannot inject an ssh option.
    """
    if not alias or alias.startswith("-"):
        return ""
    try:
        result = core.proc.run(["ssh", "-G", alias], timeout=core.timeouts.QUICK)
    except OSError:
        return ""
    if not result.ok:
        return ""
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname":
            return value.strip().lower()
    return ""


def remotes(cwd: str | Path) -> list[Remote] | None:
    """Every remote at *cwd* with its fetch URL, in git's order; None outside a repo."""
    r = git.client.run("remote", "-v", cwd=cwd)
    if not r.ok:
        return None
    found: list[Remote] = []
    for line in r.stdout.splitlines():
        name, _, rest = line.partition("\t")
        url, _, kind = rest.rpartition(" ")
        if kind == "(fetch)" and name and url:
            found.append(Remote(name, url))
    return found
