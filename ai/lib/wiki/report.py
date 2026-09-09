"""What a knowledge base reports about itself: status, findings, index."""

# doc-group: platform

from __future__ import annotations

import re
from datetime import datetime, timezone

from .model import Article, Wiki
from .parsing import _CONTRADICTION_RE
from .paths import LOG_FILE


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

    Three checks from the original lint reference are deliberately absent: tag
    near-duplicates, duplicate articles at ~80% overlap, and resolving (rather
    than finding) contradictions. Each needs a judgement no rule here can make,
    so they belong to the skill reading this report.
    """
    findings: list[dict] = []
    published = wiki.published()
    known = {a.slug for a in wiki.articles}

    def add(check: str, severity: str, message: str, where: str | None = None) -> None:
        findings.append({"check": check, "severity": severity, "message": message, "where": where})

    for article in wiki.articles:
        for target in [t for t in dict.fromkeys(article.links) if t not in known]:
            add("broken-link", "error", f"link to missing article '{target}'", article.rel)

    indexed = wiki.index_targets()
    for article in published:
        if article.slug not in indexed:
            add("missing-index-entry", "error", "not listed in the master index", article.rel)

    inbound: dict[str, int] = {a.slug: 0 for a in wiki.articles}
    for article in wiki.articles:
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

    for article in wiki.articles:
        if _CONTRADICTION_RE.search(article.body):
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
