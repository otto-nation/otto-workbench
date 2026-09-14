"""Tests for matching review-comment text against the coding rules.

The property under test is the one that failed in practice: the score a rule
earns must not be a function of how much text the rule file holds. A scorer
that rewards vocabulary size annotates every finding with whichever file is
longest, and a matcher that always matches reports no gaps — which is the
entire output `retro-scan` exists to produce.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from retro.rules import (  # noqa: E402
    MIN_MATCH_SCORE,
    MIN_PASSAGE_KEYWORDS,
    best_passage_score,
    extract_keywords,
    find_nearest_rule,
    load_rules,
    score_rules,
    split_passages,
    term_weights,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rules():
    return load_rules(REPO_ROOT)


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
    """
    rules = _rules()
    for rule in rules:
        if rule["filename"] != filename:
            continue
        rule["content"] = rule["content"] + "\n\n" + padding
        rule["keywords"] = extract_keywords(rule["content"])
        rule["bullets"] += [
            line.strip().removeprefix("- ")
            for line in padding.splitlines()
            if line.strip().startswith("- ")
        ]
        rule["passages"] = [
            kw
            for kw in map(extract_keywords, split_passages(rule["content"]))
            if len(kw) >= MIN_PASSAGE_KEYWORDS
        ]
    return rules


# A comment per topic, each written the way a reviewer writes one, paired with
# the rule file that actually covers it. Deliberately spread across rule files
# of very different sizes so a size-ranking scorer cannot score well by luck:
# the expected files rank 6th, 8th, 11th, 13th and 17th by vocabulary size, and
# not one of them is among the four that took 96% of matches in the field.
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
