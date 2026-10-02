"""Tests for creating a wiki knowledge base: `wiki init`, its modes, and ingest staging."""

import sys
from pathlib import Path

import pytest

from conftest import frontmatter_keys, git_in, remote_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402
from config.workbench_config import WikiConfig, WorkbenchConfig  # noqa: E402

from wiki_support import make_wiki, write_article


class TestInitModes:
    """`init` asks which placement a base gets, when the repo has not said.

    The two differ in who can read the result: an in-repo base is committed and
    shared, a vault base is private to this machine. Neither is a safe guess, so
    a repo that has said nothing is asked rather than defaulted.
    """

    def _repo(self, tmp_path: Path) -> Path:
        return remote_repo(tmp_path / "repo")

    def _vault_root(self, tmp_path: Path, monkeypatch) -> Path:
        """Point the data root at a temp dir and record what init should adopt."""
        root = tmp_path / "data" / "wiki"
        monkeypatch.setattr(cli.wiki, "default_vault_root", lambda: root)
        return root

    def test_no_mode_refuses_and_names_every_option(self, tmp_path, capsys, monkeypatch):
        repo = self._repo(tmp_path)
        self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())
        monkeypatch.setattr(cli.wiki, "wiki_dir_is_declared", lambda _r: False)
        assert cli.wiki.main(["init", str(repo)]) == 2
        err = capsys.readouterr().err
        assert "--vault" in err
        assert "--in-repo" in err
        assert "--wiki DIR" in err
        assert not (repo / "wiki").exists()

    def test_vault_creates_under_the_data_root(self, tmp_path, capsys, monkeypatch):
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())
        recorded = []
        monkeypatch.setattr(cli.wiki, "set_value", lambda key, value: recorded.append((key, value)))
        assert cli.wiki.main(["init", "--vault", str(repo)]) == 0
        assert cli.wiki.is_wiki(vault / "acme" / "widget")
        assert not (repo / "wiki").exists()
        assert recorded == [("wiki.root", str(vault))]

    def test_a_vault_base_is_not_created_when_the_key_cannot_be_recorded(
        self, tmp_path, capsys, monkeypatch,
    ):
        """Nothing walks to a vault, so an unrecorded root is an unreachable base.

        Creating it anyway reports success over a directory no command can
        resolve, which is worse than refusing: the user has a knowledge base
        they cannot reach and no error saying so.
        """
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())

        def refuse(key, value):
            raise cli.wiki.ConfigWriteError("installed workbench does not define it")

        monkeypatch.setattr(cli.wiki, "set_value", refuse)
        assert cli.wiki.main(["init", "--vault", str(repo)]) == 1
        assert not vault.exists()
        assert "unreachable" in capsys.readouterr().err

    def test_in_repo_creates_in_the_tree_and_records_nothing(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())
        monkeypatch.setattr(
            cli.wiki, "set_value",
            lambda *a: pytest.fail("--in-repo must not write machine config"),
        )
        assert cli.wiki.main(["init", "--in-repo", str(repo)]) == 0
        assert cli.wiki.is_wiki(repo / "wiki")
        assert not vault.exists()

    def test_a_declared_wiki_dir_needs_no_flag(self, tmp_path, monkeypatch):
        """Setting the key is already the answer: only an in-tree base has a name."""
        repo = self._repo(tmp_path)
        self._vault_root(tmp_path, monkeypatch)
        (repo / ".workbench.yml").write_text("wiki:\n  dir: knowledge\n", encoding="utf-8")
        assert cli.wiki.main(["init", str(repo)]) == 0
        assert cli.wiki.is_wiki(repo / "knowledge")

    def test_vault_refuses_a_repo_with_no_origin_remote(self, tmp_path, capsys, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        git_in(repo, "init", "-b", "main", "-q")
        vault = self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())
        assert cli.wiki.main(["init", "--vault", str(repo)]) == 2
        assert "no origin remote" in capsys.readouterr().err
        assert not vault.exists()

    def test_init_refuses_when_a_base_already_resolves(self, tmp_path, capsys, monkeypatch):
        """A second placement for one repo is how two divergent bases start."""
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        make_wiki(vault / "acme", dirname="widget")
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault))),
        )
        assert cli.wiki.main(["init", "--in-repo", str(repo)]) == 1
        assert "already has a knowledge base" in capsys.readouterr().err
        assert not (repo / "wiki").exists()

    def test_an_explicit_wiki_still_creates_a_second_base(self, tmp_path, monkeypatch):
        """`--wiki` names one directory outright, which is the documented escape."""
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        make_wiki(vault / "acme", dirname="widget")
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault))),
        )
        second = tmp_path / "second"
        assert cli.wiki.main(["init", "--wiki", str(second), str(repo)]) == 0
        assert cli.wiki.is_wiki(second)

    def test_an_explicit_wiki_wins_over_a_placement_flag(self, tmp_path, monkeypatch):
        """`--wiki` is not in the mutually-exclusive group, so the pair is legal.

        It names one directory outright and is checked first, which means
        `--wiki DIR --vault` creates the named directory and ignores `--vault`.
        Pinned because nothing in argparse says so and the precedence is silent.
        """
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.wiki, "load_config_or_default", lambda _r: WorkbenchConfig())
        named = tmp_path / "named"
        assert cli.wiki.main(["init", "--wiki", str(named), "--vault", str(repo)]) == 0
        assert cli.wiki.is_wiki(named)
        assert not vault.exists()

    def test_no_subcommand_removes_a_vault_base(self, tmp_path, monkeypatch):
        """The vault is the one tree with no producer that could rebuild it.

        Nothing in this CLI should be able to delete a base; `archive` only
        moves within one. Asserted over every subcommand rather than trusting
        that, since the cost of being wrong is unrecoverable.
        """
        repo = self._repo(tmp_path)
        vault = self._vault_root(tmp_path, monkeypatch)
        entry = make_wiki(vault / "acme", dirname="widget")
        monkeypatch.setattr(
            cli.wiki, "load_config_or_default",
            lambda _r: WorkbenchConfig(wiki=WikiConfig(root=str(vault))),
        )
        write_article(entry, "kept", tags=["x"])
        source = tmp_path / "note.md"
        source.write_text("text\n", encoding="utf-8")

        for argv in (
            ["path"], ["status"], ["lint"], ["signals"], ["sources"], ["index"],
            ["link"], ["ingest", "--stage", str(source)], ["archive", "kept"],
        ):
            cli.wiki.main([*argv, str(repo)])
            assert cli.wiki.is_wiki(entry), f"{argv[0]} damaged the vault base"
        assert (entry / "archive" / "kept.md").is_file()


class TestInit:
    def test_creates_the_full_layout(self, tmp_path, capsys):
        assert cli.wiki.main(["init", "--in-repo", str(tmp_path)]) == 0
        root = tmp_path / "wiki"
        for name in ("raw", "articles", "drafts", "archive", "meta"):
            assert (root / name).is_dir(), name
        for name in ("SCHEMA.md", "_index.md", "_sources.md", "_log.md"):
            assert (root / name).is_file(), name
        assert "created" in capsys.readouterr().out

    def test_the_result_is_findable(self, tmp_path):
        """init and path must agree on what a knowledge base is."""
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        assert cli.wiki.find_wiki(tmp_path) == tmp_path / "wiki"

    def test_the_result_lints_clean(self, tmp_path):
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        assert cli.wiki.collect_lint(cli.wiki.Wiki(tmp_path / "wiki")) == []

    def test_status_reports_an_empty_base(self, tmp_path):
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        status = cli.wiki.collect_status(cli.wiki.Wiki(tmp_path / "wiki"))
        assert (status["articles"], status["sources"]) == (0, 0)

    def test_refuses_an_existing_base(self, tmp_path, capsys):
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        assert cli.wiki.main(["init", "--in-repo", str(tmp_path)]) == 1
        assert "already exists" in capsys.readouterr().err

    def test_domain_reaches_the_schema(self, tmp_path):
        cli.wiki.main(["init", "--in-repo", str(tmp_path), "--domain", "Payments", "--audience", "the team"])
        schema = (tmp_path / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        assert "Payments" in schema and "the team" in schema
        assert "{DOMAIN}" not in schema and "{AUDIENCE}" not in schema

    def test_manifest_header_is_not_read_as_a_source(self, tmp_path):
        """The header init writes must not register as an entry."""
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        assert cli.wiki.Wiki(tmp_path / "wiki").recorded_source_hashes() == {}

    def test_explicit_path_is_honoured(self, tmp_path):
        target = tmp_path / "somewhere" / "kb"
        assert cli.wiki.main(["init", str(tmp_path), "--wiki", str(target)]) == 0
        assert (target / "SCHEMA.md").is_file()

    def test_completes_a_half_created_base(self, tmp_path):
        """An interrupted run should finish, not fail or lose what it wrote."""
        root = tmp_path / "wiki"
        (root / "raw").mkdir(parents=True)
        (root / "_log.md").write_text("# Activity Log\n\nkept\n", encoding="utf-8")
        assert cli.wiki.main(["init", "--in-repo", str(tmp_path)]) == 0
        assert "kept" in (root / "_log.md").read_text(encoding="utf-8")
        assert (root / "SCHEMA.md").is_file()

    def test_refuses_a_directory_holding_a_foreign_schema(self, tmp_path, capsys):
        """Init guards on the schema file, not on `is_wiki`.

        A foreign SCHEMA.md is the one file init would leave alone, so building
        the layout around it would yield a base reading someone else's settings.
        """
        target = tmp_path / "lib-schema"
        target.mkdir()
        (target / "SCHEMA.md").write_text("# Database Schema\n", encoding="utf-8")
        assert cli.wiki.main(["init", str(tmp_path), "--wiki", str(target)]) == 1
        assert "already exists" in capsys.readouterr().err
        assert (target / "SCHEMA.md").read_text(encoding="utf-8") == "# Database Schema\n"
        assert not (target / "articles").exists()


class TestIngestStage:
    def _base(self, tmp_path: Path) -> Path:
        cli.wiki.main(["init", "--in-repo", str(tmp_path)])
        return tmp_path / "wiki"

    def test_stages_a_markdown_source(self, tmp_path, capsys):
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("# Notes\n\nbody\n", encoding="utf-8")
        assert cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)]) == 0
        staged = next((root / "raw").iterdir())
        text = staged.read_text(encoding="utf-8")
        # Every frontmatter value is a quoted scalar, so read it as one rather
        # than matching the bare word.
        assert '\nsource_type: "file"\n' in text
        assert "body" in text
        assert "staged" in capsys.readouterr().out

    def test_staged_source_is_seen_as_new(self, tmp_path):
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        store = cli.wiki.Wiki(root)
        assert len(store.sources) == 1
        assert store.recorded_source_hashes() == {}

    def test_reported_hash_matches_the_file(self, tmp_path):
        """The manifest row must carry a computed hash, not a narrated one."""
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        staged = next((root / "raw").iterdir())
        row = cli.wiki.manifest_row(root, staged, "file")
        assert cli.wiki.hash_file(staged) in row
        assert cli.wiki.Wiki(root).sources[0].content_hash == cli.wiki.hash_file(staged)

    def test_no_content_hash_in_frontmatter(self, tmp_path):
        """One hash, computed on demand — a recorded copy would drift."""
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        staged = next((root / "raw").iterdir())
        keys = [
            line.split(":", 1)[0]
            for line in staged.read_text(encoding="utf-8").splitlines()[1:]
            if line and not line.startswith("---")
        ]
        assert "content_hash" not in keys

    def test_binary_source_is_copied_verbatim(self, tmp_path):
        """Wrapping a PDF in frontmatter would corrupt it."""
        root = self._base(tmp_path)
        src = tmp_path / "paper.pdf"
        src.write_bytes(b"%PDF-1.4\nbinary\x00bytes")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "pdf"])
        staged = next((root / "raw").iterdir())
        assert staged.read_bytes() == b"%PDF-1.4\nbinary\x00bytes"

    def test_same_name_twice_does_not_overwrite(self, tmp_path):
        root = self._base(tmp_path)
        for body in ("first\n", "second\n"):
            src = tmp_path / "README.md"
            src.write_text(body, encoding="utf-8")
            cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        staged = sorted((root / "raw").iterdir())
        assert len(staged) == 2
        bodies = {p.read_text(encoding="utf-8").strip().split("\n")[-1] for p in staged}
        assert bodies == {"first", "second"}

    def test_source_type_cannot_escape_raw(self, tmp_path):
        """`--type` reaches the filename, so it is held to the slug shape.

        Interpolated raw, a `../..` in it wrote the staged file outside the
        knowledge base entirely.
        """
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        assert cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "../../evil"]) == 0
        staged = list((root / "raw").iterdir())
        assert len(staged) == 1
        assert staged[0].parent == root / "raw"
        assert not (tmp_path.parent / "evil-s.md").exists()

    def _staged_keys(self, root: Path) -> list[str]:
        """Frontmatter keys of the one staged source, as a parser sees them."""
        return frontmatter_keys(next((root / "raw").iterdir()))

    def test_source_type_cannot_inject_frontmatter(self, tmp_path):
        """A newline in `--type` opened a second frontmatter key.

        Asserted on the key set rather than the text: the value survives as an
        inert slug, which is fine — what must not happen is it becoming a key.
        """
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "file\ninjected: yes"])
        assert self._staged_keys(root) == [
            "source_type",
            "title",
            "original_path",
            "ingest_date",
        ]

    def test_title_cannot_inject_frontmatter(self, tmp_path):
        """The same holds for a title, which is written as a YAML scalar."""
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", 'x"\ninjected: yes'])
        assert "injected" not in self._staged_keys(root)

    def test_a_quoted_title_stays_one_scalar(self, tmp_path):
        """An embedded quote must be escaped, not close the scalar early."""
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", 'a "quoted" name'])
        staged = next((root / "raw").iterdir())
        title = [
            ln for ln in staged.read_text(encoding="utf-8").splitlines() if ln.startswith("title:")
        ][0]
        assert title == 'title: "a \\"quoted\\" name"'

    def test_manifest_row_uses_the_cleaned_type(self, tmp_path):
        """The row must not carry a value the filename rejected."""
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "../../evil"])
        staged = next((root / "raw").iterdir())
        row = cli.wiki.manifest_row(root, staged, "../../evil")
        assert ".." not in row

    def test_title_drives_the_filename(self, tmp_path):
        root = self._base(tmp_path)
        src = tmp_path / "x.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", "How Auth Works"])
        assert (root / "raw" / "file-how-auth-works.md").is_file()

    def test_missing_source_reports_and_exits_one(self, tmp_path, capsys):
        self._base(tmp_path)
        assert cli.wiki.main(["ingest", str(tmp_path), "--stage", str(tmp_path / "gone.md")]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_ingest_is_logged(self, tmp_path):
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        cli.wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        assert "INGEST:" in (root / "_log.md").read_text(encoding="utf-8")


class TestSlugify:
    def test_lowercases_and_hyphenates(self):
        assert cli.wiki.slugify_title("How Auth Works") == "how-auth-works"

    def test_drops_punctuation(self):
        assert cli.wiki.slugify_title("What's a Token? (v2)") == "whats-a-token-v2"

    def test_truncates_at_a_word_boundary(self):
        """Every word is the same, so the last segment must be a whole one."""
        slug = cli.wiki.slugify_title(" ".join(["alpha"] * 30))
        assert len(slug) <= 60
        assert slug.rsplit("-", 1)[-1] == "alpha"

    def test_a_single_long_word_is_cut_rather_than_emptied(self):
        slug = cli.wiki.slugify_title("x" * 200)
        assert 0 < len(slug) <= 60

    def test_empty_title_still_yields_a_name(self):
        assert cli.wiki.slugify_title("!!!") == "untitled"


class TestSchemaTemplateSubstitution:
    def test_no_placeholder_text_survives(self, tmp_path):
        """Instructions to the filler must not be filled in themselves.

        The template's opening line named both placeholders while telling the
        reader to replace them, so substitution produced 'Replace Payments and
        the team' in every new schema.
        """
        cli.wiki.main(["init", "--in-repo", str(tmp_path), "--domain", "Payments", "--audience", "the team"])
        body = (tmp_path / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        prose = [ln for ln in body.splitlines() if not ln.strip().startswith("<!--")]
        assert not any("Replace" in ln for ln in prose)
        assert "A knowledge base about Payments, for the team." in body


class TestDomainSkipsNonProse:
    def test_html_comment_is_not_the_domain(self, tmp_path):
        """The shipped template opens with an editing note in a comment."""
        root = make_wiki(tmp_path, "<!-- an editing note -->\n\n# Wiki Schema\n\nPayments.\n")
        assert cli.wiki.Wiki(root).domain() == "Payments."

    def test_init_output_reports_the_real_domain(self, tmp_path):
        """End to end against the template init actually copies."""
        cli.wiki.main(["init", "--in-repo", str(tmp_path), "--domain", "Payments"])
        status = cli.wiki.collect_status(cli.wiki.Wiki(tmp_path / "wiki"))
        assert status["domain"] == "A knowledge base about Payments, for whoever works on it."
