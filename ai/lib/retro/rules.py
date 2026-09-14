"""Matching review-comment text against the workbench's coding rules.

Loads each rule file under `ai/guidelines/rules/` into passages — its list
items, bulleted or numbered, its table rows and its paragraphs — and finds the
rule nearest a piece of comment text by the best passage either has in common.
`extract_keywords` is the vocabulary primitive both rule loading and bullet
matching are built on — `retro.report` reuses it to find which bullet inside a
matched rule is closest to the comment being annotated.

Scoring is deliberately per-passage, IDF-weighted and normalized, because the
question the retro asks is whether any rule *covers* a finding, not which rule
is least unlike it. An unnormalized count over a whole file answers the second
question: it grows with the file's vocabulary, so the longest file wins nearly
every comparison and no finding is ever reported as a gap.
"""

# doc-group: platform

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

RULES_REL = Path("ai") / "guidelines" / "rules"

STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "don", "should", "now", "and", "but", "or", "if", "this",
    "that", "it", "its", "they", "them", "their", "we", "us", "our",
    "you", "your", "any", "up", "what", "which", "who",
})

KEYWORD_PATTERN = re.compile(r"[a-z][a-z_-]{2,}")

# A passage opens on its own line: a list item — bulleted or numbered — or a
# table row. Anything else accumulates into the paragraph being read, and a
# heading closes one without joining it — a heading shares a comment's words
# too readily for how little it says, and the passage under it states the rule
# anyway.
#
# A numbered item counts because a ladder is a list of separate rules, not one
# rule in several parts: merging "1. Scope clear? … 4. Build" into a paragraph
# is the whole-file blob this module exists to break up, at list scope. The
# ordinal is capped at two digits and must be followed by a space, so a decimal
# (`1.5 seconds`) and a year (`2026. was`) opening a line stay prose.
PASSAGE_START = re.compile(r"^\s*(?:[-*]\s+|\d{1,2}[.)]\s+|\|)")
HEADING = re.compile(r"^#{1,6}\s")

# A rule file's frontmatter says which files the rule applies to, not what it
# requires. Its path globs are read as a bullet list otherwise, and a comment
# naming a file type matches the glob rather than any rule about that type.
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# A passage below this many keywords cannot carry a subject, and normalizing a
# two-word fragment produces a high score from one coincidence.
MIN_PASSAGE_KEYWORDS = 5

# How much of a passage's meaning a comment has to share before the rule is
# called its nearest. Below this the best match is the least-bad one rather
# than a rule about the same subject, and reporting it hides a genuine gap.
# Calibrated over 1410 real review findings: the median best score is 0.12 and
# the 90th percentile 0.19, so this admits roughly the top decile.
MIN_MATCH_SCORE = 0.18

# Terms a comment and a passage must literally share, independent of the score.
# Normalization alone lets two short texts score highly off a single uncommon
# word, which is a coincidence rather than a subject in common.
MIN_SHARED_TERMS = 3


# ── Keyword extraction ───────────────────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    """The lowercase, stop-word-free vocabulary `text` is made of."""
    words = set(KEYWORD_PATTERN.findall(text.lower()))
    return words - STOP_WORDS


# ── Rules loading and matching ───────────────────────────────────────────────

def split_passages(content: str) -> list[str]:
    """The self-contained statements `content` is made of.

    A rule file states one rule per list item or table row, with prose
    paragraphs between them, so those are the units that can be about a single
    subject. The whole file is not: it is every subject its section headings
    cover, and comparing against that union is what let file length decide the
    match. Neither is a whole numbered list — a ladder's steps are separate
    rules, and merging them recreates that union inside one passage.
    """
    passages: list[str] = []
    paragraph: list[str] = []
    content = FRONTMATTER.sub("", content)

    def flush() -> None:
        if paragraph:
            passages.append(" ".join(paragraph))
            paragraph.clear()

    for line in content.splitlines():
        stripped = line.strip()
        if PASSAGE_START.match(line):
            flush()
            passages.append(stripped)
        elif not stripped or HEADING.match(stripped):
            flush()
        else:
            paragraph.append(stripped)
    flush()
    return passages


def build_rule(filename: str, content: str) -> dict:
    """One rule file's text as the dict the scorer and the report read.

    The one place a rule dict is built, so a caller holding rule text that did
    not come off disk — a test fixture, or a file grown for comparison — gets
    the same vocabulary, bullets and passages `load_rules` would have given it
    rather than a hand-copy that drifts the next time this changes.
    """
    return {
        "filename": filename,
        "keywords": extract_keywords(content),
        "bullets": [
            line.strip().removeprefix("- ")
            for line in content.splitlines()
            if line.strip().startswith("- ")
        ],
        "passages": [
            kw for kw in map(extract_keywords, split_passages(content))
            if len(kw) >= MIN_PASSAGE_KEYWORDS
        ],
        "content": content,
    }


def load_rules(workbench: Path) -> list[dict]:
    rules_dir = workbench / RULES_REL
    if not rules_dir.exists():
        return []
    return [
        build_rule(f.name, f.read_text(encoding="utf-8"))
        for f in sorted(rules_dir.glob("*.md"))
    ]


# ── Scoring ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TermWeights:
    """How much each term across the rule set counts toward a match.

    A term in every rule file distinguishes nothing, so it is worth close to
    nothing; one in a single file is what actually identifies a subject. Named
    fields rather than a bare dict because `default` — the weight for a term no
    rule file uses at all — is part of the contract and not an entry in it.
    """

    weights: dict[str, float]
    default: float

    def of(self, terms: set[str]) -> float:
        """The combined weight of `terms`."""
        return sum(self.weights.get(t, self.default) for t in terms)


def term_weights(rules: list[dict]) -> TermWeights:
    """Inverse document frequency over the rule files, smoothed.

    A term unseen in any rule file is treated as maximally rare rather than as
    weightless: it comes from the comment, and a comment's own vocabulary is
    what the normalization below divides by.
    """
    total = len(rules)
    seen_in: dict[str, int] = {}
    for rule in rules:
        for term in rule["keywords"]:
            seen_in[term] = seen_in.get(term, 0) + 1
    return TermWeights(
        weights={
            t: math.log((total + 1) / (n + 1)) + 1.0 for t, n in seen_in.items()
        },
        default=math.log(total + 1) + 1.0,
    )


def passage_similarity(
    comment_keywords: set[str], passage: set[str], weights: TermWeights,
    shared: set[str] | None = None,
) -> float:
    """Weighted cosine similarity of a comment and a rule passage, in [0, 1].

    `shared` is the two sides' common terms where the caller has already had
    to compute them; it is derived here when omitted, so the call reads the
    same either way.

    Cosine rather than Jaccard: the two texts are of very different lengths —
    a review finding against a one-line rule — and Jaccard puts their union in
    the denominator, which penalizes that difference as if it were disagreement.
    Dividing by the geometric mean instead means neither side's length alone
    moves the score.
    """
    if shared is None:
        shared = comment_keywords & passage
    magnitude = math.sqrt(weights.of(comment_keywords) * weights.of(passage))
    if not magnitude:
        return 0.0
    return weights.of(shared) / magnitude


@dataclass(frozen=True)
class RuleMatch:
    """The rule nearest some comment text, and how near it actually is.

    `score` is what the caller needs to decide the match is worth reporting;
    `rule` alone cannot answer that, since the nearest rule exists for every
    comment and most comments have no rule about them.
    """

    rule: dict
    score: float


def best_passage_score(
    comment_keywords: set[str], rule: dict, weights: TermWeights,
) -> float:
    """How well `rule`'s closest passage matches `comment_keywords`.

    A passage sharing fewer than `MIN_SHARED_TERMS` with the comment scores
    zero however the normalization would rate it: two short texts can score
    highly off one uncommon word in common, which is a coincidence rather than
    a subject.
    """
    best = 0.0
    for passage in rule["passages"]:
        shared = comment_keywords & passage
        if len(shared) < MIN_SHARED_TERMS:
            continue
        score = passage_similarity(comment_keywords, passage, weights, shared)
        if score > best:
            best = score
    return best


def score_rules(
    comment_body: str, rules: list[dict], weights: TermWeights | None = None,
) -> RuleMatch | None:
    """The best-scoring rule for `comment_body`, before any quality floor.

    `weights` is the IDF over `rules`, derived here when the caller has none.
    It depends on the rule set alone, so a caller scoring many comments against
    one set computes it once with `term_weights` and passes it in rather than
    paying for the whole pass per comment.

    Published alongside `find_nearest_rule` so a caller tuning or reporting on
    match quality can see the score the decision was made on, rather than
    re-deriving it from a rule the floor already accepted.
    """
    comment_keywords = extract_keywords(comment_body)
    if not comment_keywords:
        return None
    if weights is None:
        weights = term_weights(rules)
    best: RuleMatch | None = None
    for rule in rules:
        score = best_passage_score(comment_keywords, rule, weights)
        if score and (best is None or score > best.score):
            best = RuleMatch(rule=rule, score=score)
    return best


def find_nearest_rule(
    comment_body: str, rules: list[dict], weights: TermWeights | None = None,
) -> dict | None:
    """The rule `comment_body` is about, or None when no rule covers it.

    `weights` is threaded straight to `score_rules` — a scan calls this once
    per finding, so the IDF pass belongs outside that loop.

    None is the answer the retro is actually after: a finding no rule claims is
    a gap in the rules, and it is the whole output of a scan. A scorer with no
    floor returns the least-bad rule for every comment and so reports no gaps
    at all, which is how this read as healthy while matching nothing topical.
    """
    best = score_rules(comment_body, rules, weights)
    if best is None or best.score < MIN_MATCH_SCORE:
        return None
    return best.rule
