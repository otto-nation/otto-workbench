# Lint

```bash
wiki lint             # eleven mechanical checks
wiki lint --json      # the same, for filtering
wiki lint --signals   # findings plus the counted evidence below
```

Exit 0 is clean, 1 means findings. The CLI decides everything a rule can decide:
broken wikilinks, missing index entries, orphan articles, orphan and changed sources,
staleness against `staleness_threshold_days`, unresolved `[CONTRADICTION]` blocks,
articles under `min_article_words`, empty Related sections, links into `archive/`, and
unprocessed log entries.

Report its output as given. Do not recount, re-derive, or estimate — the numbers are
already correct.

## Fixing what it finds

| Finding | Fix |
|---|---|
| `broken-link` | Remove the link, or write the missing article. Never leave a dead wikilink |
| `missing-index-entry` | `wiki index` |
| `orphan-article` | Link it from a related article, or `wiki archive` it. An article nothing references is one nobody will find |
| `archived-link` | A live article points into `archive/`. Rewrite it to the successor article, or leave it as a deliberate tombstone |
| `orphan-source` | Compile it, or explain in `_log.md` why it stays raw |
| `changed-source` | Recompile it — the article is behind its source |
| `stale-article` | Re-check against current sources, then update `updated:`. Do not just bump the date |
| `contradiction` | Resolve it — see below |
| `sparse-article` | Enrich it, or merge it into a fuller article |
| `empty-related` | Add the links, or remove the empty heading |
| `unprocessed-log` | Process the observations and gaps on the next compile |

## The three judgements the CLI does not make

Each needs a judgement no rule can make. Do these by reading, after `wiki lint` — but run
`wiki signals` first, which counts the evidence for two of the three so you are weighing
numbers rather than impressions.

**Contradiction resolution.** `wiki lint` finds every `[CONTRADICTION]` block; deciding
which side is right is yours. Use the source authority hierarchy in `SCHEMA.md`, prefer the
more recent source when authority is equal, and when it cannot be resolved, say so in the
block rather than deleting it. An unresolved contradiction that is *recorded* is a finding;
one that is quietly dropped is a wrong article. No signal helps here — it is judgement all
the way down.

**Tag near-duplicates.** `auth` and `authentication`, `k8s` and `kubernetes` split the same
group in two. The `tags` table in `wiki signals` is the complete tag set with counts and the
articles carrying each — read it there rather than assembling it from the articles. Pick the
surviving spelling, usually the commoner one, and update the articles. Consistency matters
more than which spelling wins.

There is deliberately no similarity score on tags: the pairs worth catching score *below*
unrelated ones on any string metric, so a number would rank the false positives first.
The judgement is yours; the table only makes sure you see every tag.

**Duplicate articles.** `similar_articles` in `wiki signals` scores every published pair by
shared five-word runs. A pair above 0.8 is near-verbatim and almost certainly wants merging;
0.55 to 0.8 is worth opening both. The score measures repeated phrasing, so two articles
covering one topic in different words score low — that pair is still yours to notice while
reading, and a low score is not evidence of difference.

Merge into whichever is richer, keep every fact from both, redirect inbound links, and
`wiki archive` the loser rather than deleting it.

## Report

```
{N} error(s), {M} warning(s)
  {check}: {count}
  ...
Fixed: {what you fixed}
Needs a decision: {what you did not}
```

Ask before acting on anything that removes content: merging articles, archiving orphans,
deleting drafts. Additive fixes — rebuilding the index, adding a missing link — need no
confirmation.
