# Wiki Schema

Replace {DOMAIN} and {AUDIENCE}. Everything below them is a working default — edit as the
wiki finds its shape.

## Identity

A knowledge base about {DOMAIN}, for {AUDIENCE}.

## Compilation Preferences

- Encyclopedic prose, not bullet lists, unless the content is genuinely a list
- Preserve specific numbers, dates, versions, examples, and identifiers
- Prefer depth: fewer rich articles over many thin ones
- But split distinct concepts apart — one source covering three separable topics is three
  cross-linked articles
- Synthesize across sources rather than concatenating them
- Attribute claims to their source
- Keep ticket, PR, and incident IDs from the source material

## Terminology

Use one term per concept, consistently. Where alternatives exist, prefer the one a reader
is most likely to search for. Record project-specific terms in `meta/glossary.md`.

## Article Structure

Required: **Summary** (1-2 paragraphs, appears in the index), **Details**,
**Relationships** (wikilinks), **Sources**.

Optional: **Open Questions**, **Examples**, **History**.

### Case studies

An article from a debugging or investigation session uses this shape instead:

**Summary** (what went wrong) · **Symptoms** (what was reported, including exact error
messages and identifiers) · **Investigation** (the steps taken and what each revealed) ·
**Root Cause** (the actual mechanism) · **Fix** (the exact change that resolved it) ·
**Prevention** (what stops it recurring) · **Relationships** · **Sources**

The investigation is the valuable part. Someone with the same symptoms should be able to
follow the path, not just read the conclusion.

## Tags

Freeform but reused consistently — check the existing set before coining a new one.
Five per article at most.

## Quality Settings

Read by the `wiki` CLI:

- min_article_words: 150
- staleness_threshold_days: 180

Read by the model during compile:

- auto_publish: true
- index_grouping_threshold: 15

## Source Authority

Most to least authoritative, for resolving contradictions:

1. The system itself — source code, configuration, observed behaviour
2. Official documentation and specifications
3. Internal documentation
4. Resolved tickets and postmortems
5. Articles and tutorials
6. Chat messages and forum answers
