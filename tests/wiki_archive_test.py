"""Tests for archiving wiki articles, and how lint treats an archived one."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402

from wiki_support import make_wiki, write_article, write_index


def checks(findings: list[dict]) -> set[str]:
    return {f["check"] for f in findings}


class TestArchive:
    def test_moves_the_article_and_keeps_it_readable(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="the old way", title="Old Flow")
        result = cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        assert result.path == root / "archive" / "old-flow.md"
        assert result.moved is True
        assert not (root / "articles" / "old-flow.md").exists()
        assert "the old way" in result.path.read_text(encoding="utf-8")

    def test_archived_article_leaves_the_published_set(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        store = cli.wiki.Wiki(root)
        assert [a.slug for a in store.published()] == []
        assert [a.slug for a in store.articles if a.is_archived] == ["old-flow"]

    def test_refuses_while_a_live_article_links_here(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        write_article(root, "current", body="see [[old-flow]]")
        try:
            cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        except cli.wiki.ArticleReferencedError as exc:
            assert exc.referrers == ["current"]
        else:
            raise AssertionError("expected ArticleReferencedError")
        assert (root / "articles" / "old-flow.md").exists()

    def test_force_archives_and_records_the_referrers(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        write_article(root, "current", body="see [[old-flow]]")
        cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow", force=True)
        assert (root / "archive" / "old-flow.md").exists()
        assert "still linked from current" in (root / "_log.md").read_text(encoding="utf-8")

    def test_a_draft_linking_here_does_not_block(self, tmp_path):
        """Only published articles hold a slug back; a draft is not load-bearing."""
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        write_article(root, "sketch", body="see [[old-flow]]", subdir="drafts")
        cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        assert (root / "archive" / "old-flow.md").exists()

    def test_unknown_slug_is_an_error(self, tmp_path):
        root = make_wiki(tmp_path)
        try:
            cli.wiki.archive_article(cli.wiki.Wiki(root), "nope")
        except cli.wiki.ArticleNotFoundError:
            return
        raise AssertionError("expected ArticleNotFoundError")

    def test_archiving_twice_is_a_no_op(self, tmp_path):
        """The second call lands nowhere new and says so, rather than claiming a move."""
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        first = cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        again = cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow")
        assert again.path == first.path
        assert first.moved is True
        assert again.moved is False

    def test_cli_reports_an_already_archived_article_as_unchanged(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body", subdir="archive")
        assert cli.wiki.main(["archive", "old-flow", "--wiki", str(root)]) == 0
        out = capsys.readouterr().out
        assert "already archived" in out
        assert "wiki index" not in out

    def test_name_collision_in_archive_keeps_both(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="second version")
        (root / "archive" / "old-flow.md").write_text("first version\n", encoding="utf-8")
        target = cli.wiki.archive_article(cli.wiki.Wiki(root), "old-flow").path
        assert target.name == "old-flow-2.md"
        assert "first version" in (root / "archive" / "old-flow.md").read_text(encoding="utf-8")

    def test_cli_reports_the_refusal_with_the_referrers(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        write_article(root, "current", body="see [[old-flow]]")
        assert cli.wiki.main(["archive", "old-flow", "--wiki", str(root)]) == 1
        assert "still linked from current" in capsys.readouterr().err

    def test_cli_archives_on_force(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        write_article(root, "current", body="see [[old-flow]]")
        assert cli.wiki.main(["archive", "old-flow", "--force", "--wiki", str(root)]) == 0
        assert (root / "archive" / "old-flow.md").exists()

    def test_cli_takes_the_slug_then_the_directory(self, tmp_path):
        """`wiki archive SLUG DIR` — the documented form every other subcommand takes."""
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        assert cli.wiki.main(["archive", "old-flow", str(tmp_path)]) == 0
        assert (root / "archive" / "old-flow.md").exists()

    def test_cli_defaults_the_directory_when_only_a_slug_is_given(self, tmp_path, monkeypatch):
        root = make_wiki(tmp_path)
        write_article(root, "old-flow", body="body")
        monkeypatch.chdir(tmp_path)
        assert cli.wiki.main(["archive", "old-flow"]) == 0
        assert (root / "archive" / "old-flow.md").exists()


class TestArchivedArticlesInLint:
    def test_link_to_an_archived_article_warns_rather_than_breaking(self, tmp_path):
        """A tombstone, not a broken link. Stripping it would lose the trail."""
        root = make_wiki(tmp_path)
        write_article(root, "current", body="superseded [[old-flow]]")
        write_article(root, "old-flow", body="body", subdir="archive")
        write_index(root, "current")
        found = cli.wiki.collect_lint(cli.wiki.Wiki(root))
        assert "broken-link" not in checks(found)
        archived = [f for f in found if f["check"] == "archived-link"]
        assert len(archived) == 1
        assert archived[0]["severity"] == "warning"
        assert archived[0]["where"] == "articles/current.md"

    def test_archived_article_is_not_scanned_for_contradictions(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old", body="[CONTRADICTION] both sides", subdir="archive")
        assert "contradiction" not in checks(cli.wiki.collect_lint(cli.wiki.Wiki(root)))

    def test_archived_article_does_not_need_an_index_entry(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old", body="body", subdir="archive")
        write_index(root)
        assert "missing-index-entry" not in checks(cli.wiki.collect_lint(cli.wiki.Wiki(root)))

    def test_archived_outbound_links_are_not_reported(self, tmp_path):
        """A retired article pointing at something since deleted is not a live defect."""
        root = make_wiki(tmp_path)
        write_article(root, "old", body="see [[also-gone]]", subdir="archive")
        assert "broken-link" not in checks(cli.wiki.collect_lint(cli.wiki.Wiki(root)))

    def test_an_archived_inbound_link_does_not_rescue_an_orphan(self, tmp_path):
        """Only live articles count as inbound; otherwise archiving hides orphans."""
        root = make_wiki(tmp_path)
        write_article(root, "current", body="body")
        write_article(root, "old", body="see [[current]]", subdir="archive")
        write_index(root, "current")
        assert "orphan-article" in checks(cli.wiki.collect_lint(cli.wiki.Wiki(root)))

    def test_index_omits_archived_articles(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old", body="body", subdir="archive", tags=["auth"])
        assert "[[old]]" not in cli.wiki.build_index(cli.wiki.Wiki(root))
