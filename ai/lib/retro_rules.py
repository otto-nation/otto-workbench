"""Matching review-comment text against the workbench's coding rules.

Loads each rule file under `ai/guidelines/rules/` into a keyword set and finds
the rule nearest a piece of comment text by keyword overlap. `extract_keywords`
is the vocabulary primitive both rule loading and bullet matching are built
on — `retro_report` reuses it to find which bullet inside a matched rule is
closest to the comment being annotated.
"""

# doc-group: platform

from __future__ import annotations

import re
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

MIN_KEYWORD_OVERLAP = 2


# ── Keyword extraction ───────────────────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    """The lowercase, stop-word-free vocabulary `text` is made of."""
    words = set(KEYWORD_PATTERN.findall(text.lower()))
    return words - STOP_WORDS


# ── Rules loading and matching ───────────────────────────────────────────────

def load_rules(workbench: Path) -> list[dict]:
    rules_dir = workbench / RULES_REL
    if not rules_dir.exists():
        return []
    results = []
    for f in sorted(rules_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        keywords = extract_keywords(content)
        bullets = [
            line.strip().removeprefix("- ")
            for line in content.splitlines()
            if line.strip().startswith("- ")
        ]
        results.append({
            "filename": f.name,
            "keywords": keywords,
            "bullets": bullets,
            "content": content,
        })
    return results


def find_nearest_rule(comment_body: str, rules: list[dict]) -> dict | None:
    comment_keywords = extract_keywords(comment_body)
    if not comment_keywords:
        return None
    best_match = None
    best_score = 0
    for rule in rules:
        overlap = len(comment_keywords & rule["keywords"])
        if overlap > best_score and overlap >= MIN_KEYWORD_OVERLAP:
            best_score = overlap
            best_match = rule
    return best_match
