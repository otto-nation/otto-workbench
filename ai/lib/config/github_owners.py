"""Which GitHub owners are ours, as ``github.owners`` spells them.

An owner is ``OWNER`` — on public GitHub — or ``HOST/OWNER`` on an Enterprise
instance. Both parts compare case-insensitively, because GitHub treats logins
and hosts that way, and the host is part of the identity: ``foo`` and
``ghe.corp/foo`` are two owners. Whether an owner is a user or an organisation
makes no difference.

Here rather than beside the policy in ``gh.owners`` because two layers need it:
``config set`` refuses an owner listed in two groups before it writes, and the
config layer cannot import ``gh``. ``gh.owners`` builds its ``OwnerMap`` from
``owner_index`` below, so the writer and the resolver cannot disagree about
what a duplicate is.

``owner_index`` is the one validator: a group name must be usable as a dotted
key segment (``github.owners.<group>``), every owner must parse, and no owner
may sit in two groups — the policy has to know *which* group a reference is
in, and an owner in two cannot answer.
"""

# doc-group: platform

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from config.workbench_config import ConfigValueError

DEFAULT_HOST = "github.com"

# GitHub logins are letters, digits and hyphens; Enterprise managed users add
# an underscore suffix. The dot is allowed so an owner never fails to parse on a
# spelling GitHub accepts somewhere and this did not anticipate.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?")
# A group name is a dotted-key segment, so it may not hold a dot.
_GROUP_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Owner:
    """One GitHub owner: the host that serves it and its login, both lower-cased."""

    host: str
    name: str

    def __str__(self) -> str:
        return self.name if self.host == DEFAULT_HOST else f"{self.host}/{self.name}"


def owner(host: str, name: str) -> Owner:
    """An ``Owner`` with both parts folded, so equal spellings compare equal."""
    return Owner(host.strip().lower(), name.strip().lower())


def parse_owner(spec: str) -> Owner:
    """``OWNER`` or ``HOST/OWNER`` as an ``Owner``; ``ConfigValueError`` otherwise."""
    text = spec.strip()
    host, sep, name = text.rpartition("/")
    if not sep:
        host = DEFAULT_HOST
    if not _NAME_RE.fullmatch(name) or not _HOST_RE.fullmatch(host):
        raise ConfigValueError(
            f"{spec!r} is not an owner — write OWNER for github.com or HOST/OWNER")
    return owner(host, name)


def _put_owner(index: dict[Owner, str], group: str, spec: str) -> None:
    parsed = parse_owner(spec)
    first = index.get(parsed)
    if first is not None and first != group:
        raise ConfigValueError(
            f"{parsed} is in both the {first} and {group} groups — an"
            f" owner belongs to one group")
    index[parsed] = group


def owner_index(groups: Mapping[str, Sequence[str]]) -> dict[Owner, str]:
    """Each owner and the one group it is in.

    Raises ``ConfigValueError`` naming the first problem: a group name that is
    not a key segment, an owner that does not parse, or an owner in two groups.
    """
    index: dict[Owner, str] = {}
    for group, members in groups.items():
        if not _GROUP_RE.fullmatch(str(group)):
            raise ConfigValueError(
                f"{group!r} is not a group name — use letters, digits, - and _")
        name = str(group)
        for spec in members:
            _put_owner(index, name, spec)
    return index
