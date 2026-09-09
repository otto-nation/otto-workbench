"""Tests for the wiki knowledge base CLI."""

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import frontmatter_keys, load_script

BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "bin"

wiki = load_script("wiki_cli", BIN_DIR / "wiki")

# `wiki` inserts `ai/lib` onto sys.path as a side effect of loading, which is
# what makes this importable without a second sys.path.insert of its own.
from config.workbench_config import WikiConfig, WorkbenchConfig  # noqa: E402

# Comfortably past the 180-day `staleness_threshold_days` default, so a test
# reading the default and one overriding it both turn on the same offset.
STALE_DAYS = wiki.DEFAULT_SETTINGS["staleness_threshold_days"] + 220


def make_wiki(
    tmp_path: Path,
    schema: str = "# Schema\n\nTest knowledge base.\n",
    dirname: str = "wiki",
) -> Path:
    root = tmp_path / dirname
    for sub in ("articles", "raw", "drafts", "meta"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "SCHEMA.md").write_text(schema, encoding="utf-8")
    return root


def write_article(root: Path, slug: str, body: str = "", subdir: str = "articles", **frontmatter) -> Path:
    path = root / subdir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return path


def write_index(root: Path, *slugs: str) -> None:
    body = "# Index\n\n" + "".join(f"- [[{slug}]]\n" for slug in slugs)
    (root / "_index.md").write_text(body, encoding="utf-8")


def write_source(root: Path, name: str, content: str = "raw content") -> Path:
    path = root / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_manifest(root: Path, entries: dict[str, str]) -> None:
    lines = ["| source | hash |", "| --- | --- |"]
    lines += [f"| {path} | {digest} |" for path, digest in entries.items()]
    (root / "_sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def hash_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[: wiki.HASH_PREFIX_LEN]


def findings_for(root: Path, check: str) -> list[dict]:
    return [f for f in wiki.collect_lint(wiki.Wiki(root)) if f["check"] == check]


class TestResolution:
    def test_finds_wiki_in_cwd(self, tmp_path):
        root = make_wiki(tmp_path)
        assert wiki.find_wiki(root) == root

    def test_finds_wiki_subdirectory_from_project_root(self, tmp_path):
        root = make_wiki(tmp_path)
        assert wiki.find_wiki(tmp_path) == root

    def test_walks_up_to_find_wiki(self, tmp_path):
        root = make_wiki(tmp_path)
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert wiki.find_wiki(nested) == root

    def test_schema_is_the_existence_marker(self, tmp_path):
        """An index alone is not a wiki: it is generated and can be rebuilt.

        The plugin this replaces probed _index.md in one place and SCHEMA.md in
        another, so a half-built wiki answered differently depending on caller.
        """
        root = tmp_path / "wiki"
        (root / "articles").mkdir(parents=True)
        (root / "_index.md").write_text("# Index\n", encoding="utf-8")
        assert wiki.find_wiki(tmp_path) is None

    def test_explicit_path_wins(self, tmp_path):
        make_wiki(tmp_path)
        other = make_wiki(tmp_path / "elsewhere")
        assert wiki.find_wiki(tmp_path, explicit=str(other)) == other

    def test_explicit_path_that_is_not_a_wiki_resolves_to_nothing(self, tmp_path):
        make_wiki(tmp_path)
        assert wiki.find_wiki(tmp_path, explicit=str(tmp_path / "nope")) is None

    def test_search_stops_at_repo_root(self, tmp_path):
        """A project without a wiki must not inherit the one above it."""
        outer = make_wiki(tmp_path)
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        assert wiki.find_wiki(project) is None
        assert outer.exists()

    def test_missing_wiki_exits_two(self, tmp_path, capsys):
        assert wiki.main(["status", str(tmp_path)]) == 2
        assert "no knowledge base found" in capsys.readouterr().err


class TestConfiguredDirectory:
    """The `dirname` parameter names the directory the walk looks for at each level."""

    def test_configured_name_is_found(self, tmp_path):
        root = make_wiki(tmp_path, dirname="knowledge")
        assert wiki.find_wiki(tmp_path, dirname="knowledge") == root

    def test_configured_name_is_found_from_a_nested_directory(self, tmp_path):
        root = make_wiki(tmp_path, dirname="knowledge")
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert wiki.find_wiki(nested, dirname="knowledge") == root

    def test_default_name_is_ignored_when_another_is_configured(self, tmp_path):
        """Configuring a name means that name, not that name as well as `wiki/`."""
        make_wiki(tmp_path)
        assert wiki.find_wiki(tmp_path, dirname="knowledge") is None

    def test_explicit_path_ignores_the_setting(self, tmp_path):
        root = make_wiki(tmp_path, dirname="elsewhere")
        assert wiki.find_wiki(tmp_path, explicit=str(root), dirname="knowledge") == root


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
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / ".workbench.yml").write_text(f"wiki:\n  dir: {dirname}\n", encoding="utf-8")
        return tmp_path

    def test_read_from_the_repo_root(self, tmp_path):
        repo = self._repo(tmp_path, "knowledge")
        assert wiki.configured_dirname(repo) == "knowledge"

    def test_read_from_a_nested_directory(self, tmp_path):
        repo = self._repo(tmp_path, "knowledge")
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        assert wiki.configured_dirname(nested) == "knowledge"

    def test_the_walk_honours_it_from_a_nested_directory(self, tmp_path, capsys):
        """End to end through the CLI: the setting reaches the walk.

        Driven through `main` rather than `find_wiki`, because resolving the
        name is the CLI's job — `find_wiki` is handed the answer.
        """
        repo = self._repo(tmp_path, "knowledge")
        root = make_wiki(repo, dirname="knowledge")
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        assert wiki.main(["path", str(nested)]) == 0
        assert capsys.readouterr().out.strip() == str(root)

    def test_outside_a_repo_falls_back_to_the_start_directory(self, tmp_path):
        """No git toplevel to resolve; the search start stands in for it."""
        (tmp_path / ".workbench.yml").write_text("wiki:\n  dir: knowledge\n", encoding="utf-8")
        assert wiki.configured_dirname(tmp_path) == "knowledge"


class TestConfiguredDirname:
    """`configured_dirname` reads `wiki.dir`, defaulting when it is unset or blank."""

    def test_blank_setting_falls_back_to_the_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiki, "load_config_or_default", lambda _root: _config("   "))
        assert wiki.configured_dirname(tmp_path) == wiki.DEFAULT_WIKI_DIRNAME


class TestFrontmatter:
    def test_parses_scalars_and_inline_lists(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", title="Auth Flow", tags=["auth", "api"])
        article = wiki.Wiki(root).articles[0]
        assert article.title == "Auth Flow"
        assert article.tags == ["auth", "api"]

    def test_parses_block_lists(self, tmp_path):
        root = make_wiki(tmp_path)
        (root / "articles" / "a.md").write_text(
            "---\ntags:\n  - auth\n  - api\n---\nbody\n", encoding="utf-8"
        )
        assert wiki.Wiki(root).articles[0].tags == ["auth", "api"]

    def test_title_falls_back_to_slug(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "token-refresh")
        assert wiki.Wiki(root).articles[0].title == "token-refresh"

    def test_body_excludes_frontmatter_from_word_count(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="one two three", title="A Very Long Title Here")
        assert wiki.Wiki(root).articles[0].word_count == 3

    def test_wikilink_aliases_resolve_to_the_target(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[token-refresh|the refresh flow]]")
        assert wiki.Wiki(root).articles[0].links == ["token-refresh"]


class TestSourceHashing:
    def test_hash_is_real_sha256(self, tmp_path):
        """The compile step keys off this hash, so it must be computed, not narrated.

        The plugin asked the model for `first 8 chars of sha256`, which no model
        can produce, so every recorded hash was fabricated and incremental
        compilation never detected a change.
        """
        root = make_wiki(tmp_path)
        write_source(root, "note.md", "known content")
        assert wiki.Wiki(root).sources[0].content_hash == hash_of("known content")

    def test_unrecorded_source_is_new(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "note.md")
        assert wiki.Wiki(root).recorded_source_hashes() == {}

    def test_changed_source_detected_by_hash(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "note.md", "new text")
        write_manifest(root, {"raw/note.md": hash_of("old text")})
        store = wiki.Wiki(root)
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
        store = wiki.Wiki(root)
        assert store.recorded_source_hashes() == {}
        assert findings_for(root, "orphan-source")

    def test_a_source_named_source_is_still_recorded(self, tmp_path):
        """Skipping the header must not skip a real entry that shares its name."""
        root = make_wiki(tmp_path)
        write_source(root, "source", "content")
        write_manifest(root, {"raw/source": hash_of("content")})
        store = wiki.Wiki(root)
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
            store = wiki.Wiki(root)
            source = store.sources[0]
            assert store.recorded_source_hashes().get(source.rel) == source.content_hash


class TestLint:
    def test_clean_wiki_has_no_findings(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="word " * 200 + "[[b]]", updated=_today())
        write_article(root, "b", body="word " * 200 + "[[a]]", updated=_today())
        write_index(root, "a", "b")
        assert wiki.collect_lint(wiki.Wiki(root)) == []

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
        assert wiki.main(["lint", str(root)]) == 1
        assert "broken-link" in capsys.readouterr().out

    def test_lint_exits_zero_when_clean(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        assert wiki.main(["lint", str(root)]) == 0
        assert "clean" in capsys.readouterr().out


class TestStatus:
    def test_counts_by_category(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        write_article(root, "b")
        write_article(root, "d", subdir="drafts")
        write_source(root, "s.md")
        status = wiki.collect_status(wiki.Wiki(root))
        assert (status["articles"], status["drafts"], status["sources"]) == (2, 1, 1)

    def test_counts_uncompiled_sources(self, tmp_path):
        root = make_wiki(tmp_path)
        write_source(root, "done.md", "a")
        write_source(root, "todo.md", "b")
        write_manifest(root, {"raw/done.md": hash_of("a")})
        assert wiki.collect_status(wiki.Wiki(root))["uncompiled_sources"] == 1

    def test_reads_domain_from_schema(self, tmp_path):
        root = make_wiki(tmp_path, "# Schema\n\nPayments domain knowledge.\n")
        assert wiki.collect_status(wiki.Wiki(root))["domain"] == "Payments domain knowledge."

    def test_reports_last_compile(self, tmp_path):
        root = make_wiki(tmp_path)
        (root / "_log.md").write_text(
            "[2024-01-01] COMPILE: first\n[2024-02-01] COMPILE: second\n", encoding="utf-8"
        )
        assert "second" in wiki.collect_status(wiki.Wiki(root))["last_compile"]

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
        assert "real" in wiki.collect_status(wiki.Wiki(root))["last_compile"]

    def test_json_output_is_valid(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        assert wiki.main(["status", str(root), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["articles"] == 1


class TestSourcesCommand:
    def test_classifies_each_state(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_source(root, "new.md", "n")
        write_source(root, "same.md", "s")
        write_source(root, "moved.md", "new")
        write_manifest(root, {"raw/same.md": hash_of("s"), "raw/moved.md": hash_of("old")})
        assert wiki.main(["sources", str(root)]) == 0
        out = capsys.readouterr().out
        assert "new       " in out and "compiled  " in out and "changed   " in out

    def test_new_filter_excludes_compiled(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_source(root, "same.md", "s")
        write_manifest(root, {"raw/same.md": hash_of("s")})
        assert wiki.main(["sources", str(root), "--new"]) == 0
        assert "no new or changed sources" in capsys.readouterr().out


class TestIndex:
    def test_builds_index_grouped_by_tag(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", title="Auth", tags=["auth"])
        write_article(root, "b", title="Billing", tags=["billing"])
        rendered = wiki.build_index(wiki.Wiki(root))
        assert "## auth" in rendered and "[[a]]" in rendered and "[[b]]" in rendered

    def test_untagged_articles_are_grouped(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        assert "## untagged" in wiki.build_index(wiki.Wiki(root))

    def test_excludes_drafts(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "d", subdir="drafts")
        assert "[[d]]" not in wiki.build_index(wiki.Wiki(root))

    def test_empty_wiki_renders_placeholder(self, tmp_path):
        root = make_wiki(tmp_path)
        assert "No articles yet" in wiki.build_index(wiki.Wiki(root))

    def test_rebuild_satisfies_the_missing_entry_check(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        wiki.main(["index", str(root)])
        assert findings_for(root, "missing-index-entry") == []

    def test_check_reports_stale_without_writing(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        write_index(root)
        before = (root / "_index.md").read_text(encoding="utf-8")
        assert wiki.main(["index", str(root), "--check"]) == 1
        assert "stale" in capsys.readouterr().out
        assert (root / "_index.md").read_text(encoding="utf-8") == before

    def test_check_passes_after_rebuild(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a")
        wiki.main(["index", str(root)])
        assert wiki.main(["index", str(root), "--check"]) == 0

    def test_index_is_idempotent(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", tags=["x"])
        wiki.main(["index", str(root)])
        first = (root / "_index.md").read_text(encoding="utf-8")
        wiki.main(["index", str(root)])
        assert (root / "_index.md").read_text(encoding="utf-8") == first


class TestPathCommand:
    def test_prints_resolved_root(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        assert wiki.main(["path", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == str(root)

    def test_explicit_path_suppresses_the_walk_up(self, tmp_path):
        """An explicit path names one base; falling back would use another."""
        make_wiki(tmp_path)
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert wiki.find_wiki(nested, explicit=str(nested / "absent")) is None

    def test_walk_up_stops_at_the_depth_limit(self, tmp_path):
        root = make_wiki(tmp_path)
        deep = tmp_path.joinpath(*[f"d{i}" for i in range(wiki.MAX_PARENT_DEPTH + 2)])
        deep.mkdir(parents=True)
        assert wiki.find_wiki(deep) is None
        assert wiki.find_wiki(deep.parent.parent) == root


class TestIndexWriteFailure:
    def test_unwritable_index_reports_instead_of_raising(self, tmp_path, capsys, monkeypatch):
        root = make_wiki(tmp_path)
        write_article(root, "a")

        def refuse(*_args, **_kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "write_text", refuse)
        assert wiki.main(["index", str(root)]) == 1
        assert "cannot write" in capsys.readouterr().err


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def _config(dirname: str) -> WorkbenchConfig:
    """A merged workbench config carrying just `wiki.dir`."""
    return WorkbenchConfig(wiki=WikiConfig(dir=dirname))


class TestInit:
    def test_creates_the_full_layout(self, tmp_path, capsys):
        assert wiki.main(["init", str(tmp_path)]) == 0
        root = tmp_path / "wiki"
        for name in ("raw", "articles", "drafts", "archive", "meta"):
            assert (root / name).is_dir(), name
        for name in ("SCHEMA.md", "_index.md", "_sources.md", "_log.md"):
            assert (root / name).is_file(), name
        assert "created" in capsys.readouterr().out

    def test_the_result_is_findable(self, tmp_path):
        """init and path must agree on what a knowledge base is."""
        wiki.main(["init", str(tmp_path)])
        assert wiki.find_wiki(tmp_path) == tmp_path / "wiki"

    def test_the_result_lints_clean(self, tmp_path):
        wiki.main(["init", str(tmp_path)])
        assert wiki.collect_lint(wiki.Wiki(tmp_path / "wiki")) == []

    def test_status_reports_an_empty_base(self, tmp_path):
        wiki.main(["init", str(tmp_path)])
        status = wiki.collect_status(wiki.Wiki(tmp_path / "wiki"))
        assert (status["articles"], status["sources"]) == (0, 0)

    def test_refuses_an_existing_base(self, tmp_path, capsys):
        wiki.main(["init", str(tmp_path)])
        assert wiki.main(["init", str(tmp_path)]) == 1
        assert "already exists" in capsys.readouterr().err

    def test_domain_reaches_the_schema(self, tmp_path):
        wiki.main(["init", str(tmp_path), "--domain", "Payments", "--audience", "the team"])
        schema = (tmp_path / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        assert "Payments" in schema and "the team" in schema
        assert "{DOMAIN}" not in schema and "{AUDIENCE}" not in schema

    def test_manifest_header_is_not_read_as_a_source(self, tmp_path):
        """The header init writes must not register as an entry."""
        wiki.main(["init", str(tmp_path)])
        assert wiki.Wiki(tmp_path / "wiki").recorded_source_hashes() == {}

    def test_explicit_path_is_honoured(self, tmp_path):
        target = tmp_path / "somewhere" / "kb"
        assert wiki.main(["init", str(tmp_path), "--wiki", str(target)]) == 0
        assert (target / "SCHEMA.md").is_file()

    def test_completes_a_half_created_base(self, tmp_path):
        """An interrupted run should finish, not fail or lose what it wrote."""
        root = tmp_path / "wiki"
        (root / "raw").mkdir(parents=True)
        (root / "_log.md").write_text("# Activity Log\n\nkept\n", encoding="utf-8")
        assert wiki.main(["init", str(tmp_path)]) == 0
        assert "kept" in (root / "_log.md").read_text(encoding="utf-8")
        assert (root / "SCHEMA.md").is_file()


class TestIngestStage:
    def _base(self, tmp_path: Path) -> Path:
        wiki.main(["init", str(tmp_path)])
        return tmp_path / "wiki"

    def test_stages_a_markdown_source(self, tmp_path, capsys):
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("# Notes\n\nbody\n", encoding="utf-8")
        assert wiki.main(["ingest", str(tmp_path), "--stage", str(src)]) == 0
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
        wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        store = wiki.Wiki(root)
        assert len(store.sources) == 1
        assert store.recorded_source_hashes() == {}

    def test_reported_hash_matches_the_file(self, tmp_path):
        """The manifest row must carry a computed hash, not a narrated one."""
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        staged = next((root / "raw").iterdir())
        row = wiki.manifest_row(root, staged, "file")
        assert wiki.hash_file(staged) in row
        assert wiki.Wiki(root).sources[0].content_hash == wiki.hash_file(staged)

    def test_no_content_hash_in_frontmatter(self, tmp_path):
        """One hash, computed on demand — a recorded copy would drift."""
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
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
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "pdf"])
        staged = next((root / "raw").iterdir())
        assert staged.read_bytes() == b"%PDF-1.4\nbinary\x00bytes"

    def test_same_name_twice_does_not_overwrite(self, tmp_path):
        root = self._base(tmp_path)
        for body in ("first\n", "second\n"):
            src = tmp_path / "README.md"
            src.write_text(body, encoding="utf-8")
            wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
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
        assert wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "../../evil"]) == 0
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
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "file\ninjected: yes"])
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
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", 'x"\ninjected: yes'])
        assert "injected" not in self._staged_keys(root)

    def test_a_quoted_title_stays_one_scalar(self, tmp_path):
        """An embedded quote must be escaped, not close the scalar early."""
        root = self._base(tmp_path)
        src = tmp_path / "s.md"
        src.write_text("body\n", encoding="utf-8")
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", 'a "quoted" name'])
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
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--type", "../../evil"])
        staged = next((root / "raw").iterdir())
        row = wiki.manifest_row(root, staged, "../../evil")
        assert ".." not in row

    def test_title_drives_the_filename(self, tmp_path):
        root = self._base(tmp_path)
        src = tmp_path / "x.md"
        src.write_text("body\n", encoding="utf-8")
        wiki.main(["ingest", str(tmp_path), "--stage", str(src), "--title", "How Auth Works"])
        assert (root / "raw" / "file-how-auth-works.md").is_file()

    def test_missing_source_reports_and_exits_one(self, tmp_path, capsys):
        self._base(tmp_path)
        assert wiki.main(["ingest", str(tmp_path), "--stage", str(tmp_path / "gone.md")]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_ingest_is_logged(self, tmp_path):
        root = self._base(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("body\n", encoding="utf-8")
        wiki.main(["ingest", str(tmp_path), "--stage", str(src)])
        assert "INGEST:" in (root / "_log.md").read_text(encoding="utf-8")


class TestSlugify:
    def test_lowercases_and_hyphenates(self):
        assert wiki.slugify_title("How Auth Works") == "how-auth-works"

    def test_drops_punctuation(self):
        assert wiki.slugify_title("What's a Token? (v2)") == "whats-a-token-v2"

    def test_truncates_at_a_word_boundary(self):
        """Every word is the same, so the last segment must be a whole one."""
        slug = wiki.slugify_title(" ".join(["alpha"] * 30))
        assert len(slug) <= 60
        assert slug.rsplit("-", 1)[-1] == "alpha"

    def test_a_single_long_word_is_cut_rather_than_emptied(self):
        slug = wiki.slugify_title("x" * 200)
        assert 0 < len(slug) <= 60

    def test_empty_title_still_yields_a_name(self):
        assert wiki.slugify_title("!!!") == "untitled"


class TestSchemaTemplateSubstitution:
    def test_no_placeholder_text_survives(self, tmp_path):
        """Instructions to the filler must not be filled in themselves.

        The template's opening line named both placeholders while telling the
        reader to replace them, so substitution produced 'Replace Payments and
        the team' in every new schema.
        """
        wiki.main(["init", str(tmp_path), "--domain", "Payments", "--audience", "the team"])
        body = (tmp_path / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        prose = [ln for ln in body.splitlines() if not ln.strip().startswith("<!--")]
        assert not any("Replace" in ln for ln in prose)
        assert "A knowledge base about Payments, for the team." in body


class TestDomainSkipsNonProse:
    def test_html_comment_is_not_the_domain(self, tmp_path):
        """The shipped template opens with an editing note in a comment."""
        root = make_wiki(tmp_path, "<!-- an editing note -->\n\n# Wiki Schema\n\nPayments.\n")
        assert wiki.Wiki(root).domain() == "Payments."

    def test_init_output_reports_the_real_domain(self, tmp_path):
        """End to end against the template init actually copies."""
        wiki.main(["init", str(tmp_path), "--domain", "Payments"])
        status = wiki.collect_status(wiki.Wiki(tmp_path / "wiki"))
        assert status["domain"] == "A knowledge base about Payments, for whoever works on it."
