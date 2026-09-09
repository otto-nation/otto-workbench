"""What a knowledge base reports about itself: status, findings, index."""

# doc-group: platform

from __future__ import annotations

from datetime import datetime, timezone

from .model import Article, Wiki
from .parsing import CONTRADICTION_RE, gap_tokens
from .paths import LOG_FILE

# Article pairs are reported from this Jaccard score up. Set below the ~80%
# the lint reference asks about, because the judgement being supported is
# "should these be one article", and a pair at 0.55 is worth a look even
# though it is plainly not a duplicate.
SIMILARITY_THRESHOLD = 0.55

# Shingle width for that score. Five-word runs are long enough that ordinary
# shared phrasing does not register and short enough to survive light editing.
SHINGLE_WORDS = 5

# Two gaps join a cluster when they share this fraction of their topic words.
GAP_CLUSTER_THRESHOLD = 0.5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def collect_status(wiki: Wiki) -> dict:
    published = wiki.published()
    recorded = wiki.recorded_source_hashes()
    uncompiled = [s for s in wiki.sources if recorded.get(s.rel) != s.content_hash]
    return {
        "path": str(wiki.root),
        "domain": wiki.domain(),
        "articles": len(published),
        "drafts": len(wiki.drafts()),
        "archived": sum(1 for a in wiki.articles if a.is_archived),
        "sources": len(wiki.sources),
        "uncompiled_sources": len(uncompiled),
        "unprocessed_log_entries": len(wiki.unprocessed_log_entries()),
        "last_compile": wiki.last_log_event("COMPILE"),
        "last_ingest": wiki.last_log_event("INGEST"),
    }


def collect_lint(wiki: Wiki) -> list[dict]:
    """Every mechanically decidable health finding, in stable order.

    Two checks from the original lint reference remain deliberately absent:
    judging whether two similar articles should be merged, and resolving
    (rather than finding) contradictions. Both need a judgement no rule here
    can make, so they belong to the skill reading this report. The evidence
    for the first is emitted by ``collect_signals`` rather than guessed at.

    Archived articles are targets but not subjects: a link *to* one resolves,
    while the article itself is not scanned. A retired article's thin body or
    unresolved contradiction is a record of what was once believed, not a
    defect anyone should be asked to fix.
    """
    findings: list[dict] = []
    published = wiki.published()
    live = [a for a in wiki.articles if not a.is_archived]
    known = {a.slug for a in wiki.articles}
    archived = {a.slug for a in wiki.articles if a.is_archived}

    def add(check: str, severity: str, message: str, where: str | None = None) -> None:
        findings.append({"check": check, "severity": severity, "message": message, "where": where})

    for article in live:
        for target in [t for t in dict.fromkeys(article.links) if t not in known]:
            add("broken-link", "error", f"link to missing article '{target}'", article.rel)

    for article in live:
        for target in [t for t in dict.fromkeys(article.links) if t in archived]:
            add(
                "archived-link",
                "warning",
                f"links to archived article '{target}'",
                article.rel,
            )

    indexed = wiki.index_targets()
    for article in published:
        if article.slug not in indexed:
            add("missing-index-entry", "error", "not listed in the master index", article.rel)

    inbound: dict[str, int] = {a.slug: 0 for a in wiki.articles}
    for article in live:
        for target in {t for t in article.links if t in inbound and t != article.slug}:
            inbound[target] += 1
    for article in published:
        if inbound.get(article.slug, 0) == 0:
            add("orphan-article", "warning", "no inbound links from other articles", article.rel)

    recorded = wiki.recorded_source_hashes()
    for source in wiki.sources:
        if source.rel not in recorded:
            add("orphan-source", "warning", "never compiled into an article", source.rel)
        elif recorded[source.rel] != source.content_hash:
            add("changed-source", "warning", "changed since it was last compiled", source.rel)

    threshold = wiki.settings["staleness_threshold_days"]
    now = _utc_now()
    for article in published:
        updated = article.updated
        if updated is None:
            continue
        age = (now - updated).days
        if age > threshold:
            add("stale-article", "warning", f"not updated in {age} days", article.rel)

    for article in live:
        if CONTRADICTION_RE.search(article.body):
            add("contradiction", "error", "carries an unresolved contradiction block", article.rel)

    minimum = wiki.settings["min_article_words"]
    for article in published:
        if article.word_count < minimum:
            add(
                "sparse-article",
                "warning",
                f"{article.word_count} words, below the {minimum}-word minimum",
                article.rel,
            )

    for article in published:
        related = article.related_section_links()
        if related is not None and not related:
            add("empty-related", "warning", "Related section has no links", article.rel)

    unprocessed = wiki.unprocessed_log_entries()
    if unprocessed:
        add(
            "unprocessed-log",
            "warning",
            f"{len(unprocessed)} observation or gap entries not yet acted on",
            LOG_FILE,
        )

    return findings


def _shingles(article: Article) -> frozenset[tuple[str, ...]]:
    """Overlapping word runs from *article*'s body, for a Jaccard comparison."""
    words = article.body.lower().split()
    if len(words) < SHINGLE_WORDS:
        return frozenset()
    return frozenset(
        tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)
    )


def _jaccard(left: frozenset, right: frozenset) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _similar_pairs(wiki: Wiki) -> list[dict]:
    """Published article pairs whose bodies overlap past the threshold.

    Shingled Jaccard over the raw body: no stemming, no weighting, no
    dependency. It answers "how much identical phrasing do these two share",
    which is the question behind the duplicate-article check — two articles
    compiled from overlapping sources repeat each other almost verbatim.
    It says nothing about two articles that cover one topic in different words,
    and is not meant to; that pair is the model's to notice while reading.

    ceiling: every pair compared, with every article's shingles held at once.
    Quadratic in article count and linear in body size, which is nothing at the
    tens-to-hundreds a hand-built wiki reaches. Upgrade trigger: if a wiki
    passes 1000 articles or this is wanted incrementally per compile, index the
    shingles and compare only candidates sharing one.
    """
    shingled = [(a, _shingles(a)) for a in wiki.published()]
    scored = (
        (left, right, _jaccard(left_shingles, right_shingles))
        for i, (left, left_shingles) in enumerate(shingled)
        for right, right_shingles in shingled[i + 1:]
    )
    pairs = [
        {"articles": [left.slug, right.slug], "similarity": round(score, 3)}
        for left, right, score in scored
        if score >= SIMILARITY_THRESHOLD
    ]
    return sorted(pairs, key=lambda p: (-p["similarity"], p["articles"]))


def _tag_table(wiki: Wiki) -> list[dict]:
    """Every tag in use with its count and the articles carrying it.

    Emitted rather than scored. The near-duplicate pairs the lint reference
    names — `auth`/`authentication`, `k8s`/`kubernetes` — score 0.44 and 0.31
    by character similarity, below unrelated pairs like `ci`/`cd` at 0.50, so a
    similarity number here would rank false positives above the real ones. What
    the model actually lacks is the complete tag set in one place, which is a
    thing a rule can produce and reading forty articles reliably does not.
    """
    counts: dict[str, list[str]] = {}
    for article in wiki.published():
        for tag in article.tags:
            counts.setdefault(tag, []).append(article.slug)
    return [
        {"tag": tag, "count": len(slugs), "articles": sorted(slugs)}
        for tag, slugs in sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


def _gap_clusters(wiki: Wiki) -> list[dict]:
    """Recorded query gaps grouped by shared topic words, largest group first.

    Single-link grouping: a gap joins a cluster when it overlaps any member
    past ``GAP_CLUSTER_THRESHOLD``. Cheap and order-dependent at the margins,
    which is why every question is carried through to the output — the count
    is the signal, the grouping is a convenience, and the model re-reads the
    questions before concluding anything from either.
    """
    gaps = [(date, question, gap_tokens(question)) for date, question in wiki.query_gaps()]
    clusters: list[dict] = []
    for date, question, tokens in gaps:
        entry = {"date": date, "question": question}
        joined = next(
            (c for c in clusters if _jaccard(frozenset(tokens), frozenset(c["_tokens"])) >= GAP_CLUSTER_THRESHOLD),
            None,
        )
        if joined is None:
            clusters.append({"_tokens": set(tokens), "topics": sorted(tokens), "gaps": [entry]})
            continue
        joined["_tokens"] |= tokens
        joined["topics"] = sorted(joined["_tokens"])
        joined["gaps"].append(entry)

    for cluster in clusters:
        del cluster["_tokens"]
        cluster["count"] = len(cluster["gaps"])
    return sorted(clusters, key=lambda c: (-c["count"], c["topics"]))


def _draft_ages(wiki: Wiki) -> list[dict]:
    """Drafts with their age in days, oldest first. Undated drafts sort last."""
    now = _utc_now()
    rows = []
    for draft in wiki.drafts():
        updated = draft.updated
        rows.append(
            {
                "slug": draft.slug,
                "origin": draft.frontmatter.get("origin", ""),
                "age_days": None if updated is None else (now - updated).days,
            }
        )
    return sorted(rows, key=lambda r: (r["age_days"] is None, -(r["age_days"] or 0), r["slug"]))


def collect_signals(wiki: Wiki) -> dict:
    """Counted evidence for the judgements `wiki lint` cannot make.

    Everything here is a measurement, not a conclusion: which tags exist and
    how often, which article bodies overlap and by how much, what the wiki was
    asked and could not answer, how long drafts have sat. The ranking and the
    decisions are the skill's, which is the same division `lint` already draws
    — the difference is that these four questions previously left the model
    counting for itself, and a model counting is a model guessing.

    Deliberately derived from the same `Wiki` accessors `collect_lint` and
    `collect_status` read, so there is one definition of what a draft, a
    published article, and a gap entry are.
    """
    return {
        "tags": _tag_table(wiki),
        "similar_articles": _similar_pairs(wiki),
        "gap_clusters": _gap_clusters(wiki),
        "drafts": _draft_ages(wiki),
    }


def build_index(wiki: Wiki) -> str:
    """Render the master index from article frontmatter."""
    lines = ["# Index", ""]
    articles = sorted(wiki.published(), key=lambda a: a.title.lower())
    if not articles:
        lines.append("_No articles yet._")
        return "\n".join(lines) + "\n"

    tagged: dict[str, list[Article]] = {}
    for article in articles:
        for tag in article.tags or ["untagged"]:
            tagged.setdefault(tag, []).append(article)

    for tag in sorted(tagged):
        lines.append(f"## {tag}")
        lines.append("")
        for article in tagged[tag]:
            summary = article.frontmatter.get("summary")
            suffix = f" — {summary}" if isinstance(summary, str) and summary else ""
            lines.append(f"- [[{article.slug}]]{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
