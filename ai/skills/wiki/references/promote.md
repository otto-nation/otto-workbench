# Promote

Move reviewed drafts into the wiki. `wiki status` counts them.

```
/wiki promote            # review all drafts
/wiki promote "{title}"  # one draft
```

## 1. List

Read `drafts/`. For each: title, origin (crystallized from a query, compiled from a source,
or written by hand), created date, confidence, one-line summary.

```
Drafts ({count}):

1. "{title}" — crystallized from "{question}" ({date})
   Confidence: medium | {summary}

2. "{title}" — compiled from {source} ({date})
   Confidence: high | {summary}

Review which? [1/2/all/skip]
```

## 2. Review

Show the full draft, then offer: promote as-is, edit first, delete, or skip.

## 3. Promote

1. Move the file from `drafts/` to `articles/`.
2. Strip the draft-only frontmatter: `origin`, `crystallized_from`, `crystallized_query`.
3. Add wikilinks to and from related articles. Only to articles that exist.
4. `wiki index` to pick it up.
5. `wiki lint` — a promoted draft must leave the wiki clean.
6. Log `[{DATE}] PROMOTED: "{title}"`.

A draft that fails lint on promotion is not ready: fix the finding or put it back.

## Deleting

Deleting a draft discards work. Confirm first, and prefer archiving anything that took real
effort — `archive/` keeps it readable and out of the way.
