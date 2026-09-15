"""Tests for the snippet a retro report quotes beside a matched rule.

The property under test is the one that failed in practice: the snippet has to
be rule text. A rule file written in prose has no `- ` bullets at all, and the
old bullet scan fell through to the rule's filename for every finding that
matched one — a report row that names a rule and quotes nothing is the row a
reader cannot judge.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from retro.report import (  # noqa: E402
    MATCH_SNIPPET_MAX,
    _format_comment,
    best_matching_passage,
)
from retro.rules import (  # noqa: E402
    build_rule,
    find_nearest_rule,
    load_rules,
    term_weights,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two rule files that state every rule they have without a top-level `- `
# bullet, which is what made the old scan fall through to the filename. Named
# rather than discovered so the test says which files the defect was found on;
# `test_the_named_files_are_the_bulletless_ones` holds the naming honest.
PROSE_RULE_FILES = ("artifacts.md", "self-review.md")

# A comment per prose rule file, written the way a reviewer writes one, each
# about a subject only that file covers.
PROSE_COMMENTS = (
    (
        "Re-run the self review because HEAD moved after the findings were "
        "fixed, so the review file names an earlier commit than the branch.",
        "self-review.md",
    ),
    (
        "Read the project anatomy file before exploring the codebase rather "
        "than browsing blindly, and say if the generated index looks stale.",
        "artifacts.md",
    ),
)


def _rules():
    return load_rules(REPO_ROOT)


# One passage, short enough to quote whole, and over MIN_PASSAGE_KEYWORDS so
# the loader keeps it — a shorter line is dropped before any snippet is chosen.
SHORT_RULE = "- Quote every variable expansion in a shell script wrapper\n"


def _bullet_count(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip().startswith("- "))


class TestProseRuleFiles:
    """The zero-bullet files, which is why this defect existed at all."""

    def test_the_named_files_are_the_bulletless_ones(self):
        """The fixtures are the real trigger, not a hand-written stand-in."""
        bulletless = {
            r["filename"] for r in _rules() if not _bullet_count(r["content"])
        }
        assert bulletless == set(PROSE_RULE_FILES)

    def test_a_prose_rule_still_yields_a_text_snippet(self):
        """The defect itself: a filename where rule text should be."""
        rules = _rules()
        weights = term_weights(rules)
        for body, expected in PROSE_COMMENTS:
            rule = next(r for r in rules if r["filename"] == expected)
            snippet = best_matching_passage(body, rule, weights)
            assert snippet, expected
            assert snippet != expected
            assert not snippet.endswith(".md")
            assert len(snippet.split()) > 3, snippet

    def test_the_snippet_is_text_the_rule_file_actually_states(self):
        """Quoted text a reader can find on the page, not a paraphrase."""
        rules = _rules()
        weights = term_weights(rules)
        for body, expected in PROSE_COMMENTS:
            rule = next(r for r in rules if r["filename"] == expected)
            snippet = best_matching_passage(body, rule, weights).rstrip("…")
            words = snippet.split()
            # A passage joins the lines a wrapped item runs onto, so the whole
            # string is not in the file verbatim; its opening words are.
            assert " ".join(words[:6]) in " ".join(rule["content"].split())

    def test_each_prose_comment_reaches_its_prose_rule(self):
        """Without a match there is no snippet to get wrong."""
        rules = _rules()
        weights = term_weights(rules)
        for body, expected in PROSE_COMMENTS:
            nearest = find_nearest_rule(body, rules, weights)
            assert nearest is not None, body
            assert nearest["filename"] == expected


class TestSnippetIsWhatMatched:
    def test_the_snippet_comes_from_the_matched_rule(self):
        rules = _rules()
        weights = term_weights(rules)
        comment = (
            "The API token is hardcoded in the script. Read the secret from "
            "the environment instead of committing the credential."
        )
        nearest = find_nearest_rule(comment, rules, weights)
        assert nearest is not None
        snippet = best_matching_passage(comment, nearest, weights)
        assert "environment" in snippet.lower()

    def test_a_snippet_is_capped_and_marked_where_it_is_cut(self):
        """A passage is a whole list item, and the longest runs to 1466."""
        long_rule = build_rule(
            "long.md",
            "- " + " ".join(["rebase the worktree branch onto origin"] * 60) + "\n",
        )
        snippet = best_matching_passage(
            "Rebase the worktree branch onto origin before opening the PR",
            long_rule,
            term_weights([long_rule]),
        )
        assert len(snippet) == MATCH_SNIPPET_MAX
        assert snippet.endswith("…")

    def test_a_passage_within_the_cap_is_quoted_whole(self):
        rule = build_rule("short.md", SHORT_RULE)
        snippet = best_matching_passage(
            "Quote the variable expansion in this shell script wrapper",
            rule,
            term_weights([rule]),
        )
        assert snippet == SHORT_RULE.strip()
        assert len(snippet) < MATCH_SNIPPET_MAX

    def test_no_snippet_when_no_passage_shares_the_comments_subject(self):
        """An empty string, not a filename — the caller decides what to show."""
        rule = build_rule("short.md", SHORT_RULE)
        snippet = best_matching_passage(
            "The arctic tern migrates eleven thousand miles each season",
            rule,
            term_weights([rule]),
        )
        assert snippet == ""

    def test_every_rule_in_the_corpus_can_produce_a_snippet(self):
        """The old fallback's condition, stated over the real files.

        A rule that matched a finding always has a passage to quote, which is
        why no filename fallback survives in the renderer's snippet path.
        """
        rules = _rules()
        for rule in rules:
            assert rule["passages"], rule["filename"]
            longest = max(rule["passages"], key=lambda p: len(p.keywords))
            snippet = best_matching_passage(
                longest.text, rule, term_weights(rules),
            )
            assert snippet, rule["filename"]


class TestRenderedRow:
    def test_a_matched_comment_renders_its_snippet(self):
        lines = _format_comment({
            "author": "reviewer",
            "body": "Re-run the self review because HEAD moved",
            "nearest_rule": {
                "filename": "self-review.md",
                "match_snippet": "Re-running is cheap and finds real defects",
            },
        })
        assert any(
            'self-review.md ("Re-running is cheap and finds real defects")' in line
            for line in lines
        )

    def test_a_snippetless_match_renders_the_filename_alone(self):
        """Rather than a rule name beside an empty quotation."""
        lines = _format_comment({
            "author": "reviewer",
            "body": "some finding",
            "nearest_rule": {"filename": "self-review.md", "match_snippet": ""},
        })
        assert any(line.endswith("Nearest rule: self-review.md") for line in lines)
        assert not any('("")' in line for line in lines)
