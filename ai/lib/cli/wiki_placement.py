"""Where a repo's knowledge base lives, and where a new one should go.

Resolution consults an explicit `--wiki`, then the machine's vault, then the
in-tree walk. Placement is the other half of that question: `init` will not
guess between a committed base and a private one.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from config.workbench_config import WIKI_DIR_KEY, WIKI_LINK_KEY, WIKI_ROOT_KEY
from config.workbench_config import container_dir, load_config_or_default
from git_layout import worktree_for
from config.workbench_config_report import config_status
from config.workbench_config_write import ConfigError as ConfigWriteError
from config.workbench_config_write import set_value
from core import workbench_paths
from git import client as git_client
from wiki import paths as wiki_paths

# Bound here so the functions below keep their original names, while the import
# above stays in the `from <package> import <module>` form the tarball's
# reachability walk follows.
DEFAULT_WIKI_DIRNAME = wiki_paths.DEFAULT_WIKI_DIRNAME
find_wiki = wiki_paths.find_wiki
is_wiki = wiki_paths.is_wiki

SCRIPT = "wiki"


def resolve_wiki(start: Path, explicit: str | None) -> tuple[Path | None, Path | None]:
    """The base governing *start*, and the vault folder that was considered.

    Three placements, in order: an explicit `--wiki`, then the machine's vault,
    then the in-tree walk. The second element is the vault folder this repo
    would use, whether or not a base was found there — the caller reports it
    when nothing resolved, so "no knowledge base" names the path it checked.

    The vault is consulted from config alone, so a repo that holds a browsing
    symlink to its base does not depend on that link resolving: `is_wiki`
    follows a link to decide, and a dangling one is indistinguishable from no
    wiki at all. The link stays a convenience rather than the mechanism.

    It is consulted *first* because a vault folder exists only where someone
    deliberately made one, while an in-tree directory can be a leftover — a base
    that predates the vault, or one a branch reintroduced. Where the two
    disagree the deliberate answer should win, and reading config first also
    spares the common case a filesystem walk.
    """
    if explicit:
        return find_wiki(start, explicit=explicit), None

    vault = vault_dir(repo_root(start))
    if vault is not None and is_wiki(vault):
        return vault, vault
    return find_wiki(start, dirname=configured_dirname(start)), vault


def repo_root(start: Path) -> Path:
    """The repo *start* sits in, or *start* itself when there is no repo.

    Config is read from here rather than from *start*: ``project_config_path``
    looks for `.workbench.yml` directly under the path it is given instead of
    walking up, so passing the search start reads the project scope from a file
    that is not there, and a `--project` setting is honoured only when the CLI
    runs from the repo root itself.
    """
    toplevel = git_client.out("rev-parse", "--show-toplevel", cwd=start)
    return Path(toplevel) if toplevel else start


def configured_dirname(start: Path) -> str:
    """The `wiki.dir` setting for *start*, or the default when unset.

    Read through ``load_config_or_default`` so an unreadable config file leaves
    the knowledge base findable. Every other command here works without config;
    this one should not be the reason none of them run.
    """
    configured = load_config_or_default(repo_root(start)).wiki.dir.strip()
    return configured or DEFAULT_WIKI_DIRNAME


def wiki_dir_is_declared(root: Path) -> bool:
    """Whether some config file names `wiki.dir`, rather than it defaulting.

    Setting the key is a repo saying its base is committed, which is a choice
    `init` can act on. Relying on the default is not a choice at all, and
    reading it as one is what would silently pick a placement.

    Asked through ``config_status`` because that is what already knows which
    file answered for a key, and because it reports an unreadable config in
    ``problems`` rather than raising — so a broken config file leaves the key
    undeclared and `init` asks, which is the right outcome and needs no
    handling here.
    """
    return any(
        key.key == WIKI_DIR_KEY and not key.is_default
        for key in config_status(root).keys
    )


def vault_dir(root: Path) -> Path | None:
    """This repo's folder inside the machine's vault, or ``None``.

    ``None`` covers three cases that all mean "no vault base for this repo":
    `wiki.root` is unset, so the machine has no vault at all; the repo has no
    origin remote, so it has no identity to name a folder by; or that identity
    holds nothing usable as a path.

    A remoteless repo gets no vault folder rather than one named after its
    directory. Two local repos both called `notes` would otherwise share a base
    and write into each other's articles, which is the failure the walk's own
    docstring records from the plugin this replaced.

    The identity import is deferred to here because it is only needed once a
    vault exists: `pr.target` costs an import and a `git remote` subprocess, and
    `wiki path` runs on every session exit through the capture hook. The config
    module does the same for its schema generator, for the same reason.
    """
    configured = load_config_or_default(root).wiki.root.strip()
    if not configured:
        return None
    return vault_entry(Path(configured).expanduser(), root)


def vault_entry(vault: Path, root: Path) -> Path | None:
    """Where *root*'s base sits inside *vault*, or ``None`` with no safe name.

    Split from ``vault_dir`` so `init --vault` can name a folder in a vault this
    machine has not adopted yet, while resolution stays strict: an unset
    `wiki.root` is no vault, never a default one.

    The identity import is deferred to here because it is only needed once a
    vault is in play: `pr.target` costs an import and a `git remote` subprocess,
    and `wiki path` runs on every session exit through the capture hook. The
    config module defers its schema generator the same way, for the same reason.
    """
    from pr import target as pr_target

    identity = pr_target.repo_identity_from_origin(str(root))
    if identity is None:
        return None
    subpath = wiki_paths.vault_subpath(identity.label)
    if subpath is None:
        return None
    return (vault / subpath).resolve()


@dataclass(frozen=True)
class LinkOutcome:
    """What `apply_link` did, and the line to print about it."""

    ok: bool
    message: str


def container_link_path(root: Path) -> Path | None:
    """Where this repo's browsing link goes, or ``None`` for a plain clone.

    Beside a bare repo's worktrees rather than inside one. A per-worktree link
    would need a `.gitignore` entry in every repo that wanted one, `wt remove`
    would strand it, and committing one stores mode 120000 with an absolute
    machine-specific path as the blob — which breaks the repo for everyone else.

    Named `DEFAULT_WIKI_DIRNAME`, never `wiki.dir`: that key names the *in-tree*
    placement, and a repo with a vault base has by definition not set it. A
    configured `docs/knowledge` would also mean creating a directory at the
    container to hold the link, which is not the workbench's to create.

    `container_dir` answers for a worktree and for the container itself, and
    ``None`` for a plain clone — which has no directory outside the working tree
    to put a link in, and so gets none.
    """
    container = container_dir(str(root))
    if container is None and (root / ".git").is_dir():
        # Standing in the container itself, which is where the link appears and
        # so where someone is likely to run this. `container_dir` answers None
        # here rather than naming it: it asks git for a toplevel, and a bare
        # container has no work tree to report one.
        worktree = worktree_for(str(root))
        if worktree.ok:
            container = str(root)
    return Path(container) / DEFAULT_WIKI_DIRNAME if container else None


def _link_resolves_to(link: Path, entry: Path) -> bool:
    """Whether *link* is a symlink this repo owns, pointing at *entry*.

    Compared unresolved as well as resolved: a link whose target has been moved
    away still names it, and that is still a link this repo made rather than
    someone else's.
    """
    if not link.is_symlink():
        return False
    target = Path(os.readlink(link))
    return target == entry or (link.exists() and link.resolve() == entry)


def apply_link(root: Path, entry: Path | None, wanted: bool) -> LinkOutcome:
    """Bring this repo's browsing link into line with `wiki.link`.

    Never replaces or deletes anything it did not make. A real directory, or a
    symlink pointing somewhere else, is reported and left exactly as it is: the
    link is a convenience, and no convenience is worth removing a directory
    somebody put there.
    """
    link = container_link_path(root)
    if link is None:
        return LinkOutcome(True, "plain clone — no container to put a link in")
    if entry is None:
        return LinkOutcome(False, "this repo has no vault base to link to")

    if _link_resolves_to(link, entry):
        if wanted:
            return LinkOutcome(True, f"{link} -> {entry}: already linked")
        link.unlink()
        return LinkOutcome(True, f"{link}: removed, {WIKI_LINK_KEY} is off")
    if not wanted:
        return LinkOutcome(True, f"{WIKI_LINK_KEY} is off, and no link of ours is there")

    if link.is_symlink():
        return LinkOutcome(
            False,
            f"{link} already points at {os.readlink(link)}, not {entry}"
            f"\n  remove it yourself if it is stale",
        )
    if link.exists():
        return LinkOutcome(
            False,
            f"{link} exists and is not a symlink\n  move it yourself if you want the link there",
        )
    link.symlink_to(entry, target_is_directory=True)
    return LinkOutcome(True, f"{link} -> {entry}: linked")


def default_vault_root() -> Path:
    """The vault `init --vault` adopts when this machine has not named one.

    Under the data root rather than state: a knowledge base is authored and has
    no producer that could rebuild it. Recorded into `wiki.root` once used, so
    a later change to XDG_DATA_HOME cannot move a vault that already holds
    articles.
    """
    return workbench_paths.data_dir(DEFAULT_WIKI_DIRNAME)


class InitPlacementError(Exception):
    """`init` cannot tell where the base goes, and says what would settle it.

    An exception rather than a second return type: the alternative is a union of
    ``Path`` and an error string, and both are path-shaped enough that an
    ``isinstance`` check between them is one refactor away from treating a
    message as a directory.
    """


def _init_target(start: Path, args: argparse.Namespace) -> Path:
    """Where `init` should create a base.

    Raises ``InitPlacementError`` when the placement is not determinable.

    The two placements differ in who can read the result: a base in the repo is
    committed and shared with whoever clones it, a base in the vault is private
    to this machine. Neither is a safe guess — guessing the vault hides a team's
    knowledge base, guessing the repo commits someone's private notes — so a
    repo that has said nothing is asked, once.

    An explicitly set `wiki.dir` is already that answer: a repo only names the
    directory when its base is in-tree. Relying on the default is not.
    """
    if args.wiki:
        return Path(args.wiki).expanduser().resolve()

    root = repo_root(start)
    vault = vault_dir(root)
    if args.vault:
        entry = vault if vault is not None else vault_entry(default_vault_root(), root)
        if entry is None:
            raise InitPlacementError(
                "this repo has no origin remote, so it has no name in the vault\n"
                "  two local repos sharing a name would share one base\n"
                "  keep it in the repo with --in-repo, or name a directory with --wiki DIR"
            )
        return entry
    if args.in_repo:
        return (start / configured_dirname(start)).resolve()

    if wiki_dir_is_declared(root):
        return (start / configured_dirname(start)).resolve()
    if vault is not None:
        return vault
    raise InitPlacementError(
        "no knowledge base location chosen for this repo\n"
        "  private to this machine:  wiki init --vault\n"
        "  committed with the repo:  wiki init --in-repo\n"
        "  a specific directory:     wiki init --wiki DIR"
    )


def _record_vault_root(root: Path) -> str | None:
    """Write `wiki.root` globally, returning the reason it could not be written.

    Global scope on purpose: the value is an absolute path on this machine, and
    a repo's `.workbench.yml` is read by everyone who clones it.

    This has to happen before the base is created, not after. `wiki.root` is the
    only thing that makes a vault base findable — nothing walks to it — so a
    write that fails after the directory exists leaves a knowledge base on disk
    that no command can resolve, reported as a success. One such failure is
    routine rather than broken: ``check_key`` refuses a key the *installed*
    workbench has not learned yet, which is what a branch adding one looks like
    until it reaches main.
    """
    try:
        set_value(WIKI_ROOT_KEY, str(root))
    except ConfigWriteError as exc:
        return str(exc)
    return None
