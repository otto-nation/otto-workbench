"""The owner boundary: which GitHub owners are ours, and whether a write may go there.

``general.md`` § Upstream Repositories says nothing is posted to a repository
we do not own without the user, and that the user does the posting. This
module is that rule as code, written once and reached from three places:

* ``ai/bin/owners-check`` (``cli.owners_check``) for both agent guards, through
  ``check_command`` — only after a guard's cheap pre-filter matched, so every
  other shell command stays on the guards' no-Python path.
* ``gh.client.run`` for every write the workbench itself makes, through
  ``check_gh_call``, which raises nothing itself: the client raises
  ``OwnerBoundaryError`` so a refusal is never mistaken for gh's own failure.

Which owners are ours is ``github.owners``, read from the **global** config
file and nothing else (``OwnerMap.load``). ``load_config`` merges a repo's
``.workbench.yml`` over it, and a cloned upstream repository's tracked file
must not be able to declare itself ours. An empty or absent map refuses every
write: a guard that switched itself off when unconfigured would protect
nothing on exactly the machines that never configure it. The refusal says how
to configure it.

A target (``gh.owner_targets``) is one owner, or — resolved from a checkout's
remotes — several candidates. It is allowed only when every candidate is ours
and all are in one group; a fork whose ``upstream`` remote is a third party is
refused with "pass --repo", because gh may pick that remote as the base.

``WORKBENCH_ALLOW_UPSTREAM=1`` in the user's own environment lets the
workbench's own publishing past an upstream refusal (``gh.client`` asks
``upstream_allowed_by_user``). ``check_command`` never reads it: the guards are
for agent commands, and an agent session inherits whatever its harness was
launched with, so honouring it there would make the override reachable from
inside the session. Both guards also refuse any command that names it.
"""

# doc-group: platform

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import config.github_owners
import config.workbench_config
import gh.owner_targets
import gh.write_shapes
from config.github_owners import Owner

ALLOW_UPSTREAM_ENV = "WORKBENCH_ALLOW_UPSTREAM"
FIX_COMMAND = (
    f"otto-workbench config set {config.workbench_config.GITHUB_OWNERS_KEY}.<group> '[owner, …]'")
UNCONFIGURED = (
    f"{config.workbench_config.GITHUB_OWNERS_KEY} is not configured, so every GitHub write is"
    f" treated as upstream and refused. Record which owners are ours with: {FIX_COMMAND}")


@dataclass(frozen=True)
class Group:
    """The group an owner is in; the empty name is ``UPSTREAM`` — not ours."""

    name: str

    @property
    def upstream(self) -> bool:
        return not self.name


UPSTREAM = Group("")


@dataclass(frozen=True)
class OwnerMap:
    """Every owner that is ours, and its group."""

    index: Mapping[Owner, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> OwnerMap:
        """``github.owners`` from the global config file alone.

        Raises ``ConfigError`` when the file is unreadable, the value is not a
        map of lists of strings, or an owner is malformed or in two groups.
        """
        path = config.workbench_config.global_config_path()
        section = config.workbench_config.read_yaml(path).get("github")
        raw = section.get("owners") if isinstance(section, dict) else None
        if raw is None:
            return cls()
        if not isinstance(raw, dict) or not all(
                isinstance(v, list) and all(isinstance(m, str) for m in v) for v in raw.values()):
            raise config.workbench_config.ConfigError(
                f"{path}: {config.workbench_config.GITHUB_OWNERS_KEY} must map group names to"
                f" lists of owners")
        try:
            return cls(config.github_owners.owner_index({str(k): v for k, v in raw.items()}))
        except config.workbench_config.ConfigValueError as exc:
            raise config.workbench_config.ConfigError(f"{path}: {exc}") from exc

    @property
    def configured(self) -> bool:
        return bool(self.index)

    def classify(self, owner: Owner) -> Group:
        name = self.index.get(owner)
        return Group(name) if name else UPSTREAM

    def hosts(self) -> frozenset[str]:
        return frozenset(o.host for o in self.index)


@dataclass(frozen=True)
class Verdict:
    """Whether a write may proceed; the reason when not, the group when so."""

    ok: bool
    reason: str = ""
    group: str = ""
    target: gh.owner_targets.Target | None = None

    @classmethod
    def allow(cls, group: str = "", target: gh.owner_targets.Target | None = None) -> Verdict:
        return cls(True, "", group, target)

    @classmethod
    def refuse(cls, reason: str, target: gh.owner_targets.Target | None = None) -> Verdict:
        return cls(False, reason, "", target)


class OwnerBoundaryError(RuntimeError):
    """A GitHub write refused at the owner boundary, before gh ran. Carries the verdict."""

    def __init__(self, verdict: Verdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


def boundary_repo(repo: str, host: str = "") -> str:
    """The GraphQL ``target_repo`` hint for *repo* on *host*.

    A two-part ``OWNER/REPO`` is resolved on ``GH_HOST`` or github.com. When
    *host* is known it is prefixed so an enterprise thread is checked on that
    host rather than public GitHub. A three-part spec is returned unchanged.
    """
    repo = (repo or "").strip()
    host = (host or "").strip()
    if not repo or not host:
        return repo
    if len(repo.split("/")) >= 3:
        return repo
    return f"{host}/{repo}"


def upstream_allowed_by_user() -> bool:
    """Whether the user set ``WORKBENCH_ALLOW_UPSTREAM=1`` — exactly ``1``."""
    return os.environ.get(ALLOW_UPSTREAM_ENV) == "1"


def check_target(target: gh.owner_targets.Target, owner_map: OwnerMap) -> Verdict:
    """Whether every candidate owner of *target* is ours, in one group."""
    if not owner_map.configured:
        return Verdict.refuse(UNCONFIGURED, target)
    if not target.owners:
        return Verdict.refuse(
            f"{target.detail} name no GitHub owner — pass --repo OWNER/REPO", target)
    groups = {candidate: owner_map.classify(candidate) for candidate in target.owners}
    foreign = [str(candidate) for candidate, group in groups.items() if group.upstream]
    if foreign and len(target.owners) > 1:
        return Verdict.refuse(
            f"{target.detail} include {', '.join(foreign)}, which is not one of our owners,"
            f" and gh may pick it as the base repository — pass --repo OWNER/REPO explicitly,"
            f" or draft the command for the user to run", target)
    if foreign:
        return Verdict.refuse(
            f"{foreign[0]} is not one of our GitHub owners ({target.detail}) — draft the"
            f" command and the text for the user to run themselves", target)
    names = sorted({group.name for group in groups.values()})
    if len(names) > 1:
        return Verdict.refuse(
            f"{target.detail} span the {' and '.join(names)} groups, so gh may pick either —"
            f" pass --repo OWNER/REPO explicitly", target)
    return Verdict.allow(names[0], target)


_GH_TARGET_VARS = frozenset({"GH_REPO", "GH_HOST"})
_ENV_BUILTINS = frozenset({"export", "declare", "typeset", "local"})


def _assign_name(word: str) -> str:
    name, sep, _ = word.partition("=")
    if not sep:
        return ""
    if name.endswith("+"):
        name = name[:-1]
    return name if name.isidentifier() else ""


def _assign_target_var(word: str) -> frozenset[str]:
    if word.startswith("-"):
        return frozenset()
    return frozenset({_assign_name(word)}) & _GH_TARGET_VARS


def _builtin_assigns(words: Sequence[str]) -> frozenset[str]:
    if not words or words[0].rsplit("/", 1)[-1] not in _ENV_BUILTINS:
        return frozenset()
    names: set[str] = set()
    for word in words[1:]:
        names.update(_assign_target_var(word))
    return frozenset(names)


def _unset_names(words: Sequence[str]) -> frozenset[str]:
    if not words or words[0] != "unset":
        return frozenset()
    return frozenset(word for word in words[1:] if not word.startswith("-")) & _GH_TARGET_VARS


def _gh_env_names(statement: Sequence[str]) -> frozenset[str]:
    inv = gh.write_shapes.invocation(statement)
    peeled = gh.write_shapes.peeled_statement(statement)
    named = frozenset(inv.env) | frozenset(peeled.env) | peeled.unset
    return (named & _GH_TARGET_VARS) | _builtin_assigns(inv.words) | _unset_names(inv.words)


def _own_gh_env(statement: Sequence[str]) -> frozenset[str]:
    return frozenset(_GH_TARGET_VARS.intersection(gh.write_shapes.peeled_statement(statement).env))


def _load() -> OwnerMap | Verdict:
    try:
        owner_map = OwnerMap.load()
    except config.workbench_config.ConfigError as exc:
        return Verdict.refuse(str(exc))
    return owner_map if owner_map.configured else Verdict.refuse(UNCONFIGURED)


def check_command(command: str, cwd: str) -> Verdict:
    """Whether a shell command's GitHub writes may proceed from *cwd*. Reads always may."""
    try:
        statements = gh.write_shapes.split_statements(command)
    except ValueError as exc:
        return Verdict.refuse(
            f"the command could not be parsed ({exc}), so the GitHub write in it cannot be checked")
    writes = [s for s in statements if gh.write_shapes.statement_is_write(s)]
    if not writes:
        return Verdict.allow()
    loaded = _load()
    if isinstance(loaded, Verdict):
        return loaded
    after_cd = any(gh.write_shapes.invocation(s).program in gh.write_shapes.DIRECTORY_CHANGERS
                   for s in statements)
    assigned = frozenset().union(*(_gh_env_names(s) for s in statements)) if statements else frozenset()
    verdict = Verdict.allow()
    for statement in writes:
        shifted = assigned - _own_gh_env(statement)
        resolution = gh.owner_targets.resolve_statement(
            statement, cwd, after_cd=after_cd, known_hosts=loaded.hosts(),
            shifted_env=shifted)
        if not resolution.ok:
            return Verdict.refuse(resolution.reason)
        verdict = check_target(resolution.target, loaded)
        if not verdict.ok:
            return verdict
    return verdict


def check_gh_call(args: Sequence[str], *, cwd: str | Path | None = None,
                  input_text: str | None = None, target_repo: str = "") -> Verdict:
    """Whether ``gh *args`` may run. Reads always may, without reading any config."""
    if not gh.write_shapes.gh_is_write(args, input_text):
        return Verdict.allow()
    loaded = _load()
    if isinstance(loaded, Verdict):
        return loaded
    resolution = gh.owner_targets.resolve_gh_call(
        args, cwd=str(cwd or os.getcwd()), target_repo=target_repo, known_hosts=loaded.hosts())
    if not resolution.ok:
        return Verdict.refuse(resolution.reason)
    return check_target(resolution.target, loaded)
