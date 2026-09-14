"""Tests for matching review-comment text against the coding rules.

The property under test is the one that failed in practice: the score a rule
earns must not be a function of how much text the rule file holds. A scorer
that rewards vocabulary size annotates every finding with whichever file is
longest, and a matcher that always matches reports no gaps — which is the
entire output `retro-scan` exists to produce.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from retro.rules import (  # noqa: E402
    MIN_MATCH_SCORE,
    best_passage_score,
    build_rule,
    extract_keywords,
    find_nearest_rule,
    load_rules,
    score_rules,
    split_passages,
    term_weights,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# A numbered item whose text opens in bold: what a ladder step looks like in
# these files, and specific enough that prose quoting "1. " cannot match it.
NUMBERED_HEADING = re.compile(r"(?:^|\s)\d{1,2}[.)]\s\*\*")
ORDERED_ITEM = re.compile(r"^\d{1,2}[.)]\s")


def _rules():
    return load_rules(REPO_ROOT)


def _rule_texts() -> dict[str, str]:
    """Every rule file's raw text off disk, keyed by filename."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "ai" / "guidelines" / "rules").glob("*.md"))
    }


def _ordered_items(content: str) -> list[str]:
    """The numbered list items `content` states, one string each."""
    return [
        line.strip() for line in content.splitlines()
        if ORDERED_ITEM.match(line.strip())
    ]


def _vocab_rank(rules: list[dict]) -> list[str]:
    """Rule filenames, largest vocabulary first."""
    return [
        r["filename"]
        for r in sorted(rules, key=lambda r: len(r["keywords"]), reverse=True)
    ]


def _match_rank(comments: list[str], rules: list[dict]) -> list[str]:
    """Rule filenames, most matches first."""
    counts: dict[str, int] = {}
    for body in comments:
        nearest = find_nearest_rule(body, rules)
        if nearest:
            counts[nearest["filename"]] = counts.get(nearest["filename"], 0) + 1
    return [f for f, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


# Prose sharing no subject with any rule or any comment below, with a bigger
# vocabulary than the largest real rule file. Generated rather than copied from
# a sibling rule: padding one rule with another's text would give it that
# rule's subject for real, and a match on it would then be correct rather than
# the length artefact this exists to catch. The words are nonsense syllables so
# that no amount of it can coincide with a review comment.
_NONSENSE = [
    f"zib{a}{b}{c}qua"
    for c in "aeiouy"
    for b in "aeiouy"
    for a in "bcdfghjklmnpqrstvwxyz"
]
OFF_TOPIC_PADDING = "\n\n".join(
    "- " + " ".join(_NONSENSE[i:i + 25]) + "."
    for i in range(0, len(_NONSENSE), 25)
)


def _pad_rule(filename: str, padding: str = OFF_TOPIC_PADDING) -> list[dict]:
    """The rule set with one file grown by `padding`.

    The padded file ends up with the biggest vocabulary in the set while saying
    nothing it did not already say, so any match it gains it bought with length.
    Built through `build_rule`, the same constructor `load_rules` uses, so the
    grown file is a rule the loader could have produced rather than a hand-copy
    that stops simulating one the next time construction changes.
    """
    return [
        build_rule(rule["filename"], rule["content"] + "\n\n" + padding)
        if rule["filename"] == filename
        else rule
        for rule in _rules()
    ]


# A comment per topic, each written the way a reviewer writes one, paired with
# the rule file that actually covers it. Deliberately spread across rule files
# of very different sizes so a size-ranking scorer cannot score well by luck:
# none of the expected files is among the four that took 96% of matches in the
# field, and between them they cover both halves of the corpus by vocabulary
# size. Both properties are asserted below rather than stated here, because a
# ranking written into a comment goes stale the next time a rule file is
# edited and nothing re-reads it.
TOPICAL_COMMENTS = [
    (
        "The API token is hardcoded in the script. Read the secret from the "
        "environment instead of committing the credential to a tracked file.",
        "security-secrets.md",
    ),
    (
        "sed -i without an empty argument is the GNU spelling and fails on "
        "the BSD userland; quote the variable expansion too.",
        "bash.md",
    ),
    (
        "Do not summarise the changes at the end of the response; the diff "
        "already says what changed.",
        "output.md",
    ),
    (
        "This role duplicates the container port already declared in "
        "group_vars; reference the canonical variable instead.",
        "ansible.md",
    ),
    (
        "This skill's SKILL.md frontmatter has no trigger condition, so the "
        "agent never invokes the skill and reimplements the workflow by hand.",
        "skills.md",
    ),
    (
        "Re-run the self review because HEAD moved after the findings were "
        "fixed, so the review file names an earlier commit than the branch.",
        "self-review.md",
    ),
]


class TestSizeBias:
    """The score must not track the length of the rule file."""

    def test_match_distribution_is_not_rank_order_of_file_size(self):
        """The failure mode from the field: matches ranked by vocabulary size.

        Over a real scan the top two files took 73% of all matches in exactly
        the order of their keyword-set sizes. Asserting the two rankings
        differ is the cheapest direct statement of what went wrong.
        """
        rules = _rules()
        comments = [body for body, _ in TOPICAL_COMMENTS]

        matched = _match_rank(comments, rules)
        by_size = _vocab_rank(rules)

        assert matched, "the topical comments should match some rule"
        assert matched != by_size[: len(matched)]

    def test_the_four_largest_files_do_not_absorb_every_match(self):
        """No topical comment here is about any of them, so none should match.

        In the field these four took 96% of 1381 findings between them purely
        on vocabulary size, so naming them is a sharper assertion than one
        about the single largest file.
        """
        rules = _rules()
        biggest = set(_vocab_rank(rules)[:4])

        matched = _match_rank([body for body, _ in TOPICAL_COMMENTS], rules)

        assert matched
        assert not biggest.intersection(matched)

    def test_the_paired_rules_span_the_size_range_of_the_corpus(self):
        """What makes the pairings evidence rather than a lucky draw.

        A scorer that ranks by file size can be right about a comment paired
        with a big file by accident. These pairings are only a test of that if
        they reach both ends of the size ordering, so the spread is asserted
        from the corpus rather than described in a comment that cannot drift
        with it.
        """
        by_size = _vocab_rank(_rules())
        half = len(by_size) // 2
        ranks = {by_size.index(expected) for _, expected in TOPICAL_COMMENTS}

        assert any(r < half for r in ranks), "none is in the larger half"
        assert any(r >= half for r in ranks), "none is in the smaller half"

    def test_a_rule_is_reachable_from_outside_the_largest_files(self):
        """Every topical comment matches a file outside the top four by size."""
        rules = _rules()
        biggest = set(_vocab_rank(rules)[:4])
        for body, expected in TOPICAL_COMMENTS:
            assert expected not in biggest
            nearest = find_nearest_rule(body, rules)
            assert nearest is not None, body
            assert nearest["filename"] not in biggest

    def test_padding_a_rule_file_does_not_win_it_matches(self):
        """The causal statement of the same property.

        Growing a rule file with text that has nothing to do with the comment
        must not move the comment onto it. Under a raw-overlap score it does:
        the padded file clears the floor on almost any English sentence.
        """
        rules = _rules()
        comment = (
            "The API token is hardcoded in the script. Read the secret from "
            "the environment instead of committing the credential."
        )
        before = find_nearest_rule(comment, rules)
        assert before is not None
        assert before["filename"] == "security-secrets.md"

        padded = _pad_rule("output.md")
        grown = next(r for r in padded if r["filename"] == "output.md")
        assert len(grown["keywords"]) == max(
            len(r["keywords"]) for r in padded
        ), "the padded file should now hold the largest vocabulary in the set"

        after = find_nearest_rule(comment, padded)
        assert after is not None
        assert after["filename"] == "security-secrets.md"

    def test_absorbing_another_rules_text_does_not_steal_its_matches(self):
        """The sharpest form: pad one file with the *whole* of another.

        The padded file now contains every word of `bash.md` plus its own, so
        under a raw-overlap score its intersection with any bash comment is
        strictly larger than `bash.md`'s and it wins every one of them. The
        rule that actually states the thing must still be the answer, and the
        assertion is on the score rather than on which file is visited first,
        so a tie cannot be passed off as a win.
        """
        comment = (
            "sed -i without an empty argument is the GNU spelling and fails "
            "on the BSD userland; quote the variable expansion too."
        )
        source = next(r for r in _rules() if r["filename"] == "bash.md")
        padded = _pad_rule("output.md", source["content"])

        absorber = next(r for r in padded if r["filename"] == "output.md")
        assert source["keywords"] < absorber["keywords"], (
            "the padded file should be a strict superset of bash.md's vocabulary"
        )

        best = score_rules(comment, padded)
        assert best is not None
        assert best.rule["filename"] == "bash.md"

        weights = term_weights(padded)
        keywords = extract_keywords(comment)
        by_file = {
            r["filename"]: best_passage_score(keywords, r, weights) for r in padded
        }
        assert by_file["output.md"] <= by_file["bash.md"]

    def test_the_padded_file_takes_no_match_across_the_corpus(self):
        """The property stated over a corpus rather than one comment.

        Under the raw-overlap score, padding the smallest rule file this way
        took it from 0 matches to 74 over 1381 real review findings, on length
        alone. It must take none of the comments below, all of which are about
        a subject some other rule states and the padding does not mention.
        """
        padded = _pad_rule("output.md")
        # Every comment TOPICAL_COMMENTS pairs with a rule other than the one
        # being padded, so the padded file is the right answer to none of them.
        bodies = [
            body for body, expected in TOPICAL_COMMENTS if expected != "output.md"
        ] + [
            "Never run git push while on main; branch protection rejects it.",
            "The subagent's cwd does not persist, so qualify the path with git -C.",
            "Guard the grep with || true, since set -e exits on a non-match.",
        ]
        matched = _match_rank(bodies, padded)
        assert matched
        assert "output.md" not in matched


class TestTopicality:
    def test_each_topical_comment_matches_its_own_rule(self):
        rules = _rules()
        got = {
            expected: (find_nearest_rule(body, rules) or {}).get("filename")
            for body, expected in TOPICAL_COMMENTS
        }
        assert got == {expected: expected for _, expected in TOPICAL_COMMENTS}


class TestGaps:
    """A finding no rule covers has to come back as a gap."""

    def test_off_topic_comment_matches_no_rule(self):
        rules = _rules()
        comment = (
            "The seasonal migration of the arctic tern spans eleven thousand "
            "miles, longer than any other bird documented by ornithologists."
        )
        assert find_nearest_rule(comment, rules) is None

    def test_empty_comment_matches_no_rule(self):
        assert find_nearest_rule("", _rules()) is None

    def test_a_single_shared_term_is_not_a_match(self):
        """One word in common is a coincidence, not a subject in common."""
        rules = _rules()
        assert find_nearest_rule("Reticulating splines, worktree.", rules) is None

    def test_a_rule_file_stating_nothing_on_a_subject_reports_a_gap(self):
        """`docker.md` covers only compose syntax, so a build finding is a gap.

        The old scorer answered `ansible.md` here on incidental shared words.
        Reporting a gap is the correct answer and the one the retro acts on.
        """
        rules = _rules()
        comment = (
            "The Dockerfile base image is not pinned to a digest, so the "
            "container build is not reproducible between runs."
        )
        assert find_nearest_rule(comment, rules) is None

    def test_a_weak_best_match_is_reported_as_no_rule(self):
        """A rule is still nearest; the floor is what turns it into a gap."""
        rules = _rules()
        comment = (
            "The pagination page_size bound diverges from the offset "
            "convention used by the sibling endpoint handlers."
        )
        scored = score_rules(comment, rules)
        if scored is not None:
            assert scored.score < MIN_MATCH_SCORE
        assert find_nearest_rule(comment, rules) is None


class TestPassages:
    def test_bullets_table_rows_and_paragraphs_are_separate_passages(self):
        content = (
            "# Heading\n\nA paragraph about one thing,\ncontinued here.\n\n"
            "- first bullet\n- second bullet\n\n| a | b |\n"
        )
        assert split_passages(content) == [
            "A paragraph about one thing, continued here.",
            "- first bullet",
            "- second bullet",
            "| a | b |",
        ]

    def test_an_ordered_list_item_is_its_own_passage(self):
        """A numbered step states one rule, the same as a bullet does."""
        content = "1. first step\n2. second step\n3) third step\n"
        assert split_passages(content) == [
            "1. first step",
            "2. second step",
            "3) third step",
        ]

    def test_an_indented_ordered_item_is_its_own_passage(self):
        content = "1. outer step\n   2. nested step\n"
        assert split_passages(content) == ["1. outer step", "2. nested step"]

    def test_a_number_inside_prose_does_not_open_a_passage(self):
        """A decimal or a year opening a line is prose, not a list item."""
        content = (
            "1.5 seconds is the timeout,\n"
            "2026. was a typo nobody fixed,\n"
            "1234. neither is this.\n"
        )
        assert split_passages(content) == [
            "1.5 seconds is the timeout, 2026. was a typo nobody fixed, "
            "1234. neither is this."
        ]

    def test_no_passage_merges_two_ordered_list_items(self):
        """The blob problem at list scope: a ladder is not a single subject.

        `general.md`'s Planning ladder is four distinct rules, and merging
        them dilutes each one's weight while letting the merged passage match
        on the union of all four vocabularies — which is the size bias this
        module exists to remove, reintroduced inside one list.
        """
        blobs = [
            f"{name}: {passage[:70]}"
            for name, content in _rule_texts().items()
            for passage in split_passages(content)
            if len(NUMBERED_HEADING.findall(passage)) > 1
        ]
        assert not blobs

    def test_every_ordered_list_item_in_the_corpus_stands_alone(self):
        """Stated over the real rule files, item by item rather than in bulk."""
        texts = _rule_texts()
        merged = [
            f"{name}: {item[:70]}"
            for name, content in texts.items()
            for item in _ordered_items(content)
            if item not in split_passages(content)
        ]
        assert not merged
        assert sum(
            len(_ordered_items(c)) for c in texts.values()
        ), "the rule corpus should hold some ordered list items"

    def test_a_heading_closes_a_paragraph_without_joining_it(self):
        content = "first para\n## Heading\nsecond para\n"
        assert split_passages(content) == ["first para", "second para"]

    def test_frontmatter_is_not_a_passage(self):
        """Its path globs say where a rule applies, not what it requires."""
        content = '---\npaths:\n  - "**/*.go"\n---\n\n- the actual rule\n'
        assert split_passages(content) == ["- the actual rule"]

    def test_rules_carry_passages_and_none_is_a_whole_file(self):
        for rule in _rules():
            assert rule["passages"], rule["filename"]
            for passage in rule["passages"]:
                assert passage < rule["keywords"] or passage == rule["keywords"]
                assert len(passage) < len(rule["keywords"]) or len(rule["passages"]) == 1


class TestLoadRules:
    def test_missing_rules_directory_loads_nothing(self, tmp_path):
        assert load_rules(tmp_path) == []

    def test_every_rule_keeps_its_bullets_and_content(self):
        rules = _rules()
        assert rules
        for rule in rules:
            assert rule["filename"].endswith(".md")
            assert rule["content"]
            assert isinstance(rule["bullets"], list)
            assert isinstance(rule["keywords"], set)
