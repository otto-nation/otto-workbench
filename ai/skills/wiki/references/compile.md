# Compile

Turn new and changed raw sources into articles.

```
/wiki compile
/wiki compile --force    # every source, not just new and changed
/wiki compile --dry-run  # report what would happen, write nothing
```

## 1. Find the work

```bash
wiki sources --new
```

That is the whole of diff detection — it hashes every file in `raw/` and compares against
the manifest. Do not infer which sources are new from filenames or dates.

Also read `_log.md` for `SESSION_OBSERVATION` entries not yet processed; each is a
lightweight source. `wiki status` counts them.

Nothing new and no unprocessed observations: say so and stop.

## 2. Extract concepts

Per source, read it, read `_index.md` to see what already exists, and read `SCHEMA.md` for
compilation preferences and terminology. Then identify:

- **Key concepts** — the 3-10 main ideas. Each is a candidate article or article update.
- **Facts and claims** — specific assertions, with attribution. Numbers, dates, observed
  behaviour.
- **Existing matches** — which articles already cover this ground. For each: merge, or
  create new?
- **Contradictions** — anything conflicting with what the wiki already says.
- **Relationships** — how these concepts connect to existing articles.

Merge unless the concept is genuinely absent. Split a source across articles when it covers
separable topics: ask whether someone searching for just that sub-topic would want it
standing alone.

Preserve ticket, PR, and incident IDs in the prose or the Sources section — they are how a
reader gets back to the original context.

## 3. Draft or merge

New article:

```markdown
---
title: "{Concept Name}"
tags: [{tag}, {tag}]
sources: [{source-file}]
related: ["[[other-concept]]"]
confidence: {high|medium|low}
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
source_count: {N}
---

# {Concept Name}

{One paragraph. This is what appears in the index — it should tell a reader whether this
article answers their question.}

## Details

{Encyclopedic prose. Preserve specific numbers, dates, formulas, examples. Synthesize
across sources rather than summarizing each in turn.}

## Relationships

- Builds on [[foundation-concept]]
- Contrasts with [[alternative-approach]] on X

## Open Questions

- {Gaps and unresolved questions}

## Sources

- {source-file}: {what it contributed}
```

For a debugging or investigation source, use the case-study structure instead: Summary,
Symptoms, Investigation, Root Cause, Fix, Prevention, Relationships, Sources. The
investigative path is the point — someone hitting the same symptoms should be able to
follow it, not just read the answer.

Merging into an existing article: add facts to Details, relationships to Relationships,
questions to Open Questions, the source to both Sources and frontmatter. Update the summary
if the big picture moved. Increment `source_count`, update `updated`.

**Never delete during a merge.** If new information contradicts old, add a contradiction
block rather than overwriting:

```
> **[CONTRADICTION]** This article states {X}. However, [[other-article]]
> (sourced from {source}) states {Y}. The {newer/more authoritative} source
> suggests {which} is more likely correct.
```

Log it in `_log.md` and use the source authority hierarchy in `SCHEMA.md` to say which is
more likely right. `wiki lint` reports every unresolved block, so nothing gets lost.

Confidence: `high` for 3+ independent corroborating sources or one authoritative one;
`medium` for 1-2, or secondary sources; `low` for a single weak source or where the article
extrapolates past what the sources say.

## 4. Validate before writing

1. **Substantive** — does it teach anything past what the title implies?
2. **Not a duplicate** — does an existing article already cover most of this ground? Merge
   instead.
3. **Schema-compliant** — required sections present, tags from the existing taxonomy.
4. **Confidence justified** by the sources actually cited.

Failing an article: log `[{DATE}] REJECTED: "{title}" — {reason}` in `_log.md` and tell the
user. The source stays in `raw/` for reprocessing.

When `SCHEMA.md` sets `auto_publish: false`, write to `drafts/` instead of `articles/`.

## 5. Cross-link and record

Add wikilinks where an article mentions a concept that has its own article — first mention
per section, not every occurrence. **Only link to articles that exist.** A concept that
deserves an article but lacks one is plain text plus a gap entry in `_log.md`.

Check backlinks: if A now links to B, B's `related` frontmatter should include A.

Then:

```bash
wiki index    # regenerate _index.md from frontmatter
wiki lint     # must be clean before you are done
```

Update `_sources.md` for each processed source — content hash from `wiki sources`, the
articles it contributed to, status compiled. Append to `_log.md`:

```
[{DATE}] COMPILE: Processed {N} sources
  Created: {articles}
  Updated: {articles}
  Rejected: {with reasons}
  Contradictions: {if any}
```

Report created, updated, rejected, and contradiction counts, then the totals from
`wiki status`.
