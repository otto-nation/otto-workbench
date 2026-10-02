"""Tests for wiki signals: query gaps parsed from the log, and the signals report."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402

from wiki_support import make_wiki, write_article, write_log


class TestQueryGapParsing:
    def test_a_marker_without_its_colon_is_not_an_entry(self, tmp_path):
        """`wiki lint` counts unprocessed entries and `wiki signals` extracts
        questions from them; if one accepted a bare marker the other rejects,
        the two commands would report different numbers for the same log."""
        root = make_wiki(tmp_path)
        write_log(root, "- QUERY_GAP no colon here")
        assert cli.wiki.Wiki(root).unprocessed_log_entries() == []
        assert cli.wiki.Wiki(root).query_gaps() == []

    def test_prose_mentioning_a_marker_is_not_an_entry(self, tmp_path):
        root = make_wiki(tmp_path)
        write_log(root, "we should log a QUERY_GAP for this someday")
        assert cli.wiki.Wiki(root).unprocessed_log_entries() == []

    def test_dated_entries_are_recognised(self, tmp_path):
        """The documented format is date-prefixed, which the old anchor missed."""
        root = make_wiki(tmp_path)
        write_log(root, '[2026-01-05] QUERY_GAP: "how does token refresh work?"')
        assert cli.wiki.Wiki(root).unprocessed_log_entries() != []

    def test_gap_question_and_date_are_extracted(self, tmp_path):
        root = make_wiki(tmp_path)
        write_log(root, '[2026-01-05] QUERY_GAP: "how does token refresh work?"')
        assert cli.wiki.Wiki(root).query_gaps() == [
            cli.wiki.QueryGap(date="2026-01-05", question="how does token refresh work?")
        ]

    def test_undated_and_bulleted_entries_still_parse(self, tmp_path):
        root = make_wiki(tmp_path)
        write_log(root, "- QUERY_GAP: what signs a release?")
        assert cli.wiki.Wiki(root).query_gaps() == [
            cli.wiki.QueryGap(date="", question="what signs a release?")
        ]

    def test_other_log_lines_are_ignored(self, tmp_path):
        root = make_wiki(tmp_path)
        write_log(root, "[2026-01-05] COMPILE: Processed 2 sources")
        assert cli.wiki.Wiki(root).query_gaps() == []


class TestSignals:
    def test_tag_table_counts_every_tag(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body", tags=["auth", "api"])
        write_article(root, "b", body="body", tags=["authentication"])
        write_article(root, "c", body="body", tags=["auth"])
        table = cli.wiki.collect_signals(cli.wiki.Wiki(root))["tags"]
        assert [(r["tag"], r["count"]) for r in table] == [
            ("auth", 2),
            ("api", 1),
            ("authentication", 1),
        ]
        assert table[0]["articles"] == ["a", "c"]

    def test_tag_table_excludes_archived_articles(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "old", body="body", subdir="archive", tags=["retired"])
        assert cli.wiki.collect_signals(cli.wiki.Wiki(root))["tags"] == []

    def test_near_identical_articles_are_paired(self, tmp_path):
        root = make_wiki(tmp_path)
        shared = " ".join(f"word{i}" for i in range(60))
        write_article(root, "a", body=shared)
        write_article(root, "b", body=shared + " and one more clause here")
        pairs = cli.wiki.collect_signals(cli.wiki.Wiki(root))["similar_articles"]
        assert len(pairs) == 1
        assert pairs[0]["articles"] == ["a", "b"]
        assert pairs[0]["similarity"] > 0.8

    def test_unrelated_articles_are_not_paired(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "a", body=" ".join(f"alpha{i}" for i in range(60)))
        write_article(root, "b", body=" ".join(f"beta{i}" for i in range(60)))
        assert cli.wiki.collect_signals(cli.wiki.Wiki(root))["similar_articles"] == []

    def test_short_articles_do_not_pair_on_nothing(self, tmp_path):
        """Below one shingle there is no evidence, so emit none rather than 1.0."""
        root = make_wiki(tmp_path)
        write_article(root, "a", body="too short")
        write_article(root, "b", body="also short")
        assert cli.wiki.collect_signals(cli.wiki.Wiki(root))["similar_articles"] == []

    def test_gaps_on_one_topic_cluster_together(self, tmp_path):
        root = make_wiki(tmp_path)
        write_log(
            root,
            '[2026-01-05] QUERY_GAP: "how does token refresh work"',
            '[2026-01-06] QUERY_GAP: "does token refresh expire"',
            '[2026-01-07] QUERY_GAP: "which database stores invoices"',
        )
        clusters = cli.wiki.collect_signals(cli.wiki.Wiki(root))["gap_clusters"]
        assert [c["count"] for c in clusters] == [2, 1]
        assert "token" in clusters[0]["topics"]
        assert len(clusters[0]["gaps"]) == 2

    def test_every_gap_question_survives_clustering(self, tmp_path):
        """The grouping is a convenience; the questions are the evidence."""
        root = make_wiki(tmp_path)
        write_log(
            root,
            '[2026-01-05] QUERY_GAP: "alpha beta gamma"',
            '[2026-01-06] QUERY_GAP: "delta epsilon zeta"',
        )
        clusters = cli.wiki.collect_signals(cli.wiki.Wiki(root))["gap_clusters"]
        questions = {g["question"] for c in clusters for g in c["gaps"]}
        assert questions == {"alpha beta gamma", "delta epsilon zeta"}

    def test_draft_ages_are_reported_oldest_first(self, tmp_path):
        root = make_wiki(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        write_article(root, "stale-draft", body="body", subdir="drafts", updated=old,
                      origin="crystallized")
        write_article(root, "fresh-draft", body="body", subdir="drafts", updated=recent)
        drafts = cli.wiki.collect_signals(cli.wiki.Wiki(root))["drafts"]
        assert [d["slug"] for d in drafts] == ["stale-draft", "fresh-draft"]
        assert drafts[0]["age_days"] == 90
        assert drafts[0]["origin"] == "crystallized"

    def test_undated_drafts_sort_last_rather_than_crashing(self, tmp_path):
        root = make_wiki(tmp_path)
        write_article(root, "dated", body="body", subdir="drafts",
                      updated=(datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat())
        write_article(root, "undated", body="body", subdir="drafts")
        drafts = cli.wiki.collect_signals(cli.wiki.Wiki(root))["drafts"]
        assert [d["slug"] for d in drafts] == ["dated", "undated"]
        assert drafts[-1]["age_days"] is None


class TestSignalsCLI:
    def test_json_carries_all_four_signals(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body", tags=["auth"])
        assert cli.wiki.main(["signals", "--json", "--wiki", str(root)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["signals"]) == {"tags", "similar_articles", "gap_clusters", "drafts"}

    def test_signals_exit_zero_even_with_findings(self, tmp_path, capsys):
        """Signals are measurements, not defects, so they never fail a run."""
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body", tags=["auth"])
        write_article(root, "b", body="body", tags=["authentication"])
        assert cli.wiki.main(["signals", "--wiki", str(root)]) == 0

    def test_lint_json_omits_signals_by_default(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body")
        cli.wiki.main(["lint", "--json", "--wiki", str(root)])
        assert "signals" not in json.loads(capsys.readouterr().out)

    def test_lint_signals_flag_includes_them(self, tmp_path, capsys):
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body")
        cli.wiki.main(["lint", "--json", "--signals", "--wiki", str(root)])
        assert "signals" in json.loads(capsys.readouterr().out)

    def test_lint_signals_prints_the_tables_without_json(self, tmp_path, capsys):
        """The flag reports something in text mode too, rather than silently doing nothing."""
        root = make_wiki(tmp_path)
        write_article(root, "a", body="body", tags=["auth"])
        cli.wiki.main(["lint", "--signals", "--wiki", str(root)])
        out = capsys.readouterr().out
        assert "tags (1)" in out
        assert "query gap clusters" in out

    def test_lint_still_exits_on_findings_with_signals_asked_for(self, tmp_path, capsys):
        """Signals are additive; they must not mask a failing lint."""
        root = make_wiki(tmp_path)
        write_article(root, "a", body="see [[nowhere]]")
        assert cli.wiki.main(["lint", "--signals", "--wiki", str(root)]) == 1
