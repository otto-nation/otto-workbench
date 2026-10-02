"""Tests for the wiki's health reports: frontmatter, source hashes, lint, status and the index."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402

from wiki_support import (
    make_wiki,
    write_article,
    write_index,
    write_source,
    write_manifest,
    hash_of,
    findings_for,
)

# Comfortably past the 180-day `staleness_threshold_days` default, so a test
# reading the default and one overriding it both turn on the same offset.
STALE_DAYS = cli.wiki.DEFAULT_SETTINGS["staleness_threshold_days"] + 220


class TestFrontmatter:
    def test_parses_scalars_and_inline_lists(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", title="Auth Flow", tags=["auth", "api"])
        article = cli.wiki.Wiki(root).articles[0]
        assert article.title == "Auth Flow"
        assert article.tags == ["auth", "api"]

    def test_parses_block_lists(self, tmp_path):
        root = make_wiki(tmp_path)
        (root / "articles" / "a.md").write_text(
            "---\ntags:\n  - auth\n  - api\n---\nbody\n", encoding="utf-8"
        )
        assert cli.wiki.Wiki(root).articles[0].tags == ["auth", "api"]

    def test_title_falls_back_to_slug(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "token-refresh")
        assert cli.wiki.Wiki(root).articles[0].title == "token-refresh"

    def test_body_excludes_frontmatter_from_word_count(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="one two three", title="A Very Long Title Here")
        assert cli.wiki.Wiki(root).articles[0].word_count == 3

    def test_wikilink_aliases_resolve_to_the_target(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[token-refresh|the refresh flow]]")
        assert cli.wiki.Wiki(root).articles[0].links == ["token-refresh"]


class TestSourceHashing:
    def test_hash_is_real_sha256(self, tmp_path):
        """The compile step keys off this hash, so it must be computed, not narrated.

        The plugin asked the model for `first 8 chars of sha256`, which no model
        can produce, so every recorded hash was fabricated and incremental
        compilation never detected a change.
        """
        root = make_wiki(tmp_path)
        write_source(root, "note.md", "known content")
        assert cli.wiki.Wiki(root).sources[0].content_hash == hash_of("known content")

    def test_unrecorded_source_is_new(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "note.md")
        assert cli.wiki.Wiki(root).recorded_source_hashes() == {}

    def test_changed_source_detected_by_hash(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "note.md", "new text")
        write_manifest(root, {"raw/note.md": hash_of("old text")})
        store = cli.wiki.Wiki(root)
        recorded = store.recorded_source_hashes()
        assert recorded[store.sources[0].rel] != store.sources[0].content_hash

    def test_manifest_header_is_not_read_as_a_source(self, tmp_path):
        """`| source | hash |` is table furniture, not an entry.

        Read as one, it registers a source named `source` whose hash is the word
        `hash`, and a real source of that name would then report as compiled
        against a hash that is not one.
        """
        root = make_wiki(tmp_path)
        write_source(root, "source", "content")
        write_manifest(root, {})
        store = cli.wiki.Wiki(root)
        assert store.recorded_source_hashes() == {}
        assert findings_for(root, "orphan-source")

    def test_a_source_named_source_is_still_recorded(self, tmp_path):
        """Skipping the header must not skip a real entry that shares its name."""
        root = make_wiki(tmp_path)
        write_source(root, "source", "content")
        write_manifest(root, {"raw/source": hash_of("content")})
        store = cli.wiki.Wiki(root)
        assert store.recorded_source_hashes() == {"raw/source": hash_of("content")}

    def test_manifest_keys_match_the_source_path_they_record(self, tmp_path):
        """Every accepted spelling must land on the key lookups actually use.

        Normalising the other way — stripping `raw/` from manifest keys — reads
        as equally valid in isolation, and makes every source in every manifest
        look uncompiled, because nothing ever matches `Source.rel`.
        """
        root = make_wiki(tmp_path)
        write_source(root, "note.md", "text")
        for spelling in ("note.md", "raw/note.md", "./raw/note.md"):
            write_manifest(root, {spelling: hash_of("text")})
            store = cli.wiki.Wiki(root)
            source = store.sources[0]
            assert store.recorded_source_hashes().get(source.rel) == source.content_hash


class TestLint:
    def test_clean_wiki_has_no_findings(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="word " * 200 + "[[b]]", updated=_today())
        write_article(root, "b", body="word " * 200 + "[[a]]", updated=_today())
        write_index(root, "a", "b")
        assert cli.wiki.collect_lint(cli.wiki.Wiki(root)) == []

    def test_detects_broken_link(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[ghost]]")
        assert len(findings_for(root, "broken-link")) == 1

    def test_detects_missing_index_entry(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        write_index(root)
        assert len(findings_for(root, "missing-index-entry")) == 1

    def test_markdown_links_count_as_index_entries(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        (root / "_index.md").write_text("# Index\n\n- [A](articles/a.md)\n", encoding="utf-8")
        assert findings_for(root, "missing-index-entry") == []

    def test_detects_orphan_article(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="[[b]]")
        write_article(root, "b")
        orphans = findings_for(root, "orphan-article")
        assert [f["where"] for f in orphans] == ["articles/a.md"]

    def test_self_link_does_not_rescue_an_orphan(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[a]]")
        assert len(findings_for(root, "orphan-article")) == 1

    def test_detects_orphan_and_changed_sources(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "never.md", "a")
        write_source(root, "moved.md", "new")
        write_manifest(root, {"raw/moved.md": hash_of("old")})
        assert len(findings_for(root, "orphan-source")) == 1
        assert len(findings_for(root, "changed-source")) == 1

    def test_detects_stale_article(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", updated=_days_ago(STALE_DAYS))
        assert len(findings_for(root, "stale-article")) == 1

    def test_staleness_threshold_comes_from_schema(self, tmp_path):
        threshold = STALE_DAYS + 1
        root = make_wiki(tmp_path, f"# Schema\n\nTest.\n\n- staleness_threshold_days: {threshold}\n")
        write_article(root, "a", updated=_days_ago(STALE_DAYS))
        assert findings_for(root, "stale-article") == []

    def test_article_without_date_is_not_stale(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        assert findings_for(root, "stale-article") == []

    def test_finds_contradiction_marker(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="[CONTRADICTION] two sources disagree")
        assert len(findings_for(root, "contradiction")) == 1

    def test_detects_sparse_article(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="too short")
        assert len(findings_for(root, "sparse-article")) == 1

    def test_min_words_comes_from_schema(self, tmp_path):
        root = make_wiki(tmp_path, "# Schema\n\nTest.\n\n- min_article_words: 2\n")
        write_article(root, "a", body="two words")
        assert findings_for(root, "sparse-article") == []

    def test_detects_empty_related_section(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body\n\n## Related\n\n")
        assert len(findings_for(root, "empty-related")) == 1

    def test_populated_related_section_passes(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body\n\n## Related\n\n- [[b]]\n")
        write_article(root, "b")
        assert findings_for(root, "empty-related") == []

    def test_article_without_related_heading_is_not_flagged(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body only")
        assert findings_for(root, "empty-related") == []

    def test_detects_unprocessed_log_entries(self, tmp_path):
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "- QUERY_GAP: no article on retries\n- SESSION_OBSERVATION: saw a pattern\n",
            encoding="utf-8",
        )
        findings = findings_for(root, "unprocessed-log")
        assert len(findings) == 1
        assert "2 observation" in findings[0]["message"]

    def test_detects_entries_written_in_the_documented_form(self, tmp_path):
        """The bracketed date is what the artifacts rule tells sessions to write.

        It was not matched, so an entry written exactly as documented counted
        for nothing: no backlog in `wiki status`, nothing to process in `wiki
        lint` — indistinguishable from a log that had been drained.
        """
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "[2026-01-15] SESSION_OBSERVATION: the hook resolves the container\n"
            "  Context: a session started at the bare repo\n"
            "[2026-01-15] QUERY_GAP: no article on worktree layout\n",
            encoding="utf-8",
        )
        findings = findings_for(root, "unprocessed-log")
        assert len(findings) == 1
        assert "2 observation" in findings[0]["message"]

    def test_a_compile_entry_is_not_an_unprocessed_one(self, tmp_path):
        """Widening the date prefix must not make every log line an entry."""
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "[2026-01-15] COMPILE: drained the log\n",
            encoding="utf-8",
        )
        assert findings_for(root, "unprocessed-log") == []

    def test_drafts_are_not_linted_as_published(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "d", body="short", subdir="drafts")
        write_index(root)
        assert findings_for(root, "sparse-article") == []
        assert findings_for(root, "missing-index-entry") == []

    def test_drafts_still_satisfy_links(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[d]]")
        write_article(root, "d", subdir="drafts")
        assert findings_for(root, "broken-link") == []

    def test_lint_exits_one_on_findings(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[ghost]]")
        assert cli.wiki.main(["lint", str(root)]) == 1
        assert "broken-link" in capsys.readouterr().out

    def test_lint_exits_zero_when_clean(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        assert cli.wiki.main(["lint", str(root)]) == 0
        assert "clean" in capsys.readouterr().out


class TestStatus:
    def test_counts_by_category(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        write_article(root, "b")
        write_article(root, "d", subdir="drafts")
        write_source(root, "s.md")
        status = cli.wiki.collect_status(cli.wiki.Wiki(root))
        assert (status["articles"], status["drafts"], status["sources"]) == (2, 1, 1)

    def test_counts_uncompiled_sources(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "done.md", "a")
        write_source(root, "todo.md", "b")
        write_manifest(root, {"raw/done.md": hash_of("a")})
        assert cli.wiki.collect_status(cli.wiki.Wiki(root))["uncompiled_sources"] == 1

    def test_reads_domain_from_schema(self, tmp_path):
        root = make_wiki(tmp_path, "# Schema\n\nPayments domain knowledge.\n")
        assert cli.wiki.collect_status(cli.wiki.Wiki(root))["domain"] == "Payments domain knowledge."

    def test_reports_last_compile(self, tmp_path):
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "[2024-01-01] COMPILE: first\n[2024-02-01] COMPILE: second\n", encoding="utf-8"
        )
        assert "second" in cli.wiki.collect_status(cli.wiki.Wiki(root))["last_compile"]

    def test_longer_event_name_is_not_mistaken_for_the_event(self, tmp_path):
        """RECOMPILE is its own event, and must not answer for COMPILE.

        Substring matching made the most recent RECOMPILE line the reported
        last compile. Anchoring at line start is not the fix either — real log
        lines open with a bracketed date, not the keyword.
        """
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "[2024-01-01] COMPILE: real\n[2024-02-01] RECOMPILE: different event\n",
            encoding="utf-8",
        )
        assert "real" in cli.wiki.collect_status(cli.wiki.Wiki(root))["last_compile"]

    def test_json_output_is_valid(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        assert cli.wiki.main(["status", str(root), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["articles"] == 1


class TestSourcesCommand:
    def test_classifies_each_state(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_source(root, "new.md", "n")
        write_source(root, "same.md", "s")
        write_source(root, "moved.md", "new")
        write_manifest(root, {"raw/same.md": hash_of("s"), "raw/moved.md": hash_of("old")})
        assert cli.wiki.main(["sources", str(root)]) == 0
        out = capsys.readouterr().out
        assert "new       " in out and "compiled  " in out and "changed   " in out

    def test_new_filter_excludes_compiled(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_source(root, "same.md", "s")
        write_manifest(root, {"raw/same.md": hash_of("s")})
        assert cli.wiki.main(["sources", str(root), "--new"]) == 0
        assert "no new or changed sources" in capsys.readouterr().out


class TestIndex:
    def test_builds_index_grouped_by_tag(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", title="Auth", tags=["auth"])
        write_article(root, "b", title="Billing", tags=["billing"])
        rendered = cli.wiki.build_index(cli.wiki.Wiki(root))
        assert "## auth" in rendered and "[[a]]" in rendered and "[[b]]" in rendered

    def test_untagged_articles_are_grouped(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        assert "## untagged" in cli.wiki.build_index(cli.wiki.Wiki(root))

    def test_excludes_drafts(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "d", subdir="drafts")
        assert "[[d]]" not in cli.wiki.build_index(cli.wiki.Wiki(root))

    def test_empty_wiki_renders_placeholder(self, tmp_path):
        root = make_wiki(tmp_path)
        assert "No articles yet" in cli.wiki.build_index(cli.wiki.Wiki(root))

    def test_rebuild_satisfies_the_missing_entry_check(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        cli.wiki.main(["index", str(root)])
        assert findings_for(root, "missing-index-entry") == []

    def test_check_reports_stale_without_writing(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        write_index(root)
        before = (root / "_index.md").read_text(encoding="utf-8")
        assert cli.wiki.main(["index", str(root), "--check"]) == 1
        assert "stale" in capsys.readouterr().out
        assert (root / "_index.md").read_text(encoding="utf-8") == before

    def test_check_passes_after_rebuild(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        cli.wiki.main(["index", str(root)])
        assert cli.wiki.main(["index", str(root), "--check"]) == 0

    def test_index_is_idempotent(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", tags=["x"])
        cli.wiki.main(["index", str(root)])
        first = (root / "_index.md").read_text(encoding="utf-8")
        cli.wiki.main(["index", str(root)])
        assert (root / "_index.md").read_text(encoding="utf-8") == first


class TestIndexWriteFailure:
    def test_unwritable_index_reports_instead_of_raising(self, tmp_path, capsys, monkeypatch):
        root = make_wiki(tmp_path)
        write_article(root, "a")

        def refuse(*_args, **_kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "write_text", refuse)
        assert cli.wiki.main(["index", str(root)]) == 1
        assert "cannot write" in capsys.readouterr().err


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
