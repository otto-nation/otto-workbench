# Lint

```bash
wiki lint           # nine mechanical checks
wiki lint --json    # the same, for filtering
```

Exit 0 is clean, 1 means findings. The CLI decides everything a rule can decide:
broken wikilinks, missing index entries, orphan articles, orphan and changed sources,
staleness against `staleness_threshold_days`, unresolved `[CONTRADICTION]` blocks,
articles under `min_article_words`, empty Related sections, and unprocessed log entries.

Report its output as given. Do not recount, re-derive, or estimate — the numbers are
already correct.

## Fixing what it finds

| Finding | Fix |
|---|---|
| `broken-link` | Remove the link, or write the missing article. Never leave a dead wikilink |
| `missing-index-entry` | `wiki index` |
| `orphan-article` | Link it from a related article, or archive it. An article nothing references is one nobody will find |
| `orphan-source` | Compile it, or explain in `_log.md` why it stays raw |
| `changed-source` | Recompile it — the article is behind its source |
| `stale-article` | Re-check against current sources, then update `updated:`. Do not just bump the date |
| `contradiction` | Resolve it — see below |
| `sparse-article` | Enrich it, or merge it into a fuller article |
| `empty-related` | Add the links, or remove the empty heading |
| `unprocessed-log` | Process the observations and gaps on the next compile |

## The three checks the CLI does not make

Each needs a judgement no rule can make. Do these by reading, after `wiki lint`.

**Contradiction resolution.** `wiki lint` finds every `[CONTRADICTION]` block; deciding
which side is right is yours. Use the source authority hierarchy in `SCHEMA.md`, prefer the
more recent source when authority is equal, and when it cannot be resolved, say so in the
block rather than deleting it. An unresolved contradiction that is *recorded* is a finding;
one that is quietly dropped is a wrong article.

**Tag near-duplicates.** `auth` and `authentication`, `k8s` and `kubernetes` split the same
group in two. Read the tag set out of `wiki lint --json`, pick the surviving spelling, and
update the articles. Consistency matters more than which spelling wins.

**Duplicate articles.** Two articles covering substantially the same ground should be one.
Merge into whichever is richer, keep every fact from both, redirect inbound links, and
archive the loser rather than deleting it.

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
