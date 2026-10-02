"""Tests for the wiki CLI: finding a knowledge base and resolving where one lives."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import add_worktree, git_in, remote_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402
from config.workbench_config import WikiConfig, WorkbenchConfig  # noqa: E402

from wiki_support import make_wiki


class TestResolution:
    def test_finds_wiki_in_cwd(self, tmp_path):
        root = make_wiki(tmp_path)
        assert cli.wiki.find_wiki(root) == root

    def test_finds_wiki_subdirectory_from_project_root(self, tmp_path):
        root = make_wiki(tmp_path)
        assert cli.wiki.find_wiki(tmp_path) == root

    def test_walks_up_to_find_wiki(self, tmp_path):
        root = make_wiki(tmp_path)
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.find_wiki(nested) == root

    def test_an_index_alone_is_not_a_wiki(self, tmp_path):
        """The index is generated and can be rebuilt; the schema is authored.

        The plugin this replaces probed _index.md in one place and SCHEMA.md in
        another, so a half-built wiki answered differently depending on caller.
        """
        root = tmp_path / "wiki"
        (root / "articles").mkdir(parents=True)
        (root / "_index.md").write_text("# Index\n", encoding="utf-8")
        assert cli.wiki.find_wiki(tmp_path) is None

    def test_explicit_path_wins(self, tmp_path):
        make_wiki(tmp_path)
        other = make_wiki(tmp_path / "elsewhere")
        assert cli.wiki.find_wiki(tmp_path, explicit=str(other)) == other

    def test_explicit_path_that_is_not_a_wiki_resolves_to_nothing(self, tmp_path):
        make_wiki(tmp_path)
        assert cli.wiki.find_wiki(tmp_path, explicit=str(tmp_path / "nope")) is None

    def test_search_stops_at_repo_root(self, tmp_path):
        """A project without a wiki must not inherit the one above it."""
        outer = make_wiki(tmp_path)
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        assert cli.wiki.find_wiki(project) is None
        assert outer.exists()

    def test_missing_wiki_exits_two(self, tmp_path, capsys):
        assert cli.wiki.main(["status", str(tmp_path)]) == 2
        assert "no knowledge base found" in capsys.readouterr().err


class TestIsWiki:
    """What identifies a directory as a knowledge base.

    SCHEMA.md is a generic filename — an unrelated library shipping a schema
    reference under that name made every directory holding one answer as a
    knowledge base to every subcommand, so the trees a base exists for are
    required too.
    """

    def test_the_full_layout_is_a_wiki(self, tmp_path):
        assert cli.wiki.is_wiki(make_wiki(tmp_path))

    def test_a_foreign_schema_alone_is_not_a_wiki(self, tmp_path):
        foreign = tmp_path / "lib-schema"
        foreign.mkdir()
        (foreign / "SCHEMA.md").write_text("# Database Schema\n", encoding="utf-8")
        assert not cli.wiki.is_wiki(foreign)

    def test_a_foreign_schema_does_not_resolve_from_within(self, tmp_path, capsys):
        """The reported repro: cwd inside the foreign directory itself."""
        foreign = tmp_path / "lib-schema"
        foreign.mkdir()
        (foreign / "SCHEMA.md").write_text("# Database Schema\n", encoding="utf-8")
        assert cli.wiki.find_wiki(foreign) is None
        assert cli.wiki.main(["path", str(foreign)]) == 2
        assert "no knowledge base found" in capsys.readouterr().err

    def test_articles_alone_is_not_enough(self, tmp_path):
        root = tmp_path / "wiki"
        (root / "articles").mkdir(parents=True)
        (root / "SCHEMA.md").write_text("# Wiki Schema\n", encoding="utf-8")
        assert not cli.wiki.is_wiki(root)

    def test_raw_alone_is_not_enough(self, tmp_path):
        root = tmp_path / "wiki"
        (root / "raw").mkdir(parents=True)
        (root / "SCHEMA.md").write_text("# Wiki Schema\n", encoding="utf-8")
        assert not cli.wiki.is_wiki(root)

    def test_a_file_named_articles_does_not_count(self, tmp_path):
        root = tmp_path / "wiki"
        (root / "raw").mkdir(parents=True)
        (root / "articles").write_text("", encoding="utf-8")
        (root / "SCHEMA.md").write_text("# Wiki Schema\n", encoding="utf-8")
        assert not cli.wiki.is_wiki(root)


class TestConfiguredDirectory:
    """The `dirname` parameter names the directory the walk looks for at each level."""

    def test_configured_name_is_found(self, tmp_path):
        root = make_wiki(tmp_path, dirname="knowledge")
        assert cli.wiki.find_wiki(tmp_path, dirname="knowledge") == root

    def test_configured_name_is_found_from_a_nested_directory(self, tmp_path):
        root = make_wiki(tmp_path, dirname="knowledge")
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.find_wiki(nested, dirname="knowledge") == root

    def test_default_name_is_ignored_when_another_is_configured(self, tmp_path):
        """Configuring a name means that name, not that name as well as `wiki/`."""
        make_wiki(tmp_path)
        assert cli.wiki.find_wiki(tmp_path, dirname="knowledge") is None

    def test_explicit_path_ignores_the_setting(self, tmp_path):
        root = make_wiki(tmp_path, dirname="elsewhere")
        assert cli.wiki.find_wiki(tmp_path, explicit=str(root), dirname="knowledge") == root


class TestVaultSubpath:
    """A repo's folder name inside the vault, derived from its remote identity."""

    @pytest.mark.parametrize("label,expected", [
        ("otto-nation/otto-workbench", "otto-nation/otto-workbench"),
        ("acme/widget", "acme/widget"),
        ("group/sub/widget", "group/sub/widget"),
        ("acme/widget.js", "acme/widget.js"),
    ])
    def test_a_usable_label_nests_by_segment(self, label, expected):
        assert cli.wiki.vault_subpath(label) == expected

    @pytest.mark.parametrize("label", ["", ".", "..", "acme/", "/widget", "a//b"])
    def test_an_unusable_label_is_refused(self, label):
        assert cli.wiki.vault_subpath(label) is None

    @pytest.mark.parametrize("label", ["../evil", "acme/../../etc/passwd", "acme/.."])
    def test_a_traversal_segment_is_refused(self, label):
        """Slugging is not enough on its own, which is easy to assume it is.

        The slug character class keeps `.`, so `..` comes through it unchanged
        and would climb out of the vault. A simplification to slug-only fails
        here rather than in a directory above the vault.
        """
        assert cli.wiki.vault_subpath(label) is None

    def test_a_segment_that_slugs_away_is_refused(self, label="acme/\u6587\u6863"):
        """Dropping an empty segment would merge two repos into one folder."""
        assert cli.wiki.vault_subpath(label) is None


class TestVaultResolution:
    """The vault is a config read, consulted before any walk.

    Every case here drives `main` or `resolve_wiki` rather than `find_wiki`:
    the vault is resolved by the CLI, and `find_wiki` is only handed the
    in-tree half of the answer.
    """

    def _repo(self, tmp_path: Path) -> Path:
        return remote_repo(tmp_path / "repo")

    def _vault(self, tmp_path: Path, monkeypatch, *, create: bool = True) -> Path:
        """Point `wiki.root` at a vault, and optionally put acme/widget in it."""
        root = tmp_path / "vault"
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _root: WorkbenchConfig(wiki=WikiConfig(root=str(root))),
        )
        entry = root / "acme" / "widget"
        if create:
            make_wiki(entry.parent, dirname="widget")
        return entry.resolve()

    def test_a_vault_base_resolves_from_the_repo(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        entry = self._vault(tmp_path, monkeypatch)
        assert cli.wiki.main(["path", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(entry)

    def test_it_resolves_the_same_from_a_nested_directory(self, tmp_path, monkeypatch, capsys):
        """Stands in for a second worktree: no walk runs, so depth cannot matter."""
        repo = self._repo(tmp_path)
        entry = self._vault(tmp_path, monkeypatch)
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.main(["path", str(nested)]) == 0
        assert capsys.readouterr().out.strip() == str(entry)

    def test_the_vault_wins_over_an_in_tree_base(self, tmp_path, monkeypatch, capsys):
        """The case that pins the ordering, and the only one that can.

        Where only one base exists either ordering finds it, so precedence is
        unobservable until the two disagree. A vault folder exists only where
        someone deliberately made one; an in-tree directory can be a leftover.
        """
        repo = self._repo(tmp_path)
        entry = self._vault(tmp_path, monkeypatch)
        make_wiki(repo)
        assert cli.wiki.main(["path", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(entry)

    def test_a_dangling_link_still_resolves_from_config(self, tmp_path, monkeypatch, capsys):
        """A broken browsing link costs nothing, because no link is consulted.

        The repo holds a symlink at `wiki/` and the vault has moved out from
        under it. A walk sees a directory that is not a wiki; config still knows
        where the base is. This holds whichever order the two are tried in —
        what it pins is that the link is not the mechanism.
        """
        repo = self._repo(tmp_path)
        entry = self._vault(tmp_path, monkeypatch)
        (repo / "wiki").symlink_to(tmp_path / "gone", target_is_directory=True)
        assert not cli.wiki.is_wiki(repo / "wiki")
        assert cli.wiki.main(["path", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(entry)

    def test_no_vault_configured_falls_through_to_the_walk(self, tmp_path, monkeypatch, capsys):
        """An unset `wiki.root` is no vault, not a default one."""
        repo = self._repo(tmp_path)
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default", lambda _root: WorkbenchConfig(),
        )
        root = make_wiki(repo)
        assert cli.wiki.main(["path", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(root)
        assert cli.wiki.vault_dir(repo) is None

    def test_a_repo_with_no_remote_gets_no_vault_folder(self, tmp_path, monkeypatch):
        """No identity, no folder — two local `notes` repos must not share one."""
        repo = tmp_path / "repo"
        repo.mkdir()
        git_in(repo, "init", "-b", "main", "-q")
        self._vault(tmp_path, monkeypatch, create=False)
        assert cli.wiki.vault_dir(repo) is None

    def test_an_empty_vault_reports_the_path_it_checked(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        entry = self._vault(tmp_path, monkeypatch, create=False)
        assert cli.wiki.main(["path", str(repo)]) == 2
        assert str(entry) in capsys.readouterr().err

    def test_an_explicit_wiki_beats_the_vault(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._vault(tmp_path, monkeypatch)
        named = make_wiki(tmp_path / "elsewhere", dirname="kb")
        assert cli.wiki.main(["path", "--wiki", str(named), str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(named)


class TestBrowsingLink:
    """The link lives beside the worktrees and nothing resolves through it.

    Placement is the whole point: inside a worktree it would need a `.gitignore`
    entry in every repo, `wt remove` would strand it, and committing one stores
    an absolute machine-specific path as the blob.
    """

    def _linked_container(self, container: Path, tmp_path: Path, monkeypatch) -> Path:
        """A container whose repo has a real remote, and a vault holding its base."""
        git_in(container / ".git", "remote", "set-url", "origin", "git@github.com:acme/widget.git")
        vault = tmp_path / "vault"
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault), link=True)),
        )
        return make_wiki(vault / "acme", dirname="widget").resolve()

    def test_the_link_is_placed_beside_the_worktrees(self, container, tmp_path, monkeypatch):
        entry = self._linked_container(container, tmp_path, monkeypatch)
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        link = container / "wiki"
        assert link.is_symlink()
        assert link.resolve() == entry
        assert not (container / "main" / "wiki").exists()

    def test_a_second_worktree_shares_the_one_link(self, container, tmp_path, monkeypatch):
        entry = self._linked_container(container, tmp_path, monkeypatch)
        second = add_worktree(container, "second")
        assert cli.wiki.main(["link", str(second)]) == 0
        assert (container / "wiki").resolve() == entry
        assert not (second / "wiki").exists()

    def test_linking_twice_changes_nothing(self, container, tmp_path, monkeypatch, capsys):
        self._linked_container(container, tmp_path, monkeypatch)
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        first = os.readlink(container / "wiki")
        capsys.readouterr()
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        assert "already linked" in capsys.readouterr().out
        assert os.readlink(container / "wiki") == first

    def test_running_from_the_container_puts_it_in_the_same_place(
        self, container, tmp_path, monkeypatch,
    ):
        """The container is where the link appears, so it is where people will cd."""
        entry = self._linked_container(container, tmp_path, monkeypatch)
        assert cli.wiki.main(["link", str(container)]) == 0
        assert (container / "wiki").resolve() == entry

    def test_a_symlink_pointing_elsewhere_is_refused_not_replaced(
        self, container, tmp_path, monkeypatch, capsys,
    ):
        self._linked_container(container, tmp_path, monkeypatch)
        other = make_wiki(tmp_path / "other", dirname="kb")
        (container / "wiki").symlink_to(other, target_is_directory=True)
        assert cli.wiki.main(["link", str(container / "main")]) == 1
        assert "already points at" in capsys.readouterr().err
        assert (container / "wiki").resolve() == other.resolve()

    def test_a_real_directory_is_refused_and_survives(
        self, container, tmp_path, monkeypatch, capsys,
    ):
        """No browsing convenience is worth deleting a directory somebody made."""
        self._linked_container(container, tmp_path, monkeypatch)
        occupied = container / "wiki"
        occupied.mkdir()
        (occupied / "keep.md").write_text("mine", encoding="utf-8")
        assert cli.wiki.main(["link", str(container / "main")]) == 1
        assert "not a symlink" in capsys.readouterr().err
        assert (occupied / "keep.md").read_text(encoding="utf-8") == "mine"

    def test_the_key_being_off_removes_a_link_we_made(self, container, tmp_path, monkeypatch):
        entry = self._linked_container(container, tmp_path, monkeypatch)
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(entry.parent.parent), link=False)),
        )
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        assert not (container / "wiki").exists()
        assert not (container / "wiki").is_symlink()

    def test_the_key_being_off_leaves_a_link_we_did_not_make(
        self, container, tmp_path, monkeypatch,
    ):
        entry = self._linked_container(container, tmp_path, monkeypatch)
        other = make_wiki(tmp_path / "other", dirname="kb")
        (container / "wiki").symlink_to(other, target_is_directory=True)
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(entry.parent.parent), link=False)),
        )
        assert cli.wiki.main(["link", str(container / "main")]) == 0
        assert (container / "wiki").resolve() == other.resolve()

    def test_a_plain_clone_says_it_has_nowhere_to_put_one(self, tmp_path, monkeypatch, capsys):
        repo = remote_repo(tmp_path / "repo")
        vault = tmp_path / "vault"
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault), link=True)),
        )
        make_wiki(vault / "acme", dirname="widget")
        assert cli.wiki.main(["link", str(repo)]) == 0
        assert "plain clone" in capsys.readouterr().out
        assert not (repo / "wiki").exists()

    def test_an_in_tree_base_has_nothing_to_link_to(self, container, capsys, monkeypatch):
        """A link beside the worktrees would point inside one of them."""
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(link=True)),
        )
        make_wiki(container / "main")
        assert cli.wiki.main(["link", str(container / "main")]) == 1
        assert "not this repo's vault base" in capsys.readouterr().err
        assert not (container / "wiki").exists()

    def test_init_vault_places_the_link_when_the_key_is_on(
        self, container, tmp_path, monkeypatch,
    ):
        git_in(container / ".git", "remote", "set-url", "origin", "git@github.com:acme/widget.git")
        vault = tmp_path / "vault"
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault), link=True)),
        )
        assert cli.wiki.main(["init", "--vault", str(container / "main")]) == 0
        assert (container / "wiki").resolve() == (vault / "acme" / "widget").resolve()

    def test_init_still_succeeds_when_the_link_cannot_be_placed(
        self, container, tmp_path, monkeypatch, capsys,
    ):
        """The base is what init promised; the affordance is not."""
        git_in(container / ".git", "remote", "set-url", "origin", "git@github.com:acme/widget.git")
        vault = tmp_path / "vault"
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault), link=True)),
        )
        (container / "wiki").mkdir()
        assert cli.wiki.main(["init", "--vault", str(container / "main")]) == 0
        assert cli.wiki.is_wiki(vault / "acme" / "widget")
        assert "not a symlink" in capsys.readouterr().err


class TestSymlinkedEntry:
    """A base reached through a symlink has one name, whichever side it is entered from.

    The placement this covers is a link in the repo pointing at a base kept
    elsewhere. `find_wiki` resolves *start* before walking, so entering inside
    the link already yielded the target's path while entering above it yielded
    the link's — two names for one wiki in console output, and in the `root`
    every write is relative to.
    """

    def _linked(self, tmp_path: Path) -> tuple[Path, Path]:
        """A base at `vault/kb`, and a `repo/wiki` symlink pointing at it."""
        target = make_wiki(tmp_path / "vault", dirname="kb")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "wiki").symlink_to(target, target_is_directory=True)
        return repo, target.resolve()

    def test_entering_above_the_link_returns_the_real_path(self, tmp_path):
        repo, target = self._linked(tmp_path)
        assert cli.wiki.find_wiki(repo) == target

    def test_both_entry_points_agree_on_one_path(self, tmp_path):
        repo, _ = self._linked(tmp_path)
        assert cli.wiki.find_wiki(repo) == cli.wiki.find_wiki(repo / "wiki")

    def test_path_prints_the_real_path(self, tmp_path, capsys):
        repo, target = self._linked(tmp_path)
        assert cli.wiki.main(["path", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == str(target)

    def test_status_reports_the_real_path(self, tmp_path, capsys):
        repo, target = self._linked(tmp_path)
        assert cli.wiki.main(["status", "--json", str(repo)]) == 0
        assert json.loads(capsys.readouterr().out)["path"] == str(target)

    def test_an_explicit_link_resolves_to_the_same_path(self, tmp_path):
        repo, target = self._linked(tmp_path)
        assert cli.wiki.find_wiki(tmp_path, explicit=str(repo / "wiki")) == target

    def test_a_dangling_link_is_not_a_wiki(self, tmp_path):
        """Why config has to be consulted before the walk, once a vault exists.

        `is_wiki` follows the link to decide, so a link whose target has moved
        answers exactly as a directory with no wiki in it does. Nothing in the
        walk can tell the two apart.
        """
        repo, target = self._linked(tmp_path)
        shutil.move(str(target), str(tmp_path / "moved"))
        assert not cli.wiki.is_wiki(repo / "wiki")
        assert cli.wiki.find_wiki(repo) is None


class TestConfiguredDirnameResolvesTheRepoRoot:
    """`wiki.dir` is read from the repo root, whatever directory the walk starts in.

    `project_config_path` looks for `.workbench.yml` directly under the path it
    is handed rather than walking up, so passing the search-start directory read
    the project scope from a file that is not there — and a `--project` setting
    was honoured only when the CLI happened to run from the repo root itself.

    These use a real git repo and a real config file rather than a stand-in,
    because a monkeypatched `load_config_or_default` ignores the argument under
    test and cannot fail this way.
    """

    def _repo(self, tmp_path: Path, dirname: str) -> Path:
        subprocess.run(["git", "init", "-b", "main", "-q", str(tmp_path)], check=True)
        (tmp_path / ".workbench.yml").write_text(f"wiki:\n  dir: {dirname}\n", encoding="utf-8")
        return tmp_path

    def test_read_from_the_repo_root(self, tmp_path):
        repo = self._repo(tmp_path, "knowledge")
        assert cli.wiki.configured_dirname(repo) == "knowledge"

    def test_read_from_a_nested_directory(self, tmp_path):
        repo = self._repo(tmp_path, "knowledge")
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.configured_dirname(nested) == "knowledge"

    def test_the_walk_honours_it_from_a_nested_directory(self, tmp_path, capsys):
        """End to end through the CLI: the setting reaches the walk.

        Driven through `main` rather than `find_wiki`, because resolving the
        name is the CLI's job — `find_wiki` is handed the answer.
        """
        repo = self._repo(tmp_path, "knowledge")
        root = make_wiki(repo, dirname="knowledge")
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.main(["path", str(nested)]) == 0
        assert capsys.readouterr().out.strip() == str(root)

    def test_outside_a_repo_falls_back_to_the_start_directory(self, tmp_path):
        """No git toplevel to resolve; the search start stands in for it."""
        (tmp_path / ".workbench.yml").write_text("wiki:\n  dir: knowledge\n", encoding="utf-8")
        assert cli.wiki.configured_dirname(tmp_path) == "knowledge"


class TestConfiguredDirname:
    """`configured_dirname` reads `wiki.dir`, defaulting when it is unset or blank."""

    def test_blank_setting_falls_back_to_the_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _root: _config("   "))
        assert cli.wiki.configured_dirname(tmp_path) == cli.wiki.DEFAULT_WIKI_DIRNAME


class TestPathCommand:
    def test_prints_resolved_root(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        assert cli.wiki.main(["path", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == str(root)

    def test_explicit_path_suppresses_the_walk_up(self, tmp_path):
        """An explicit path names one base; falling back would use another."""
        make_wiki(tmp_path)
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert cli.wiki.find_wiki(nested, explicit=str(nested / "absent")) is None

    def test_walk_up_stops_at_the_depth_limit(self, tmp_path):
        root = make_wiki(tmp_path)
        deep = tmp_path.joinpath(*[f"d{i}" for i in range(cli.wiki.MAX_PARENT_DEPTH + 2)])
        deep.mkdir(parents=True)
        assert cli.wiki.find_wiki(deep) is None
        assert cli.wiki.find_wiki(deep.parent.parent) == root


def _config(dirname: str) -> WorkbenchConfig:
    """A merged workbench config carrying just `wiki.dir`."""
    return WorkbenchConfig(wiki=WikiConfig(dir=dirname))


class TestPackageSurface:
    def test_every_public_name_is_importable(self):
        """`__all__` is the package's advertised surface, so a name listed
        there but never imported is a promise the package cannot keep — the
        CLI reaches types through their own modules and so never notices."""
        import importlib

        pkg = importlib.import_module("wiki")
        missing = [name for name in pkg.__all__ if not hasattr(pkg, name)]
        assert missing == []
